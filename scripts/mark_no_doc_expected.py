#!/usr/bin/env python3
"""
Mark instruments that don't need document linking.

Some instrument types (commercial paper, bank loans, etc.) don't have
publicly filed legal documents. This script marks them with a flag
in the attributes JSONB column so they're excluded from coverage metrics.

Usage:
    python scripts/mark_no_doc_expected.py --dry-run
    python scripts/mark_no_doc_expected.py --execute
"""

import argparse
import asyncio
import io
import os
import sys

if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from sqlalchemy import text
from app.core.database import async_session_maker


# Instrument types that don't have governing legal documents
NO_DOC_EXPECTED_TYPES = {
    'commercial_paper',       # Short-term, no indenture
    'bank_loan',              # Bilateral, no public document
    'abl',                    # Asset-backed line, internal docs
    'finance_lease',          # Lease agreement, not debt document
    'certificates_of_deposit',  # Bank product
    'structured_notes',       # Internal structuring
    'structured_liabilities', # Internal structuring
    'mtn_program',            # Program, not individual issuance
    'advances',               # FHLB advances, no public doc
    'asset_backed_securities', # Securitization docs separate
    'asset_backed_notes',     # Securitization docs separate
    'preferred_stock',        # Not debt
    'trust_preferred',        # Hybrid, separate docs
}

# Name patterns that indicate no document expected
NO_DOC_NAME_PATTERNS = [
    # Commercial paper and short-term
    '%Commercial paper%',
    '%Short-term bank loans%',
    '%Short-term borrowings%',
    # Generic buckets
    '%Other long-term debt%',
    '%Other Automotive%',
    '%Other debt%',
    '%Other borrowings%',
    '%Miscellaneous debt%',
    # Leases (not debt)
    '%Finance Leases%',
    '%Capital Leases%',
    '%Equipment Obligations%',
    # Bank products
    '%Brokered CDs%',
    '%Certificates of Deposit%',
    # Internal/related party
    '%Liability Related to Future Royalties%',
    '%Intercompany%',
    '%Related party%',
    # Bilateral facilities (no public doc)
    '%Letter of credit facilities%',
    '%Bilateral revolving credit%',
    '%Bilateral credit%',
    # Securitizations (separate docs)
    '%Securitization Notes Payable%',
    '%Credit card securitization%',
    '%Auto loan securitization%',
    '%Mortgage-backed%',
    '%Asset-backed%',
    # FHLB advances
    '%FHLB advances%',
    '%Federal Home Loan Bank%',
    # Medium-term note programs (no single indenture)
    '%Medium-term notes%',
    '%MTN program%',
    # Current portion buckets
    '%Current portion of%',
    '%Long-term Debt - Current%',
    # Aggregated/ranges (no single document)
    '% through %',  # e.g., "due 2025 through 2030"
    '%various maturities%',
    '%various rates%',
    # Generic titles without specifics
    '%senior unsecured notes$',  # Just "senior unsecured notes" with no rate/maturity
]


async def mark_no_doc_expected(dry_run: bool = True):
    async with async_session_maker() as session:
        print("=" * 70)
        print("MARK INSTRUMENTS - NO DOCUMENT EXPECTED")
        print("=" * 70)
        print(f"Mode: {'DRY RUN' if dry_run else 'EXECUTE'}")
        print()

        # 1. By instrument type
        print("1. BY INSTRUMENT TYPE")
        print("-" * 70)

        result = await session.execute(text("""
            SELECT id, name, instrument_type
            FROM debt_instruments
            WHERE is_active = true
              AND instrument_type = ANY(:types)
              AND (attributes IS NULL OR NOT (attributes ? 'no_document_expected'))
        """), {"types": list(NO_DOC_EXPECTED_TYPES)})
        by_type = result.fetchall()

        print(f"   Found: {len(by_type)} instruments")
        for row in by_type[:5]:
            print(f"     - {row[2]}: {row[1][:50] if row[1] else 'NULL'}")
        if len(by_type) > 5:
            print(f"     ... and {len(by_type) - 5} more")

        # 2. By name pattern
        print()
        print("2. BY NAME PATTERN")
        print("-" * 70)

        # Build OR conditions for name patterns
        pattern_conditions = " OR ".join([f"name LIKE '{p}'" for p in NO_DOC_NAME_PATTERNS])

        result = await session.execute(text(f"""
            SELECT id, name, instrument_type
            FROM debt_instruments
            WHERE is_active = true
              AND ({pattern_conditions})
              AND (attributes IS NULL OR NOT (attributes ? 'no_document_expected'))
        """))
        by_name = result.fetchall()

        # Exclude those already found by type
        type_ids = {row[0] for row in by_type}
        by_name = [row for row in by_name if row[0] not in type_ids]

        print(f"   Found: {len(by_name)} additional instruments")
        for row in by_name[:5]:
            print(f"     - {row[1][:60] if row[1] else 'NULL'}")
        if len(by_name) > 5:
            print(f"     ... and {len(by_name) - 5} more")

        # Combine and update
        all_ids = [row[0] for row in by_type] + [row[0] for row in by_name]

        print()
        print(f"TOTAL TO MARK: {len(all_ids)}")

        if not dry_run and all_ids:
            # Update in batches
            for i in range(0, len(all_ids), 100):
                batch = all_ids[i:i+100]
                placeholders = ", ".join([f"'{id}'::uuid" for id in batch])
                await session.execute(text(f"""
                    UPDATE debt_instruments
                    SET attributes = COALESCE(attributes, '{{}}'::jsonb) || '{{"no_document_expected": true}}'::jsonb
                    WHERE id IN ({placeholders})
                """))

            await session.commit()
            print(f"   Marked {len(all_ids)} instruments")

        # Show updated coverage
        print()
        print("=" * 70)
        print("UPDATED COVERAGE METRICS")
        print("=" * 70)

        result = await session.execute(text("""
            SELECT
                COUNT(DISTINCT di.id) as total,
                COUNT(DISTINCT did.debt_instrument_id) as linked,
                COUNT(DISTINCT CASE WHEN di.attributes->>'no_document_expected' = 'true' THEN di.id END) as no_doc_expected
            FROM debt_instruments di
            LEFT JOIN debt_instrument_documents did ON did.debt_instrument_id = di.id
            WHERE di.is_active = true
        """))
        row = result.fetchone()

        total = row[0]
        linked = row[1]
        no_doc = row[2]
        linkable = total - no_doc

        print(f"Total active instruments:     {total}")
        print(f"No document expected:         {no_doc}")
        print(f"Linkable instruments:         {linkable}")
        print(f"Actually linked:              {linked}")
        print()
        print(f"Raw coverage:                 {linked/total*100:.1f}%")
        print(f"Adjusted coverage:            {linked/linkable*100:.1f}% (excluding no-doc-expected)")


async def main():
    parser = argparse.ArgumentParser(description="Mark instruments that don't need document linking")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be changed")
    parser.add_argument("--execute", action="store_true", help="Actually make changes")
    args = parser.parse_args()

    if not args.dry_run and not args.execute:
        parser.error("Either --dry-run or --execute is required")

    await mark_no_doc_expected(dry_run=not args.execute)


if __name__ == "__main__":
    asyncio.run(main())
