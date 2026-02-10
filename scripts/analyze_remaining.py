#!/usr/bin/env python3
"""Analyze remaining unlinked instruments."""

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


async def analyze():
    async with async_session_maker() as session:
        print("=" * 70)
        print("REMAINING UNLINKED INSTRUMENTS ANALYSIS")
        print("=" * 70)

        # Get count by instrument type
        result = await session.execute(text("""
            SELECT
                di.instrument_type,
                COUNT(*) as cnt
            FROM debt_instruments di
            LEFT JOIN debt_instrument_documents did ON did.debt_instrument_id = di.id
            WHERE di.is_active = true
              AND did.id IS NULL
              AND (di.attributes IS NULL OR di.attributes->>'no_document_expected' IS NULL)
            GROUP BY di.instrument_type
            ORDER BY cnt DESC
        """))

        print("\n1. BY INSTRUMENT TYPE")
        print("-" * 70)
        total = 0
        for row in result.fetchall():
            print(f"  {row[0] or 'NULL':30}: {row[1]:4}")
            total += row[1]
        print(f"  {'TOTAL':30}: {total:4}")

        # Get count by company
        result = await session.execute(text("""
            SELECT
                c.ticker,
                COUNT(*) as cnt
            FROM debt_instruments di
            JOIN companies c ON c.id = di.company_id
            LEFT JOIN debt_instrument_documents did ON did.debt_instrument_id = di.id
            WHERE di.is_active = true
              AND did.id IS NULL
              AND (di.attributes IS NULL OR di.attributes->>'no_document_expected' IS NULL)
            GROUP BY c.ticker
            ORDER BY cnt DESC
            LIMIT 20
        """))

        print("\n2. TOP 20 COMPANIES WITH UNLINKED INSTRUMENTS")
        print("-" * 70)
        for row in result.fetchall():
            print(f"  {row[0]:6}: {row[1]:3} unlinked")

        # Check document availability for unlinked
        result = await session.execute(text("""
            WITH unlinked AS (
                SELECT di.id, di.company_id, di.instrument_type
                FROM debt_instruments di
                LEFT JOIN debt_instrument_documents did ON did.debt_instrument_id = di.id
                WHERE di.is_active = true
                  AND did.id IS NULL
                  AND (di.attributes IS NULL OR di.attributes->>'no_document_expected' IS NULL)
            )
            SELECT
                CASE
                    WHEN u.instrument_type IN ('revolver', 'term_loan_a', 'term_loan_b', 'term_loan', 'abl', 'credit_facility') THEN 'credit_facility'
                    ELSE 'notes_bonds'
                END as category,
                CASE
                    WHEN EXISTS (
                        SELECT 1 FROM document_sections ds
                        WHERE ds.company_id = u.company_id
                        AND ds.section_type = CASE
                            WHEN u.instrument_type IN ('revolver', 'term_loan_a', 'term_loan_b', 'term_loan', 'abl', 'credit_facility') THEN 'credit_agreement'
                            ELSE 'indenture'
                        END
                    ) THEN 'Has docs'
                    ELSE 'No docs'
                END as doc_status,
                COUNT(*) as cnt
            FROM unlinked u
            GROUP BY 1, 2
            ORDER BY 1, 2
        """))

        print("\n3. DOCUMENT AVAILABILITY FOR UNLINKED")
        print("-" * 70)
        for row in result.fetchall():
            print(f"  {row[0]:20} | {row[1]:10}: {row[2]:4}")

        # Sample of unlinked notes/bonds
        result = await session.execute(text("""
            SELECT
                c.ticker,
                di.name,
                di.instrument_type,
                di.interest_rate,
                di.maturity_date,
                (SELECT COUNT(*) FROM document_sections ds WHERE ds.company_id = c.id AND ds.section_type = 'indenture') as indenture_count
            FROM debt_instruments di
            JOIN companies c ON c.id = di.company_id
            LEFT JOIN debt_instrument_documents did ON did.debt_instrument_id = di.id
            WHERE di.is_active = true
              AND did.id IS NULL
              AND (di.attributes IS NULL OR di.attributes->>'no_document_expected' IS NULL)
              AND di.instrument_type NOT IN ('revolver', 'term_loan_a', 'term_loan_b', 'term_loan', 'abl', 'credit_facility')
            ORDER BY c.ticker
            LIMIT 30
        """))

        print("\n4. SAMPLE UNLINKED NOTES/BONDS (30)")
        print("-" * 70)
        for row in result.fetchall():
            ticker, name, itype, rate, maturity, ind_count = row
            rate_str = f"{rate/100:.2f}%" if rate else "N/A"
            print(f"  {ticker:6} | {itype:20} | {name[:35]:35} | {rate_str:8} | {maturity} | {ind_count} indentures")

        # Sample of unlinked credit facilities
        result = await session.execute(text("""
            SELECT
                c.ticker,
                di.name,
                di.instrument_type,
                di.maturity_date,
                (SELECT COUNT(*) FROM document_sections ds WHERE ds.company_id = c.id AND ds.section_type = 'credit_agreement') as ca_count
            FROM debt_instruments di
            JOIN companies c ON c.id = di.company_id
            LEFT JOIN debt_instrument_documents did ON did.debt_instrument_id = di.id
            WHERE di.is_active = true
              AND did.id IS NULL
              AND (di.attributes IS NULL OR di.attributes->>'no_document_expected' IS NULL)
              AND di.instrument_type IN ('revolver', 'term_loan_a', 'term_loan_b', 'term_loan', 'abl', 'credit_facility')
            ORDER BY c.ticker
            LIMIT 30
        """))

        print("\n5. SAMPLE UNLINKED CREDIT FACILITIES (30)")
        print("-" * 70)
        for row in result.fetchall():
            ticker, name, itype, maturity, ca_count = row
            print(f"  {ticker:6} | {itype:15} | {name[:40]:40} | {maturity} | {ca_count} CAs")

        # Categorize WHY they're unlinked
        print("\n6. ROOT CAUSE ANALYSIS")
        print("-" * 70)

        # Generic names that are hard to match
        result = await session.execute(text("""
            SELECT COUNT(*)
            FROM debt_instruments di
            LEFT JOIN debt_instrument_documents did ON did.debt_instrument_id = di.id
            WHERE di.is_active = true
              AND did.id IS NULL
              AND (di.attributes IS NULL OR di.attributes->>'no_document_expected' IS NULL)
              AND (
                  di.name IS NULL
                  OR di.name = ''
                  OR di.name LIKE 'Unknown%'
                  OR di.name LIKE 'Other %'
              )
        """))
        generic_names = result.fetchone()[0]
        print(f"  Generic/missing names (Unknown, Other, NULL): {generic_names}")

        # No rate or maturity
        result = await session.execute(text("""
            SELECT COUNT(*)
            FROM debt_instruments di
            LEFT JOIN debt_instrument_documents did ON did.debt_instrument_id = di.id
            WHERE di.is_active = true
              AND did.id IS NULL
              AND (di.attributes IS NULL OR di.attributes->>'no_document_expected' IS NULL)
              AND di.interest_rate IS NULL
              AND di.maturity_date IS NULL
        """))
        no_identifiers = result.fetchone()[0]
        print(f"  No rate AND no maturity date: {no_identifiers}")

        # Companies with 0 relevant documents
        result = await session.execute(text("""
            WITH unlinked AS (
                SELECT DISTINCT di.company_id, di.instrument_type
                FROM debt_instruments di
                LEFT JOIN debt_instrument_documents did ON did.debt_instrument_id = di.id
                WHERE di.is_active = true
                  AND did.id IS NULL
                  AND (di.attributes IS NULL OR di.attributes->>'no_document_expected' IS NULL)
            )
            SELECT COUNT(DISTINCT u.company_id)
            FROM unlinked u
            WHERE NOT EXISTS (
                SELECT 1 FROM document_sections ds
                WHERE ds.company_id = u.company_id
                AND ds.section_type IN ('indenture', 'credit_agreement')
            )
        """))
        no_docs = result.fetchone()[0]
        print(f"  Companies with NO relevant documents: {no_docs}")


async def main():
    await analyze()


if __name__ == "__main__":
    asyncio.run(main())
