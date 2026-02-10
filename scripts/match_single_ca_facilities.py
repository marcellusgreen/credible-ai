#!/usr/bin/env python3
"""
Auto-match credit facilities at companies with only one credit agreement.

When a company has exactly one credit agreement document, any unlinked
credit facilities can be safely matched to that document since there's
no ambiguity.
"""

import argparse
import asyncio
import sys
import io
import os
import uuid

if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from sqlalchemy import text
from app.core.database import async_session_maker


async def match_single_ca_facilities(dry_run: bool = True):
    async with async_session_maker() as session:
        print("=" * 70)
        print("AUTO-MATCH FACILITIES AT SINGLE-CA COMPANIES")
        print("=" * 70)
        print(f"Mode: {'DRY RUN' if dry_run else 'EXECUTE'}")
        print()

        # Find unlinked facilities at companies with exactly one CA
        result = await session.execute(text("""
            WITH company_ca_counts AS (
                SELECT company_id, COUNT(*) as ca_count
                FROM document_sections
                WHERE section_type = 'credit_agreement'
                GROUP BY company_id
                HAVING COUNT(*) = 1
            )
            SELECT
                c.ticker,
                di.id as instrument_id,
                di.name as instrument_name,
                di.instrument_type,
                ds.id as document_id,
                ds.filing_date,
                ds.section_title
            FROM debt_instruments di
            JOIN companies c ON c.id = di.company_id
            JOIN company_ca_counts cac ON cac.company_id = c.id
            JOIN document_sections ds ON ds.company_id = c.id AND ds.section_type = 'credit_agreement'
            LEFT JOIN debt_instrument_documents did ON did.debt_instrument_id = di.id
            WHERE di.is_active = true
              AND di.instrument_type IN ('revolver', 'term_loan_a', 'term_loan_b', 'term_loan', 'abl', 'credit_facility')
              AND did.id IS NULL
              AND (di.attributes IS NULL OR di.attributes->>'no_document_expected' IS NULL)
            ORDER BY c.ticker, di.name
        """))

        matches = result.fetchall()

        if not matches:
            print("No unlinked facilities at single-CA companies.")
            return

        print(f"Found {len(matches)} facilities to match:")
        print()

        for row in matches:
            ticker, inst_id, inst_name, inst_type, doc_id, filing_date, section_title = row
            print(f"  {ticker}: {inst_type}")
            print(f"    Instrument: {inst_name[:60]}")
            print(f"    -> Document: {section_title[:60] if section_title else 'Credit Agreement'} ({filing_date})")
            print()

        if not dry_run:
            print("Creating links...")
            for row in matches:
                ticker, inst_id, inst_name, inst_type, doc_id, filing_date, section_title = row

                # Check if link already exists
                existing = await session.execute(text("""
                    SELECT 1 FROM debt_instrument_documents
                    WHERE debt_instrument_id = :inst_id AND document_section_id = :doc_id
                """), {"inst_id": str(inst_id), "doc_id": str(doc_id)})

                if existing.fetchone():
                    print(f"    [SKIP] Link already exists")
                    continue

                # Create the link
                await session.execute(text("""
                    INSERT INTO debt_instrument_documents
                    (id, debt_instrument_id, document_section_id, relationship_type,
                     match_method, match_confidence, match_evidence, is_verified, created_at)
                    VALUES
                    (:id, :inst_id, :doc_id, 'governing',
                     'auto_single_ca', 0.85, '{"reason": "Only credit agreement for company"}'::jsonb, false, NOW())
                """), {
                    "id": str(uuid.uuid4()),
                    "inst_id": str(inst_id),
                    "doc_id": str(doc_id)
                })

            await session.commit()
            print(f"\nCreated {len(matches)} links.")

        # Show updated coverage
        print()
        print("=" * 70)
        result = await session.execute(text("""
            SELECT
                COUNT(DISTINCT di.id) as total,
                COUNT(DISTINCT did.debt_instrument_id) as linked
            FROM debt_instruments di
            LEFT JOIN debt_instrument_documents did ON did.debt_instrument_id = di.id
            WHERE di.is_active = true
              AND di.instrument_type IN ('revolver', 'term_loan_a', 'term_loan_b', 'term_loan', 'abl', 'credit_facility')
              AND (di.attributes IS NULL OR di.attributes->>'no_document_expected' IS NULL)
        """))
        row = result.fetchone()
        total, linked = row
        print(f"Credit facility coverage: {linked}/{total} ({linked/total*100:.1f}%)")


async def main():
    parser = argparse.ArgumentParser(description="Auto-match facilities at single-CA companies")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be matched")
    parser.add_argument("--execute", action="store_true", help="Actually create links")
    args = parser.parse_args()

    if not args.dry_run and not args.execute:
        parser.error("Either --dry-run or --execute is required")

    await match_single_ca_facilities(dry_run=not args.execute)


if __name__ == "__main__":
    asyncio.run(main())
