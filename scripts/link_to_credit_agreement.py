#!/usr/bin/env python3
"""
Link unlinked loans/revolvers to their credit agreements.

Term loans and revolvers are governed by credit agreements. When no specific
matching is found, we can link to the most recent credit agreement with lower confidence.

Usage:
    python scripts/link_to_credit_agreement.py --dry-run
    python scripts/link_to_credit_agreement.py --save
    python scripts/link_to_credit_agreement.py --ticker CHTR --save
"""

import argparse
import asyncio
import io
import os
import re
import sys
from decimal import Decimal

if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from sqlalchemy import text
from app.core.database import async_session_maker
from app.models import DebtInstrumentDocument


# Instrument types that should be linked to credit agreements
CREDIT_FACILITY_TYPES = [
    'revolver', 'revolving', 'term_loan', 'term_loan_a', 'term_loan_b', 'term_loan_c',
    'credit_facility', 'revolving_credit_facility', 'bridge_loan', 'delayed_draw',
]


def is_loan_type(instrument_type: str | None, name: str | None) -> bool:
    """Check if this instrument is a loan/revolver that needs a credit agreement."""
    if instrument_type:
        inst_lower = instrument_type.lower()
        if any(t in inst_lower for t in CREDIT_FACILITY_TYPES):
            return True
        if 'loan' in inst_lower or 'revolver' in inst_lower or 'credit' in inst_lower:
            return True

    if name:
        name_lower = name.lower()
        if any(t in name_lower for t in ['term loan', 'revolver', 'revolving', 'credit facility']):
            return True

    return False


def get_credit_agreement_date(title: str) -> str | None:
    """Extract the date from a credit agreement title."""
    if not title:
        return None

    patterns = [
        r'dated\s+(?:as\s+of\s+)?(\w+\s+\d{1,2},?\s+\d{4})',
        r'(\w+\s+\d{1,2},?\s+\d{4})',
        r'(\d{4}-\d{2}-\d{2})',
    ]

    for pattern in patterns:
        match = re.search(pattern, title, re.IGNORECASE)
        if match:
            return match.group(1)

    return None


async def link_to_credit_agreements(ticker: str | None = None, dry_run: bool = True):
    """Link unlinked loans/revolvers to credit agreements."""

    async with async_session_maker() as session:
        print("=" * 70)
        print("LINK LOANS/REVOLVERS TO CREDIT AGREEMENTS")
        print("=" * 70)
        print(f"Mode: {'DRY RUN' if dry_run else 'SAVE'}")
        if ticker:
            print(f"Ticker: {ticker}")
        print()

        # Build query for companies to process
        if ticker:
            company_query = text('''
                SELECT id, ticker FROM companies WHERE ticker = :ticker
            ''')
            params = {'ticker': ticker}
        else:
            # Get companies with unlinked loan-type instruments
            company_query = text('''
                SELECT DISTINCT c.id, c.ticker
                FROM companies c
                JOIN debt_instruments di ON di.company_id = c.id
                WHERE di.is_active = true
                  AND di.id NOT IN (SELECT DISTINCT debt_instrument_id FROM debt_instrument_documents)
                  AND (di.attributes IS NULL OR di.attributes->>'no_document_expected' IS NULL
                       OR di.attributes->>'no_document_expected' != 'true')
                ORDER BY c.ticker
            ''')
            params = {}

        result = await session.execute(company_query, params)
        companies = result.fetchall()

        print(f"Processing {len(companies)} companies")
        print()

        total_linked = 0
        total_skipped = 0

        for company_id, company_ticker in companies:
            # Get credit agreements for this company (most recent first)
            result = await session.execute(text('''
                SELECT id, section_title, content, filing_date
                FROM document_sections
                WHERE company_id = :cid AND section_type = 'credit_agreement'
                ORDER BY filing_date DESC
            '''), {'cid': company_id})
            docs = result.fetchall()

            if not docs:
                continue

            # Use the most recent credit agreement
            credit_agreement = {
                'id': docs[0][0],
                'title': docs[0][1],
                'date': get_credit_agreement_date(docs[0][1]) or str(docs[0][3])
            }

            # Get unlinked loan-type instruments
            result = await session.execute(text('''
                SELECT id, name, instrument_type
                FROM debt_instruments
                WHERE company_id = :cid
                  AND is_active = true
                  AND id NOT IN (SELECT DISTINCT debt_instrument_id FROM debt_instrument_documents)
                  AND (attributes IS NULL OR attributes->>'no_document_expected' IS NULL
                       OR attributes->>'no_document_expected' != 'true')
            '''), {'cid': company_id})
            instruments = result.fetchall()

            # Filter to only loan-type instruments
            loan_instruments = [
                (id, name, itype) for id, name, itype in instruments
                if is_loan_type(itype, name)
            ]

            if not loan_instruments:
                continue

            print(f"\n{company_ticker}: {len(loan_instruments)} unlinked loan instruments")
            print(f"  Credit agreement: {credit_agreement['title'][:60]}...")
            print(f"  Date: {credit_agreement['date']}")

            for inst_id, inst_name, inst_type in loan_instruments:
                inst_display = (inst_name or inst_type or 'Unknown')[:50]
                print(f"    -> {inst_display}")

                if not dry_run:
                    try:
                        new_link = DebtInstrumentDocument(
                            debt_instrument_id=inst_id,
                            document_section_id=credit_agreement['id'],
                            relationship_type='governs',
                            match_confidence=Decimal('0.60'),  # Lower confidence for fallback
                            match_method='credit_agreement_fallback',
                            match_evidence={'note': 'Linked to most recent credit agreement; specific matching not found'},
                            is_verified=False,
                            created_by='credit_agreement_linker',
                        )
                        session.add(new_link)
                        total_linked += 1
                    except Exception as e:
                        print(f"      Error: {e}")
                        total_skipped += 1
                else:
                    total_linked += 1

        if not dry_run:
            await session.commit()

        print()
        print("=" * 70)
        print(f"Total linked: {total_linked}")
        print(f"Total skipped: {total_skipped}")

        return {'linked': total_linked, 'skipped': total_skipped}


async def main():
    parser = argparse.ArgumentParser(description="Link loans/revolvers to credit agreements")
    parser.add_argument("--ticker", help="Single company ticker")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be changed")
    parser.add_argument("--save", action="store_true", help="Save changes")
    args = parser.parse_args()

    if not args.dry_run and not args.save:
        parser.error("Either --dry-run or --save is required")

    result = await link_to_credit_agreements(ticker=args.ticker, dry_run=not args.save)
    print(f"\nResult: {result}")


if __name__ == "__main__":
    asyncio.run(main())
