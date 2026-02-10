#!/usr/bin/env python3
"""Analyze credit facility matching gaps."""

import asyncio
import sys
import io
import os

if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from sqlalchemy import text
from app.core.database import async_session_maker


async def analyze_cf_patterns():
    async with async_session_maker() as session:
        # Get unlinked credit facilities with their characteristics
        result = await session.execute(text("""
            SELECT
                di.name,
                di.instrument_type,
                di.commitment,
                di.maturity_date,
                c.ticker,
                (SELECT COUNT(*) FROM document_sections ds WHERE ds.company_id = c.id AND ds.section_type = 'credit_agreement') as ca_count
            FROM debt_instruments di
            JOIN companies c ON c.id = di.company_id
            LEFT JOIN debt_instrument_documents did ON did.debt_instrument_id = di.id
            WHERE di.is_active = true
              AND di.instrument_type IN ('revolver', 'term_loan_a', 'term_loan_b', 'term_loan', 'abl', 'credit_facility')
              AND did.id IS NULL
              AND (di.attributes IS NULL OR di.attributes->>'no_document_expected' IS NULL)
            ORDER BY c.ticker, di.name
            LIMIT 100
        """))
        rows = result.fetchall()

        print("UNLINKED CREDIT FACILITIES - SAMPLE")
        print("=" * 120)

        # Group by pattern
        generic_count = 0
        specific_count = 0

        for row in rows:
            name = row[0] or ''
            inst_type = row[1]
            commitment = row[2]
            maturity = row[3]
            ticker = row[4]
            ca_count = row[5]

            # Check if generic
            name_lower = name.lower()
            is_generic = any([
                'senior secured' in name_lower and 'credit facility' in name_lower and len(name) < 50,
                'revolving credit facility' in name_lower and len(name) < 40,
                name_lower in ['revolver', 'term loan', 'term loan a', 'term loan b', 'credit facility'],
                name_lower.startswith('term loan') and len(name) < 25,
            ])

            if is_generic:
                generic_count += 1
            else:
                specific_count += 1

            marker = "GENERIC" if is_generic else "SPECIFIC"
            print(f"{ticker:6} | {marker:8} | {inst_type:15} | {name[:55]:55} | CAs: {ca_count}")

        print()
        print(f"Generic names: {generic_count}")
        print(f"Specific names: {specific_count}")

        # Now look at specific ones more closely
        print()
        print("=" * 120)
        print("SPECIFIC FACILITY NAMES - POTENTIAL FOR BETTER MATCHING")
        print("=" * 120)

        result = await session.execute(text("""
            SELECT
                di.name,
                di.instrument_type,
                di.maturity_date,
                c.ticker
            FROM debt_instruments di
            JOIN companies c ON c.id = di.company_id
            LEFT JOIN debt_instrument_documents did ON did.debt_instrument_id = di.id
            WHERE di.is_active = true
              AND di.instrument_type IN ('revolver', 'term_loan_a', 'term_loan_b', 'term_loan', 'abl', 'credit_facility')
              AND did.id IS NULL
              AND (di.attributes IS NULL OR di.attributes->>'no_document_expected' IS NULL)
              AND di.maturity_date IS NOT NULL
            ORDER BY c.ticker
        """))

        with_maturity = result.fetchall()
        print(f"\nFacilities with maturity date (better matching potential): {len(with_maturity)}")
        for row in with_maturity[:30]:
            name, inst_type, maturity, ticker = row
            print(f"  {ticker:6} | {maturity} | {name[:60]}")

        # Check companies with exactly one CA
        print()
        print("=" * 120)
        print("COMPANIES WITH EXACTLY ONE CA - EASY WINS")
        print("=" * 120)

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
                di.name,
                di.instrument_type,
                ds.filing_date,
                ds.id as doc_id
            FROM debt_instruments di
            JOIN companies c ON c.id = di.company_id
            JOIN company_ca_counts cac ON cac.company_id = c.id
            JOIN document_sections ds ON ds.company_id = c.id AND ds.section_type = 'credit_agreement'
            LEFT JOIN debt_instrument_documents did ON did.debt_instrument_id = di.id
            WHERE di.is_active = true
              AND di.instrument_type IN ('revolver', 'term_loan_a', 'term_loan_b', 'term_loan', 'abl', 'credit_facility')
              AND did.id IS NULL
              AND (di.attributes IS NULL OR di.attributes->>'no_document_expected' IS NULL)
            ORDER BY c.ticker
        """))

        easy_wins = result.fetchall()
        print(f"\nUnlinked facilities at companies with ONLY one CA: {len(easy_wins)}")
        print("These can be safely auto-matched!")
        for row in easy_wins[:20]:
            ticker, name, inst_type, filing_date, doc_id = row
            print(f"  {ticker:6} | {inst_type:15} | {name[:50]} -> CA from {filing_date}")


async def main():
    await analyze_cf_patterns()


if __name__ == "__main__":
    asyncio.run(main())
