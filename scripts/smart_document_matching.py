#!/usr/bin/env python3
"""
Smart document matching for debt instruments.

Uses pattern matching to find relevant document sections, then uses LLM
for confirmation. More effective than pure LLM matching for large documents.

Usage:
    python scripts/smart_document_matching.py --ticker CHTR --dry-run
    python scripts/smart_document_matching.py --ticker CHTR --save
    python scripts/smart_document_matching.py --all --save --limit 10
"""

import argparse
import asyncio
import io
import json
import os
import re
import sys
from decimal import Decimal
from typing import Optional

if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

import httpx
from sqlalchemy import text
from app.core.database import async_session_maker
from app.models import DebtInstrumentDocument


DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
DEEPSEEK_BASE_URL = "https://api.deepseek.com/v1"

MAX_CONCURRENT_CALLS = 5
api_semaphore = asyncio.Semaphore(MAX_CONCURRENT_CALLS)


def extract_rate_maturity(instrument: dict) -> tuple[Optional[str], Optional[str]]:
    """Extract rate and maturity year patterns from instrument."""
    rate_pattern = None
    maturity_pattern = None

    # Extract rate (e.g., "5.25%" -> "5.25" or "5 1/4")
    if instrument.get('interest_rate'):
        rate = instrument['interest_rate'] / 100  # Convert from bps
        # Create patterns for both decimal and fraction formats
        rate_pattern = f"{rate:.2f}".rstrip('0').rstrip('.')
        # Also handle fractions like 5 1/4 for 5.25

    # Extract maturity year
    if instrument.get('maturity_date'):
        maturity_str = str(instrument['maturity_date'])
        if len(maturity_str) >= 4:
            maturity_pattern = maturity_str[:4]  # Just the year

    return rate_pattern, maturity_pattern


def find_instrument_in_document(instrument: dict, doc_content: str) -> list[dict]:
    """Search document for mentions of the instrument."""
    matches = []

    name = instrument.get('name', '')
    inst_type = (instrument.get('instrument_type') or '').lower()
    rate_pattern, maturity_year = extract_rate_maturity(instrument)

    # For term loans, search for the specific loan name
    if 'term' in inst_type or 'loan' in inst_type or 'revolver' in inst_type:
        # Extract key identifiers from name (e.g., "Term A-6", "Term B-3", "Revolving Loan C")
        loan_patterns = []

        if name:
            # Try to find the loan identifier
            term_match = re.search(r'Term\s*[AB][-\s]?\d+', name, re.IGNORECASE)
            if term_match:
                loan_patterns.append(term_match.group())

            revolving_match = re.search(r'Revolving\s*(?:Loan\s*)?[A-Z]?\d*', name, re.IGNORECASE)
            if revolving_match:
                loan_patterns.append(revolving_match.group())

            # Also try the full name
            if name and len(name) > 3:
                loan_patterns.append(name)

        for pattern in loan_patterns:
            if pattern and re.search(re.escape(pattern), doc_content, re.IGNORECASE):
                # Find context around the match
                match_pos = doc_content.lower().find(pattern.lower())
                if match_pos >= 0:
                    start = max(0, match_pos - 200)
                    end = min(len(doc_content), match_pos + len(pattern) + 500)
                    context = doc_content[start:end]
                    matches.append({
                        'type': 'name_match',
                        'pattern': pattern,
                        'context': context,
                        'confidence': 0.85
                    })

    # For bonds/notes, search for rate + maturity combination
    else:
        if rate_pattern and maturity_year:
            # Search for rate near maturity year
            # Pattern: look for rate% ... 20XX or "due 20XX" near rate
            rate_escaped = re.escape(rate_pattern)

            # Try various patterns
            patterns_to_try = [
                rf'{rate_escaped}%?\s*[^%]*?(?:due|matur)[^%]*?{maturity_year}',
                rf'{rate_escaped}%?\s*(?:Senior\s*)?Notes?\s*(?:due\s*)?{maturity_year}',
                rf'{maturity_year}[^%]*?{rate_escaped}%',
            ]

            for pattern in patterns_to_try:
                found = re.search(pattern, doc_content, re.IGNORECASE)
                if found:
                    match_pos = found.start()
                    start = max(0, match_pos - 100)
                    end = min(len(doc_content), found.end() + 300)
                    context = doc_content[start:end]
                    matches.append({
                        'type': 'rate_maturity_match',
                        'pattern': f'{rate_pattern}% due {maturity_year}',
                        'context': context,
                        'confidence': 0.80
                    })
                    break

        # Also try just the name if it's descriptive
        if name and len(name) > 10 and 'note' in name.lower():
            # Extract key parts of the name
            if re.search(re.escape(name[:30]), doc_content, re.IGNORECASE):
                match_pos = doc_content.lower().find(name[:30].lower())
                if match_pos >= 0:
                    start = max(0, match_pos - 100)
                    end = min(len(doc_content), match_pos + 500)
                    context = doc_content[start:end]
                    matches.append({
                        'type': 'name_match',
                        'pattern': name[:30],
                        'context': context,
                        'confidence': 0.75
                    })

    return matches


async def call_deepseek_confirm(instrument: dict, doc_title: str, context: str) -> bool:
    """Use LLM to confirm a potential match."""
    if not DEEPSEEK_API_KEY:
        return True  # Skip confirmation if no API key

    prompt = f"""Confirm if this document excerpt governs this debt instrument.

INSTRUMENT:
- Name: {instrument.get('name', 'Unknown')}
- Type: {instrument.get('instrument_type', 'Unknown')}
- Rate: {instrument.get('interest_rate', 'N/A')}
- Maturity: {instrument.get('maturity_date', 'N/A')}

DOCUMENT: {doc_title}
EXCERPT:
{context[:1500]}

Does this document excerpt clearly relate to the debt instrument above?
Reply with just "YES" or "NO"."""

    async with api_semaphore:
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    f"{DEEPSEEK_BASE_URL}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": "deepseek-chat",
                        "messages": [{"role": "user", "content": prompt}],
                        "max_tokens": 10,
                        "temperature": 0.1,
                    },
                )
                response.raise_for_status()
                data = response.json()
                answer = data["choices"][0]["message"]["content"].strip().upper()
                return "YES" in answer
        except Exception as e:
            print(f"    LLM confirm error: {e}")
            return True  # Assume match if LLM fails


async def process_company(ticker: str, dry_run: bool = True) -> dict:
    """Process all unlinked instruments for a company."""

    print(f"\n{'='*60}")
    print(f"Processing {ticker}")
    print(f"{'='*60}")

    async with async_session_maker() as session:
        # Get company
        result = await session.execute(text('''
            SELECT id FROM companies WHERE ticker = :ticker
        '''), {'ticker': ticker})
        company_row = result.fetchone()
        if not company_row:
            return {"error": f"Company not found: {ticker}"}
        company_id = company_row[0]

        # Get unlinked instruments
        result = await session.execute(text('''
            SELECT di.id, di.name, di.instrument_type, di.interest_rate,
                   di.maturity_date, di.outstanding, di.principal
            FROM debt_instruments di
            WHERE di.company_id = :cid
              AND di.is_active = true
              AND di.id NOT IN (SELECT DISTINCT debt_instrument_id FROM debt_instrument_documents)
              AND (di.attributes IS NULL OR di.attributes->>:key IS NULL OR di.attributes->>:key != :val)
            ORDER BY di.maturity_date
        '''), {'cid': company_id, 'key': 'no_document_expected', 'val': 'true'})
        instruments = [dict(row._mapping) for row in result.fetchall()]

        if not instruments:
            print(f"  No unlinked instruments")
            return {"ticker": ticker, "matched": 0, "unmatched": 0}

        print(f"  Found {len(instruments)} unlinked instruments")

        # Get all documents (both indentures and credit agreements)
        result = await session.execute(text('''
            SELECT id, section_title, section_type, content, filing_date
            FROM document_sections
            WHERE company_id = :cid
              AND section_type IN ('indenture', 'credit_agreement')
            ORDER BY filing_date DESC
        '''), {'cid': company_id})
        documents = [dict(row._mapping) for row in result.fetchall()]

        print(f"  Available: {len([d for d in documents if d['section_type'] == 'indenture'])} indentures, "
              f"{len([d for d in documents if d['section_type'] == 'credit_agreement'])} credit agreements")

        # Match each instrument
        total_matched = 0
        total_unmatched = 0
        links_to_create = []

        for inst in instruments:
            inst_name = inst['name'] or 'Unknown'
            inst_type = (inst['instrument_type'] or '').lower()

            # Determine which documents to search
            if 'term' in inst_type or 'loan' in inst_type or 'revolver' in inst_type or 'credit' in inst_type:
                docs_to_search = [d for d in documents if d['section_type'] == 'credit_agreement']
            elif 'note' in inst_type or 'bond' in inst_type or 'debenture' in inst_type:
                docs_to_search = [d for d in documents if d['section_type'] == 'indenture']
            else:
                docs_to_search = documents

            best_match = None
            best_confidence = 0

            for doc in docs_to_search:
                if not doc['content']:
                    continue

                matches = find_instrument_in_document(inst, doc['content'])

                for match in matches:
                    if match['confidence'] > best_confidence:
                        # Optionally confirm with LLM for lower confidence matches
                        if match['confidence'] < 0.80 and DEEPSEEK_API_KEY:
                            confirmed = await call_deepseek_confirm(
                                inst, doc['section_title'], match['context']
                            )
                            if not confirmed:
                                continue

                        best_match = {
                            'document_id': doc['id'],
                            'document_title': doc['section_title'],
                            'match_type': match['type'],
                            'pattern': match['pattern'],
                            'confidence': match['confidence']
                        }
                        best_confidence = match['confidence']

            if best_match:
                print(f"  ✓ {inst_name[:50]}: matched to {best_match['document_title'][:40]}... "
                      f"(conf: {best_match['confidence']:.2f}, {best_match['match_type']})")
                links_to_create.append({
                    'instrument_id': inst['id'],
                    'document_id': best_match['document_id'],
                    'confidence': best_match['confidence'],
                    'method': f"smart_{best_match['match_type']}",
                    'evidence': {'pattern': best_match['pattern']}
                })
                total_matched += 1
            else:
                print(f"  ✗ {inst_name[:50]}: no match")
                total_unmatched += 1

        # Save links
        if not dry_run and links_to_create:
            print(f"\n  Saving {len(links_to_create)} links...")
            saved = 0
            for link in links_to_create:
                try:
                    new_link = DebtInstrumentDocument(
                        debt_instrument_id=link['instrument_id'],
                        document_section_id=link['document_id'],
                        relationship_type='governs',
                        match_confidence=Decimal(str(round(link['confidence'], 3))),
                        match_method=link['method'],
                        match_evidence=link['evidence'],
                        is_verified=False,
                        created_by='smart_matching',
                    )
                    session.add(new_link)
                    saved += 1
                except Exception as e:
                    print(f"    Error: {e}")

            await session.commit()
            print(f"  Saved {saved} links")

        return {
            'ticker': ticker,
            'matched': total_matched,
            'unmatched': total_unmatched,
            'links_created': len(links_to_create) if not dry_run else 0
        }


async def main():
    parser = argparse.ArgumentParser(description="Smart document matching")
    parser.add_argument("--ticker", help="Single company ticker")
    parser.add_argument("--all", action="store_true", help="Process all companies with unlinked instruments")
    parser.add_argument("--dry-run", action="store_true", help="Don't save to database")
    parser.add_argument("--save", action="store_true", help="Save matches to database")
    parser.add_argument("--limit", type=int, default=10, help="Max companies (with --all)")
    args = parser.parse_args()

    if not args.ticker and not args.all:
        parser.error("Either --ticker or --all is required")

    dry_run = not args.save

    if args.ticker:
        result = await process_company(args.ticker.upper(), dry_run=dry_run)
        print(f"\nResult: {result}")
        return

    # Batch mode
    async with async_session_maker() as session:
        result = await session.execute(text('''
            SELECT c.ticker, COUNT(*) as unlinked
            FROM debt_instruments di
            JOIN companies c ON di.company_id = c.id
            WHERE di.is_active = true
              AND di.id NOT IN (SELECT DISTINCT debt_instrument_id FROM debt_instrument_documents)
              AND (di.attributes IS NULL OR di.attributes->>:key IS NULL OR di.attributes->>:key != :val)
            GROUP BY c.ticker
            ORDER BY unlinked DESC
            LIMIT :limit
        '''), {'key': 'no_document_expected', 'val': 'true', 'limit': args.limit})
        tickers = [row[0] for row in result.fetchall()]

    print(f"Processing {len(tickers)} companies: {tickers}")

    results = []
    for ticker in tickers:
        try:
            result = await process_company(ticker, dry_run=dry_run)
            results.append(result)
        except Exception as e:
            print(f"Error processing {ticker}: {e}")
            results.append({"ticker": ticker, "error": str(e)})

    print("\n" + "="*60)
    print("SUMMARY")
    print("="*60)
    total_matched = sum(r.get('matched', 0) for r in results)
    total_unmatched = sum(r.get('unmatched', 0) for r in results)
    print(f"Total matched: {total_matched}")
    print(f"Total unmatched: {total_unmatched}")


if __name__ == "__main__":
    asyncio.run(main())
