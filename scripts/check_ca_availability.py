#!/usr/bin/env python3
"""Check credit agreement document availability."""

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


async def check_ca_availability():
    async with async_session_maker() as session:
        # How many companies have credit facilities vs how many have CAs
        result = await session.execute(text("""
            WITH companies_with_cf AS (
                SELECT DISTINCT c.id, c.ticker
                FROM companies c
                JOIN debt_instruments di ON di.company_id = c.id
                WHERE di.is_active = true
                AND di.instrument_type IN ('revolver', 'term_loan_a', 'term_loan_b', 'term_loan', 'abl', 'credit_facility')
            ),
            companies_with_ca AS (
                SELECT DISTINCT company_id
                FROM document_sections
                WHERE section_type = 'credit_agreement'
            )
            SELECT
                (SELECT COUNT(*) FROM companies_with_cf) as companies_with_cf,
                (SELECT COUNT(*) FROM companies_with_cf WHERE id IN (SELECT company_id FROM companies_with_ca)) as companies_with_both,
                (SELECT COUNT(*) FROM companies_with_cf WHERE id NOT IN (SELECT company_id FROM companies_with_ca)) as companies_cf_no_ca
        """))
        row = result.fetchone()
        print(f"Companies with credit facilities: {row[0]}")
        print(f"  - Have credit agreements downloaded: {row[1]}")
        print(f"  - Missing credit agreements: {row[2]}")

        # Get list of companies missing CAs
        result = await session.execute(text("""
            SELECT DISTINCT c.ticker
            FROM companies c
            JOIN debt_instruments di ON di.company_id = c.id
            WHERE di.is_active = true
            AND di.instrument_type IN ('revolver', 'term_loan_a', 'term_loan_b', 'term_loan', 'abl', 'credit_facility')
            AND c.id NOT IN (
                SELECT DISTINCT company_id FROM document_sections WHERE section_type = 'credit_agreement'
            )
            ORDER BY c.ticker
        """))
        tickers = [row[0] for row in result.fetchall()]
        print(f"\nCompanies missing credit agreements ({len(tickers)}):")
        print(", ".join(tickers))

        # Count unlinked facilities at companies with CAs vs without
        print()
        print("=" * 70)
        result = await session.execute(text("""
            SELECT
                CASE WHEN ds_ca.company_id IS NOT NULL THEN 'Has CA docs' ELSE 'Missing CA docs' END as status,
                COUNT(DISTINCT di.id) as unlinked_facilities
            FROM debt_instruments di
            JOIN companies c ON c.id = di.company_id
            LEFT JOIN debt_instrument_documents did ON did.debt_instrument_id = di.id
            LEFT JOIN (
                SELECT DISTINCT company_id FROM document_sections WHERE section_type = 'credit_agreement'
            ) ds_ca ON ds_ca.company_id = c.id
            WHERE di.is_active = true
              AND di.instrument_type IN ('revolver', 'term_loan_a', 'term_loan_b', 'term_loan', 'abl', 'credit_facility')
              AND did.id IS NULL
              AND (di.attributes IS NULL OR di.attributes->>'no_document_expected' IS NULL)
            GROUP BY CASE WHEN ds_ca.company_id IS NOT NULL THEN 'Has CA docs' ELSE 'Missing CA docs' END
        """))
        print("\nUnlinked credit facilities by CA availability:")
        for row in result.fetchall():
            print(f"  {row[0]}: {row[1]}")


async def main():
    await check_ca_availability()


if __name__ == "__main__":
    asyncio.run(main())
