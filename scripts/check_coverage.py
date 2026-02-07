#!/usr/bin/env python3
"""Check document coverage stats."""

from sqlalchemy import text

from script_utils import get_db_session, print_header, run_async


async def check():
    async with get_db_session() as session:
        # Total active instruments
        result = await session.execute(text('''
            SELECT COUNT(*) FROM debt_instruments WHERE is_active = true
        '''))
        total = result.scalar()

        # Instruments with document links
        result = await session.execute(text('''
            SELECT COUNT(DISTINCT di.id)
            FROM debt_instruments di
            JOIN debt_instrument_documents did ON did.debt_instrument_id = di.id
            WHERE di.is_active = true
        '''))
        with_links = result.scalar()

        # Instruments without document links
        result = await session.execute(text('''
            SELECT COUNT(*)
            FROM debt_instruments di
            LEFT JOIN debt_instrument_documents did ON did.debt_instrument_id = di.id
            WHERE di.is_active = true AND did.id IS NULL
        '''))
        without_links = result.scalar()

        # Breakdown by type
        result = await session.execute(text('''
            SELECT
                CASE
                    WHEN di.instrument_type IN ('revolver', 'term_loan_a', 'term_loan_b', 'term_loan') THEN 'Credit Facilities'
                    ELSE 'Notes/Bonds'
                END as category,
                COUNT(*) as total,
                COUNT(did.id) as with_links
            FROM debt_instruments di
            LEFT JOIN debt_instrument_documents did ON did.debt_instrument_id = di.id
            WHERE di.is_active = true
            GROUP BY 1
        '''))
        by_type = result.fetchall()

        print_header("DOCUMENT COVERAGE SUMMARY")
        print(f"\nTotal active instruments: {total}")
        print(f"With document links: {with_links}")
        print(f"Without document links: {without_links}")
        coverage_pct = (with_links / total * 100) if total > 0 else 0
        print(f"\nCoverage rate: {coverage_pct:.1f}%")

        print(f"\nBy category:")
        for row in by_type:
            cat = row[0]
            tot = row[1]
            linked = row[2]
            pct = (linked / tot * 100) if tot > 0 else 0
            print(f"  {cat}: {linked}/{tot} ({pct:.1f}%)")

        # Count links by confidence
        result = await session.execute(text('''
            SELECT
                CASE
                    WHEN match_confidence >= 0.7 THEN 'High (>=0.7)'
                    WHEN match_confidence >= 0.5 THEN 'Medium (0.5-0.7)'
                    ELSE 'Low (<0.5)'
                END as confidence_level,
                COUNT(*)
            FROM debt_instrument_documents
            GROUP BY 1
            ORDER BY 1
        '''))
        by_confidence = result.fetchall()

        print(f"\nLinks by confidence:")
        for row in by_confidence:
            print(f"  {row[0]}: {row[1]}")



if __name__ == "__main__":
    run_async(check())
