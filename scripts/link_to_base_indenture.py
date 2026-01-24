#!/usr/bin/env python3
"""
Link unlinked notes to their base indenture.

Most companies have a single base indenture (dated in the 1990s-2000s) under which
all their notes are issued. When no specific supplemental indenture is found,
we can link to the base indenture with lower confidence.

Usage:
    python scripts/link_to_base_indenture.py --dry-run
    python scripts/link_to_base_indenture.py --save
    python scripts/link_to_base_indenture.py --ticker CSX --save
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


def is_officer_certificate(title: str, content: str) -> bool:
    """Check if this is an officer certificate (which we should skip)."""
    title_lower = title.lower() if title else ''
    content_lower = content[:2000].lower() if content else ''

    # Skip officer certificates and pricing actions
    if any(x in title_lower for x in ['officer', 'pricing', 'certificate']):
        return True
    if any(x in content_lower for x in ['action of authorized pricing officers', 'officer certificate']):
        return True

    return False


def is_base_indenture(title: str, content: str) -> bool:
    """Check if this is a base indenture (not a supplemental or officer certificate)."""
    title_lower = title.lower() if title else ''
    content_lower = content[:2000].lower() if content else ''

    if is_officer_certificate(title, content):
        return False

    # Skip supplemental indentures
    if 'supplemental' in title_lower:
        return False

    # Look for base indenture markers
    if 'indenture' in title_lower:
        # Check for base indenture characteristics
        if any(x in content_lower for x in ['unlimited as to aggregate principal', 'base indenture',
                                            'original indenture', 'may from time to time']):
            return True
        # If it's longer than 20k chars, it's likely a base indenture
        if len(content) > 20000:
            return True

    return False


def is_any_indenture(title: str, content: str) -> bool:
    """Check if this is any valid indenture (base or supplemental, but not officer cert)."""
    title_lower = title.lower() if title else ''

    if is_officer_certificate(title, content):
        return False

    # Accept any indenture (base or supplemental)
    if 'indenture' in title_lower:
        return True

    return False


def extract_base_indenture_date(title: str) -> str | None:
    """Extract the date from a base indenture title like 'dated as of August 1, 1990'."""
    if not title:
        return None

    patterns = [
        r'dated\s+(?:as\s+of\s+)?(\w+\s+\d{1,2},?\s+\d{4})',
        r'dated\s+(?:as\s+of\s+)?(\d{1,2}/\d{1,2}/\d{4})',
    ]

    for pattern in patterns:
        match = re.search(pattern, title, re.IGNORECASE)
        if match:
            return match.group(1)

    return None


async def link_to_base_indentures(ticker: str | None = None, dry_run: bool = True):
    """Link unlinked notes to base indentures."""

    async with async_session_maker() as session:
        print("=" * 70)
        print("LINK NOTES TO BASE INDENTURES")
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
            # Get companies with unlinked note-type instruments
            company_query = text('''
                SELECT DISTINCT c.id, c.ticker
                FROM companies c
                JOIN debt_instruments di ON di.company_id = c.id
                WHERE di.is_active = true
                  AND di.id NOT IN (SELECT DISTINCT debt_instrument_id FROM debt_instrument_documents)
                  AND (di.attributes IS NULL OR di.attributes->>'no_document_expected' IS NULL
                       OR di.attributes->>'no_document_expected' != 'true')
                  AND (di.instrument_type LIKE '%note%' OR di.instrument_type LIKE '%bond%'
                       OR di.instrument_type LIKE '%debenture%' OR di.instrument_type IS NULL)
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
            # Get base indentures for this company
            result = await session.execute(text('''
                SELECT id, section_title, content
                FROM document_sections
                WHERE company_id = :cid AND section_type = 'indenture'
                ORDER BY filing_date DESC
            '''), {'cid': company_id})
            docs = result.fetchall()

            # First try to find a base indenture
            best_indenture = None
            any_indenture = None

            for doc_id, title, content in docs:
                if is_base_indenture(title, content or ''):
                    best_indenture = {
                        'id': doc_id,
                        'title': title,
                        'date': extract_base_indenture_date(title),
                        'type': 'base'
                    }
                    break
                elif any_indenture is None and is_any_indenture(title, content or ''):
                    any_indenture = {
                        'id': doc_id,
                        'title': title,
                        'date': extract_base_indenture_date(title),
                        'type': 'supplemental'
                    }

            # Fall back to any indenture if no base indenture found
            if not best_indenture:
                best_indenture = any_indenture

            if not best_indenture:
                continue

            # Get unlinked note-type instruments
            result = await session.execute(text('''
                SELECT id, name, instrument_type
                FROM debt_instruments
                WHERE company_id = :cid
                  AND is_active = true
                  AND id NOT IN (SELECT DISTINCT debt_instrument_id FROM debt_instrument_documents)
                  AND (attributes IS NULL OR attributes->>'no_document_expected' IS NULL
                       OR attributes->>'no_document_expected' != 'true')
                  AND (instrument_type LIKE '%note%' OR instrument_type LIKE '%bond%'
                       OR instrument_type LIKE '%debenture%' OR instrument_type IS NULL)
            '''), {'cid': company_id})
            instruments = result.fetchall()

            if not instruments:
                continue

            print(f"\n{company_ticker}: {len(instruments)} unlinked instruments")
            print(f"  Indenture ({best_indenture['type']}): {best_indenture['title'][:60]}...")
            if best_indenture['date']:
                print(f"  Date: {best_indenture['date']}")

            # Use slightly lower confidence for supplemental indentures
            confidence = Decimal('0.60') if best_indenture['type'] == 'base' else Decimal('0.55')
            method = 'base_indenture_fallback' if best_indenture['type'] == 'base' else 'suppl_indenture_fallback'

            for inst_id, inst_name, inst_type in instruments:
                inst_display = (inst_name or inst_type or 'Unknown')[:50]
                print(f"    -> {inst_display}")

                if not dry_run:
                    try:
                        new_link = DebtInstrumentDocument(
                            debt_instrument_id=inst_id,
                            document_section_id=best_indenture['id'],
                            relationship_type='governs',
                            match_confidence=confidence,
                            match_method=method,
                            match_evidence={'note': f"Linked to {best_indenture['type']} indenture; specific supplemental indenture not found"},
                            is_verified=False,
                            created_by='base_indenture_linker',
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
    parser = argparse.ArgumentParser(description="Link notes to base indentures")
    parser.add_argument("--ticker", help="Single company ticker")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be changed")
    parser.add_argument("--save", action="store_true", help="Save changes")
    args = parser.parse_args()

    if not args.dry_run and not args.save:
        parser.error("Either --dry-run or --save is required")

    result = await link_to_base_indentures(ticker=args.ticker, dry_run=not args.save)
    print(f"\nResult: {result}")


if __name__ == "__main__":
    asyncio.run(main())
