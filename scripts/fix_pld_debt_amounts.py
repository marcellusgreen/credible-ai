#!/usr/bin/env python3
"""
Fix PLD (Prologis) debt instrument amounts.

The stored debt_footnote sections for PLD are broken — they contain the entire
filing (truncated at 100K chars) starting from the table of contents, never
reaching the actual debt note. This script:

1. Fetches PLD's latest 10-K directly from SEC via SecApiClient
2. Finds the debt note section using keyword search (bypassing broken regex)
3. Sends instrument list + clean debt note to Gemini 2.5 Pro
4. Updates the DB with matched amounts

Usage:
    python scripts/fix_pld_debt_amounts.py --dry-run    # Preview
    python scripts/fix_pld_debt_amounts.py              # Apply changes
"""
import argparse
import asyncio
import os
import re
import sys
import time
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv
load_dotenv()

from sqlalchemy import select, or_
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from app.models import Company, DebtInstrument
from app.services.utils import parse_json_robust

# Reuse the prompt and Gemini interface from backfill_amounts_from_docs
from scripts.backfill_amounts_from_docs import (
    TARGETED_EXTRACTION_PROMPT,
    get_gemini_response,
    format_instrument_list,
)


def extract_debt_note_from_filing(content: str) -> str | None:
    """Find and extract the debt note section from a full 10-K/10-Q filing.

    Searches for common debt note headers that appear after the financial
    statements, then captures content until the next note header.
    """
    # Patterns for finding the debt note section header
    # PLD uses "Note X – Debt" style headers
    debt_note_patterns = [
        # "Note 5 – Debt" or "Note 5 - Debt" or "Note 5. Debt"
        r'(Note\s*\d+\s*[\.\-\u2014\u2013]\s*(?:Debt|Long[\-\s]?Term\s+Debt|Borrowings|'
        r'Senior\s+Notes|Notes\s+Payable|Unsecured\s+Senior\s+Notes))',
        # Numbered without "Note" prefix: "5. Debt"
        r'(\d+\.\s+(?:Debt|Long[\-\s]?Term\s+Debt|Borrowings))',
        # All caps variant
        r'(NOTE\s*\d+\s*[\.\-\u2014\u2013]\s*(?:DEBT|LONG[\-\s]?TERM\s+DEBT|BORROWINGS))',
    ]

    best_match = None
    best_pos = len(content)

    for pattern in debt_note_patterns:
        # Search from position 10000+ to skip past financial statement headers
        for m in re.finditer(pattern, content):
            if m.start() > 5000 and m.start() < best_pos:
                best_match = m
                best_pos = m.start()

    if not best_match:
        return None

    # Find the end of this note (next "Note X" header)
    start = best_match.start()
    end_patterns = [
        r'\nNote\s*\d+\s*[\.\-\u2014\u2013]',
        r'\nNOTE\s*\d+\s*[\.\-\u2014\u2013]',
        r'\n\d+\.\s+[A-Z][a-z]',  # Next numbered section
    ]

    end_pos = len(content)
    for ep in end_patterns:
        for em in re.finditer(ep, content[start + 100:]):
            candidate = start + 100 + em.start()
            if candidate < end_pos:
                end_pos = candidate
                break

    section = content[start:end_pos]

    # Sanity check: should be at least 500 chars and contain debt keywords
    if len(section) < 500:
        return None

    lower = section.lower()
    debt_keywords = ['senior notes', 'due 20', '% notes', 'aggregate principal',
                     'debt consisted', 'unsecured notes', 'term loan', 'credit facility',
                     'maturity', 'interest rate']
    matches = sum(1 for kw in debt_keywords if kw in lower)
    if matches < 2:
        return None

    # Truncate if too long (shouldn't be for a single note)
    if len(section) > 50000:
        section = section[:50000] + "\n... [truncated]"

    return section


async def main():
    parser = argparse.ArgumentParser(description='Fix PLD debt instrument amounts')
    parser.add_argument('--dry-run', action='store_true',
                        help='Preview changes without applying')
    parser.add_argument('--model', type=str, default='gemini-2.5-pro',
                        help='Gemini model (default: gemini-2.5-pro)')
    args = parser.parse_args()

    database_url = os.getenv('DATABASE_URL')
    sec_api_key = os.getenv('SEC_API_KEY')
    gemini_key = os.getenv('GEMINI_API_KEY')

    if not database_url:
        print('Error: DATABASE_URL required')
        sys.exit(1)
    if not sec_api_key:
        print('Error: SEC_API_KEY required')
        sys.exit(1)
    if not gemini_key:
        print('Error: GEMINI_API_KEY required')
        sys.exit(1)

    engine = create_async_engine(database_url, echo=False, pool_pre_ping=True)
    async_session = async_sessionmaker(engine, expire_on_commit=False)

    try:
        # Step 1: Get PLD company and instruments
        print("=" * 80)
        print("FIX PLD DEBT AMOUNTS")
        print("=" * 80)

        async with async_session() as session:
            result = await session.execute(
                select(Company).where(Company.ticker == 'PLD')
            )
            company = result.scalar_one_or_none()
            if not company:
                print("ERROR: PLD not found in database")
                return

            print(f"Company: {company.name} (CIK: {company.cik})")

            # Get instruments needing amounts
            result = await session.execute(
                select(DebtInstrument).where(
                    DebtInstrument.company_id == company.id,
                    DebtInstrument.is_active == True,
                    or_(
                        DebtInstrument.outstanding == None,
                        DebtInstrument.outstanding <= 0,
                    )
                )
            )
            instruments = list(result.scalars().all())

            # Detach instrument data
            instrument_data = []
            for inst in instruments:
                instrument_data.append({
                    'id': inst.id,
                    'name': inst.name,
                    'cusip': inst.cusip,
                    'interest_rate': inst.interest_rate,
                    'maturity_date': inst.maturity_date,
                    'instrument_type': inst.instrument_type,
                    'attributes': dict(inst.attributes) if inst.attributes else {},
                })

        print(f"Instruments needing amounts: {len(instrument_data)}")

        if not instrument_data:
            print("No instruments need amounts — nothing to do.")
            return

        # Step 2: Download PLD's latest 10-K from SEC
        print("\nDownloading PLD filings from SEC-API...")
        from app.services.sec_client import SecApiClient
        client = SecApiClient(api_key=sec_api_key)

        # Get the latest 10-K filing
        filings = client.get_filings_by_ticker(
            'PLD', form_types=['10-K'], max_filings=3, cik=company.cik
        )

        if not filings:
            print("ERROR: No 10-K filings found for PLD")
            return

        debt_note = None
        filing_date = None
        for filing in filings:
            filing_url = filing.get('linkToFilingDetails', '')
            filed_at = filing.get('filedAt', '')[:10]
            print(f"  Trying 10-K filed {filed_at}...")

            content = client.get_filing_content(filing_url)
            if not content:
                print(f"    Could not download content")
                continue

            print(f"    Downloaded {len(content):,} chars")

            # Extract debt note section
            debt_note = extract_debt_note_from_filing(content)
            if debt_note:
                filing_date = filed_at
                print(f"    Found debt note section: {len(debt_note):,} chars")
                break
            else:
                print(f"    Could not find debt note section, trying next filing...")

        if not debt_note:
            # Try 10-Q filings as fallback
            print("\nTrying 10-Q filings as fallback...")
            q_filings = client.get_filings_by_ticker(
                'PLD', form_types=['10-Q'], max_filings=3, cik=company.cik
            )
            for filing in q_filings:
                filing_url = filing.get('linkToFilingDetails', '')
                filed_at = filing.get('filedAt', '')[:10]
                print(f"  Trying 10-Q filed {filed_at}...")

                content = client.get_filing_content(filing_url)
                if not content:
                    continue

                print(f"    Downloaded {len(content):,} chars")
                debt_note = extract_debt_note_from_filing(content)
                if debt_note:
                    filing_date = filed_at
                    print(f"    Found debt note section: {len(debt_note):,} chars")
                    break

        if not debt_note:
            print("\nERROR: Could not find debt note section in any filing.")
            print("Manual extraction may be needed.")
            return

        # Show a preview of the debt note
        print(f"\nDebt note preview (first 500 chars):")
        print(debt_note[:500])
        print("...")

        # Step 3: Build instrument list and call Gemini
        print(f"\nSending {len(instrument_data)} instruments to Gemini ({args.model})...")

        # Create proxy objects for format_instrument_list
        class _Proxy:
            pass

        proxies = []
        for d in instrument_data:
            p = _Proxy()
            p.name = d['name']
            p.cusip = d['cusip']
            p.interest_rate = d['interest_rate']
            p.maturity_date = d['maturity_date']
            p.instrument_type = d['instrument_type']
            p.id = d['id']
            proxies.append(p)

        instrument_list = format_instrument_list(proxies)

        prompt = TARGETED_EXTRACTION_PROMPT.format(
            company_name=company.name,
            instrument_list=instrument_list,
            filing_type='10-K',
            filing_date=filing_date,
            content=debt_note,
        )

        result = await get_gemini_response(prompt, model_name=args.model)
        amounts = result.get('amounts', [])

        # Normalize key names
        valid_amounts = []
        for entry in amounts:
            amt = entry.get('outstanding_cents') or entry.get('outstanding_amount_cents')
            if amt and isinstance(amt, (int, float)) and amt > 0:
                entry['outstanding_cents'] = int(amt)
                valid_amounts.append(entry)

        print(f"Gemini returned {len(valid_amounts)} amounts")
        if result.get('scale_detected'):
            print(f"Scale detected: {result['scale_detected']}")

        # Step 4: Match and apply
        matched_count = 0
        matched_amounts = {}  # instrument_data index -> amount info

        for entry in valid_amounts:
            idx_1based = entry.get('instrument_index')
            if idx_1based is None:
                continue
            try:
                idx = int(idx_1based) - 1
            except (ValueError, TypeError):
                continue

            if idx < 0 or idx >= len(instrument_data):
                continue

            amt = entry['outstanding_cents']
            matched_amounts[idx] = {
                'amount': amt,
                'confidence': entry.get('confidence', 'medium'),
                'notes': entry.get('notes', ''),
            }
            matched_count += 1

        print(f"\nMatched {matched_count}/{len(instrument_data)} instruments")

        # Show results
        total_dollars = 0
        for idx, match_info in sorted(matched_amounts.items()):
            name = instrument_data[idx]['name'] or instrument_data[idx].get('instrument_type', '(unnamed)')
            amt_dollars = match_info['amount'] / 100
            total_dollars += amt_dollars
            if amt_dollars >= 1_000_000_000:
                amt_str = f"${amt_dollars / 1_000_000_000:.2f}B"
            elif amt_dollars >= 1_000_000:
                amt_str = f"${amt_dollars / 1_000_000:.1f}M"
            else:
                amt_str = f"${amt_dollars:,.0f}"
            conf = match_info['confidence']
            print(f"  {idx+1:3d}. {name[:60]:60s} {amt_str:>12s} ({conf})")

        if total_dollars >= 1_000_000_000:
            total_str = f"${total_dollars / 1_000_000_000:.2f}B"
        else:
            total_str = f"${total_dollars / 1_000_000:.0f}M"
        print(f"\nTotal: {total_str}")

        # Step 5: Update database
        if args.dry_run:
            print(f"\nDRY RUN: Would update {matched_count} instruments")
        else:
            if not matched_amounts:
                print("\nNo amounts to update.")
                return

            async with async_session() as session:
                for idx, match_info in matched_amounts.items():
                    inst_id = instrument_data[idx]['id']
                    result = await session.execute(
                        select(DebtInstrument).where(DebtInstrument.id == inst_id)
                    )
                    db_inst = result.scalar_one_or_none()
                    if db_inst:
                        db_inst.outstanding = match_info['amount']
                        if not db_inst.principal or db_inst.principal <= 0:
                            db_inst.principal = match_info['amount']

                        attrs = dict(db_inst.attributes) if db_inst.attributes else {}
                        attrs.update({
                            'amount_source': 'doc_backfill',
                            'amount_doc_type': '10-K',
                            'amount_section_type': 'debt_footnote_manual',
                            'amount_doc_date': filing_date,
                            'amount_confidence': match_info['confidence'],
                            'amount_updated_at': datetime.now().strftime('%Y-%m-%d'),
                        })
                        db_inst.attributes = attrs

                await session.commit()
                print(f"\nUpdated {matched_count} instruments in database.")

    finally:
        await engine.dispose()


if __name__ == '__main__':
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())
