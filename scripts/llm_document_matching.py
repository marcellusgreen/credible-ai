#!/usr/bin/env python3
"""
LLM-based document matching for debt instruments.

Uses DeepSeek (cheap) to match unlinked debt instruments to their governing
legal documents using natural language understanding instead of regex patterns.

Usage:
    # Test on a single company
    python scripts/llm_document_matching.py --ticker GOOGL --dry-run

    # Run on all unmatched companies
    python scripts/llm_document_matching.py --all --dry-run

    # Save matches to database
    python scripts/llm_document_matching.py --ticker GOOGL --save
"""

import argparse
import asyncio
import io
import json
import os
import sys
from decimal import Decimal
from typing import Optional

# Handle Windows encoding
if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

import httpx
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import async_session_maker
from app.models import Company, DebtInstrument, DocumentSection, DebtInstrumentDocument


DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
DEEPSEEK_BASE_URL = "https://api.deepseek.com/v1"

# Limit concurrent API calls to avoid rate limiting
MAX_CONCURRENT_CALLS = 5
api_semaphore = asyncio.Semaphore(MAX_CONCURRENT_CALLS)


async def call_deepseek(prompt: str, max_tokens: int = 500) -> Optional[str]:
    """Call DeepSeek API with a prompt, with concurrency limiting."""
    if not DEEPSEEK_API_KEY:
        raise ValueError("DEEPSEEK_API_KEY not set")

    async with api_semaphore:  # Limit concurrent calls
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(
                f"{DEEPSEEK_BASE_URL}/chat/completions",
                headers={
                    "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": "deepseek-chat",
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": max_tokens,
                    "temperature": 0.1,  # Low temperature for consistent matching
                },
            )
            response.raise_for_status()
            data = response.json()
            return data["choices"][0]["message"]["content"]


def convert_rate_to_percent(rate_value) -> str:
    """Convert rate from basis points (stored as integer) to percent string."""
    if rate_value is None:
        return None
    try:
        # Rate is stored in basis points (e.g., 175 = 1.75%)
        rate_float = float(rate_value) / 100
        # Format without trailing zeros: 1.75, 5.5, 3.0
        formatted = f"{rate_float:.3f}".rstrip('0').rstrip('.')
        return formatted
    except (ValueError, TypeError):
        return None


def extract_relevant_snippet(content: str, instrument: dict, max_length: int = 4000) -> str:
    """Extract the most relevant snippet from document content for the instrument."""
    import re

    if not content:
        return "No content"

    # Try to find sections mentioning the instrument's rate or year
    rate = instrument.get('coupon', 'N/A')
    maturity = instrument.get('maturity', 'N/A')

    # Extract rate value - handle both "1.75%" format and basis points
    rate_val = None
    if rate and rate != 'N/A':
        rate_match = re.search(r'([\d.]+)%', str(rate))
        if rate_match:
            rate_val = rate_match.group(1)
        else:
            # Try converting from basis points
            rate_val = convert_rate_to_percent(rate)

    # Extract year (e.g., "2031-01-15" -> "2031")
    year_match = re.search(r'(\d{4})', str(maturity))
    year_val = year_match.group(1) if year_match else None

    # If we have both rate and year, try to find content mentioning both
    if rate_val and year_val:
        # Search for section containing both
        pattern = rf'.{{0,500}}{re.escape(rate_val)}.{{0,2000}}{year_val}.{{0,500}}|.{{0,500}}{year_val}.{{0,2000}}{re.escape(rate_val)}.{{0,500}}'
        matches = list(re.finditer(pattern, content, re.IGNORECASE | re.DOTALL))
        if matches:
            # Return the longest match
            best = max(matches, key=lambda m: len(m.group()))
            return best.group()[:max_length]

    # If we have just rate, find section with it
    if rate_val:
        pattern = rf'.{{0,1000}}{re.escape(rate_val)}.{{0,1000}}'
        match = re.search(pattern, content, re.IGNORECASE)
        if match:
            return match.group()[:max_length]

    # If we have just year, find section with it
    if year_val:
        pattern = rf'.{{0,1000}}{year_val}\s+Notes.{{0,1000}}'
        match = re.search(pattern, content, re.IGNORECASE)
        if match:
            return match.group()[:max_length]

    # Fallback to first N chars
    return content[:max_length]


def filter_relevant_documents(documents: list[dict], instrument: dict) -> list[dict]:
    """Filter documents to only those that might contain the instrument."""
    import re

    if not documents:
        return []

    rate = instrument.get('coupon', 'N/A')
    maturity = instrument.get('maturity', 'N/A')
    inst_name = instrument.get('name', '')
    inst_type = instrument.get('type', '').lower()

    # Extract rate value - handle both "1.75%" format and basis points
    rate_val = None
    if rate and rate != 'N/A':
        rate_match = re.search(r'([\d.]+)%', str(rate))
        if rate_match:
            rate_val = rate_match.group(1)
        else:
            rate_val = convert_rate_to_percent(rate)

    # Extract year
    year_match = re.search(r'(\d{4})', str(maturity))
    year_val = year_match.group(1) if year_match else None

    # Check if this is a credit facility
    is_loan = any(kw in inst_type for kw in ['loan', 'revolver', 'credit', 'abl', 'facility'])

    relevant = []
    for doc in documents:
        content = doc.get('content', '') or ''
        title = doc.get('title', '') or ''
        doc_type = doc.get('type', '').lower()

        # Skip "Description of Securities" documents (not debt indentures)
        if 'DESCRIPTION OF EACH REGISTRANT' in content[:1000]:
            continue
        if 'DESCRIPTION OF SECURITIES' in content[:1000]:
            continue

        # For credit facilities, include ALL credit agreements (don't filter by content)
        if is_loan and doc_type == 'credit_agreement':
            relevant.append(doc)
            continue

        # Check if document contains rate or year
        has_rate = rate_val and rate_val in content
        has_year = year_val and year_val in content

        # For notes/bonds with rate AND year, require both to match
        if rate_val and year_val:
            if has_rate or has_year:
                relevant.append(doc)
        elif rate_val:
            if has_rate:
                relevant.append(doc)
        elif year_val:
            if has_year:
                relevant.append(doc)
        else:
            # No rate or year info - include recent documents
            relevant.append(doc)

    # Return up to 10 most relevant documents, prioritizing most recent
    return sorted(relevant, key=lambda d: d.get('filing_date') or '', reverse=True)[:10]


def build_matching_prompt(instrument: dict, documents: list[dict]) -> str:
    """Build a prompt to match an instrument to documents."""

    instrument_desc = f"""
DEBT INSTRUMENT TO MATCH:
- Name: {instrument['name']}
- Type: {instrument['type']}
- Coupon Rate: {instrument['coupon']}
- Maturity Date: {instrument['maturity']}
- Principal/Outstanding: {instrument['amount']}
- Issuer: {instrument['issuer']}
"""

    docs_desc = "CANDIDATE DOCUMENTS:\n"
    for i, doc in enumerate(documents, 1):
        # Extract relevant snippet instead of just first N chars
        content_preview = extract_relevant_snippet(doc['content'], instrument, max_length=4000)
        docs_desc += f"""
--- Document {i} ---
Title: {doc['title']}
Type: {doc['type']}
Filing Date: {doc['filing_date']}
Relevant Content: {content_preview}

"""

    prompt = f"""{instrument_desc}

{docs_desc}

TASK: Determine which document(s), if any, govern this debt instrument.

For BONDS/NOTES: Look for indentures that mention the same coupon rate AND maturity year. The document should explicitly reference this specific note series.
For LOANS/REVOLVERS: Look for credit agreements that mention the facility type (revolving, term loan) and are from a similar time period.

Respond with JSON only:
{{
    "matches": [
        {{
            "document_index": 1,
            "confidence": 0.85,
            "reasoning": "Brief explanation of why this document governs this instrument"
        }}
    ],
    "no_match_reason": "Explanation if no matches found"
}}

Rules:
- Only match if there's clear evidence (same rate AND year for bonds, or same facility type for loans)
- Confidence >= 0.7 for strong matches (exact rate and year)
- Confidence 0.5-0.7 for probable matches (year matches, rate close)
- Don't include matches below 0.5 confidence"""

    return prompt


def parse_llm_response(response: str) -> dict:
    """Parse the LLM's JSON response."""
    try:
        # Try to extract JSON from response
        start = response.find('{')
        end = response.rfind('}') + 1
        if start >= 0 and end > start:
            json_str = response[start:end]
            return json.loads(json_str)
    except json.JSONDecodeError:
        pass

    return {"matches": [], "no_match_reason": "Failed to parse LLM response"}


async def get_unlinked_instruments(session: AsyncSession, company_id) -> list:
    """Get instruments without any document links."""
    result = await session.execute(text("""
        SELECT di.id, di.name, di.instrument_type, di.interest_rate,
               di.maturity_date, di.outstanding, di.principal,
               e.name as issuer_name
        FROM debt_instruments di
        LEFT JOIN entities e ON di.issuer_id = e.id
        WHERE di.company_id = :company_id
          AND di.is_active = true
          AND di.id NOT IN (SELECT debt_instrument_id FROM debt_instrument_documents)
    """), {"company_id": company_id})
    return result.fetchall()


async def get_documents(session: AsyncSession, company_id, doc_type: str) -> list:
    """Get documents of a specific type for a company."""
    result = await session.execute(text("""
        SELECT id, section_title, section_type, filing_date, content
        FROM document_sections
        WHERE company_id = :company_id
          AND section_type = :doc_type
        ORDER BY filing_date DESC
    """), {"company_id": company_id, "doc_type": doc_type})
    return result.fetchall()


async def match_instrument_with_llm(
    instrument: dict,
    documents: list[dict],
) -> list[dict]:
    """Use LLM to match an instrument to documents."""
    if not documents:
        return []

    prompt = build_matching_prompt(instrument, documents)

    try:
        response = await call_deepseek(prompt)
        result = parse_llm_response(response)

        matches = []
        for match in result.get("matches", []):
            doc_idx = match.get("document_index", 0) - 1  # Convert to 0-indexed
            if 0 <= doc_idx < len(documents):
                matches.append({
                    "document_id": documents[doc_idx]["id"],
                    "confidence": match.get("confidence", 0.5),
                    "reasoning": match.get("reasoning", ""),
                    "match_method": "llm_deepseek",
                })

        return matches
    except Exception as e:
        print(f"    LLM error: {e}")
        return []


async def fetch_company_data(ticker: str) -> Optional[dict]:
    """Fetch all needed data for a company, then close the session."""
    async with async_session_maker() as session:
        # Get company
        result = await session.execute(
            select(Company).where(Company.ticker == ticker.upper())
        )
        company = result.scalar_one_or_none()
        if not company:
            return None

        # Get unlinked instruments
        instruments = await get_unlinked_instruments(session, company.id)

        # Get documents
        indentures = await get_documents(session, company.id, "indenture")
        credit_agreements = await get_documents(session, company.id, "credit_agreement")

        # Convert to plain dicts to avoid detached instance issues
        return {
            "company_id": company.id,
            "ticker": ticker,
            "instruments": [
                {
                    "id": inst.id,
                    "name": inst.name,
                    "instrument_type": inst.instrument_type,
                    "interest_rate": inst.interest_rate,
                    "maturity_date": inst.maturity_date,
                    "outstanding": inst.outstanding,
                    "principal": inst.principal,
                    "issuer_name": inst.issuer_name,
                }
                for inst in instruments
            ],
            "indentures": [
                {
                    "id": doc.id,
                    "section_title": doc.section_title,
                    "section_type": doc.section_type,
                    "filing_date": doc.filing_date,
                    "content": doc.content,
                }
                for doc in indentures
            ],
            "credit_agreements": [
                {
                    "id": doc.id,
                    "section_title": doc.section_title,
                    "section_type": doc.section_type,
                    "filing_date": doc.filing_date,
                    "content": doc.content,
                }
                for doc in credit_agreements
            ],
        }


async def save_links(links_to_create: list) -> int:
    """Save links to database with a fresh session."""
    if not links_to_create:
        return 0

    saved = 0
    async with async_session_maker() as session:
        for link in links_to_create:
            try:
                new_link = DebtInstrumentDocument(
                    debt_instrument_id=link["instrument_id"],
                    document_section_id=link["document_id"],
                    relationship_type="governs",
                    match_confidence=Decimal(str(round(link["confidence"], 3))),
                    match_method=link["method"],
                    match_evidence=link["evidence"],
                    is_verified=False,
                    created_by="llm_deepseek",
                )
                session.add(new_link)
                saved += 1
            except Exception as e:
                print(f"    Error saving link: {e}")

        await session.commit()
    return saved


async def process_company(
    session: AsyncSession,  # kept for compatibility but not used
    ticker: str,
    dry_run: bool = True,
    save: bool = False,
) -> dict:
    """Process all unlinked instruments for a company."""

    print(f"\n{'='*60}")
    print(f"Processing {ticker}")
    print(f"{'='*60}")

    # Fetch all data with a short-lived session
    data = await fetch_company_data(ticker)
    if not data:
        return {"error": f"Company not found: {ticker}"}

    instruments = data["instruments"]
    if not instruments:
        print(f"  No unlinked instruments")
        return {"ticker": ticker, "matched": 0, "unmatched": 0}

    print(f"  Found {len(instruments)} unlinked instruments")

    indentures = data["indentures"]
    credit_agreements = data["credit_agreements"]

    print(f"  Available: {len(indentures)} indentures, {len(credit_agreements)} credit agreements")

    # Classify instruments and match
    bond_types = {"notes", "bonds", "senior_notes", "senior_secured_notes",
                  "senior_unsecured_notes", "subordinated_notes", "convertible_notes", "debentures"}
    loan_types = {"revolver", "term_loan", "term_loan_a", "term_loan_b", "abl", "credit_facility"}

    # Prepare all matching tasks
    async def match_single_instrument(inst: dict) -> tuple[dict, list[dict]]:
        """Match a single instrument and return (instrument, matches)."""
        inst_type = (inst["instrument_type"] or "").lower()

        # Determine document type to search
        if inst_type in bond_types or "note" in inst_type:
            docs = indentures
        elif inst_type in loan_types or "loan" in inst_type or "revolver" in inst_type:
            docs = credit_agreements
        else:
            docs = indentures + credit_agreements

        if not docs:
            return (inst, [])

        # Format instrument for LLM
        instrument_dict = {
            "id": str(inst["id"]),
            "name": inst["name"] or "Unknown",
            "type": inst["instrument_type"] or "Unknown",
            "coupon": f"{inst['interest_rate']/100:.3f}%" if inst["interest_rate"] else "N/A",
            "maturity": str(inst["maturity_date"]) if inst["maturity_date"] else "N/A",
            "amount": f"${inst['outstanding']/100:,.0f}" if inst["outstanding"] else (
                f"${inst['principal']/100:,.0f}" if inst["principal"] else "N/A"
            ),
            "issuer": inst["issuer_name"] or "Parent company",
        }

        # Convert all docs to dict format
        all_docs_dict = [
            {
                "id": str(doc["id"]),
                "title": doc["section_title"] or "Untitled",
                "type": doc["section_type"],
                "filing_date": str(doc["filing_date"]) if doc["filing_date"] else "Unknown",
                "content": doc["content"] or "",
            }
            for doc in docs
        ]

        # Filter to only documents that might be relevant (contain rate/year/keywords)
        relevant_docs = filter_relevant_documents(all_docs_dict, instrument_dict)

        if not relevant_docs:
            # Fallback to 8 most recent if no relevant found
            relevant_docs = all_docs_dict[:8]
            if relevant_docs:
                print(f"    [Note: Using {len(relevant_docs)} most recent docs - no specific matches found]")

        matches = await match_instrument_with_llm(instrument_dict, relevant_docs)
        return (inst, matches)

    # Run all LLM calls in parallel (with semaphore limiting concurrency)
    print(f"\n  Running {len(instruments)} LLM matches in parallel (max {MAX_CONCURRENT_CALLS} concurrent)...")
    results = await asyncio.gather(*[match_single_instrument(inst) for inst in instruments])

    # Process results
    total_matched = 0
    total_unmatched = 0
    links_to_create = []

    for inst, matches in results:
        inst_name = inst["name"] or "Unknown"
        if matches:
            print(f"  ✓ {inst_name[:50]}: {len(matches)} match(es)")
            for match in matches:
                links_to_create.append({
                    "instrument_id": inst["id"],
                    "document_id": match["document_id"],
                    "confidence": match["confidence"],
                    "method": match["match_method"],
                    "evidence": {"reasoning": match["reasoning"]},
                })
            total_matched += 1
        else:
            print(f"  ✗ {inst_name[:50]}: no match")
            total_unmatched += 1

    # Save to database with a fresh session
    if save and links_to_create:
        print(f"\n  Saving {len(links_to_create)} links to database...")
        saved = await save_links(links_to_create)
        print(f"  Saved {saved} links")

    return {
        "ticker": ticker,
        "matched": total_matched,
        "unmatched": total_unmatched,
        "links_created": len(links_to_create) if save else 0,
    }


async def main():
    parser = argparse.ArgumentParser(description="LLM-based document matching")
    parser.add_argument("--ticker", help="Single company ticker")
    parser.add_argument("--all", action="store_true", help="Process all companies with zero match")
    parser.add_argument("--dry-run", action="store_true", help="Don't save to database")
    parser.add_argument("--save", action="store_true", help="Save matches to database")
    parser.add_argument("--limit", type=int, default=5, help="Max companies to process (with --all)")
    args = parser.parse_args()

    if not args.ticker and not args.all:
        parser.error("Either --ticker or --all is required")

    if not DEEPSEEK_API_KEY:
        print("ERROR: DEEPSEEK_API_KEY not set in environment")
        sys.exit(1)

    # Single ticker mode - use one session
    if args.ticker:
        async with async_session_maker() as session:
            result = await process_company(session, args.ticker, dry_run=not args.save, save=args.save)
            print(f"\nResult: {result}")
        return

    # Batch mode - get list of tickers first, then process each with fresh session
    async with async_session_maker() as session:
        # Get companies with 0% match but have documents
        zero_match = await session.execute(text("""
            SELECT c.ticker, COUNT(DISTINCT di.id) as inst_count
            FROM companies c
            JOIN debt_instruments di ON di.company_id = c.id AND di.is_active = true
            LEFT JOIN debt_instrument_documents did ON did.debt_instrument_id = di.id
            WHERE did.id IS NULL
              AND EXISTS (
                  SELECT 1 FROM document_sections ds
                  WHERE ds.company_id = c.id
                    AND ds.section_type IN ('indenture', 'credit_agreement')
              )
            GROUP BY c.ticker
            HAVING COUNT(DISTINCT di.id) = COUNT(CASE WHEN did.id IS NULL THEN 1 END)
            ORDER BY inst_count DESC
            LIMIT :limit
        """), {"limit": args.limit})

        tickers = [row[0] for row in zero_match.fetchall()]
        print(f"Processing {len(tickers)} companies with 0% match: {tickers}")

    # Process each company with a fresh session to avoid connection timeouts
    results = []
    for ticker in tickers:
        async with async_session_maker() as company_session:
            try:
                result = await process_company(company_session, ticker, dry_run=not args.save, save=args.save)
                results.append(result)
            except Exception as e:
                print(f"Error processing {ticker}: {e}")
                results.append({"ticker": ticker, "error": str(e), "matched": 0, "unmatched": 0})

    print("\n" + "="*60)
    print("SUMMARY")
    print("="*60)
    total_matched = sum(r.get("matched", 0) for r in results)
    total_unmatched = sum(r.get("unmatched", 0) for r in results)
    print(f"Total matched: {total_matched}")
    print(f"Total unmatched: {total_unmatched}")


if __name__ == "__main__":
    asyncio.run(main())
