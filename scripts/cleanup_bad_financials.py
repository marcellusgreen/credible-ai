#!/usr/bin/env python3
"""
Clean up old financial records with extraction failures.

Deletes records where:
- EBITDA is present but revenue is missing (extraction failure)
- A newer, better record exists for the same company

Usage:
    python scripts/cleanup_bad_financials.py          # Dry run
    python scripts/cleanup_bad_financials.py --save   # Apply cleanup
"""

import argparse

from sqlalchemy import text

from script_utils import get_db_session, print_header, run_async


async def main():
    parser = argparse.ArgumentParser(description="Clean up bad financial records")
    parser.add_argument("--save", action="store_true", help="Apply cleanup to database")
    args = parser.parse_args()

    print_header("CLEANUP BAD FINANCIALS")

    async with get_db_session() as session:
        conn = await session.connection()
        # Find records with EBITDA but no revenue (excluding MSTR)
        result = await conn.execute(text('''
            SELECT cf.id, c.ticker, cf.fiscal_year, cf.fiscal_quarter,
                   cf.ebitda / 100.0 / 1e9 as ebitda_b
            FROM company_financials cf
            JOIN companies c ON c.id = cf.company_id
            WHERE (cf.revenue IS NULL OR cf.revenue = 0)
            AND cf.ebitda IS NOT NULL AND cf.ebitda > 100000000
            AND c.ticker != 'MSTR'
            ORDER BY c.ticker, cf.fiscal_year DESC, cf.fiscal_quarter DESC
        '''))
        bad_records = result.fetchall()

        if not bad_records:
            print("No bad records found")
            return

        print(f"Found {len(bad_records)} records with EBITDA but no revenue:")
        for row in bad_records:
            print(f"  {row[1]} Q{row[3]} {row[2]}: EBITDA ${row[4]:.2f}B")

        # Check which companies have newer, better data
        print("\nChecking for better records...")
        to_delete = []

        for bad in bad_records:
            bad_id, ticker, year, quarter, _ = bad

            # Check if there's a newer record with revenue
            result = await conn.execute(text('''
                SELECT cf.fiscal_year, cf.fiscal_quarter, cf.revenue / 100.0 / 1e9 as rev_b
                FROM company_financials cf
                JOIN companies c ON c.id = cf.company_id
                WHERE c.ticker = :ticker
                AND cf.revenue IS NOT NULL AND cf.revenue > 0
                AND (cf.fiscal_year > :year OR (cf.fiscal_year = :year AND cf.fiscal_quarter > :quarter))
                ORDER BY cf.fiscal_year DESC, cf.fiscal_quarter DESC
                LIMIT 1
            '''), {'ticker': ticker, 'year': year, 'quarter': quarter})

            newer = result.fetchone()
            if newer:
                print(f"  {ticker}: Has newer good record (Q{newer[1]} {newer[0]}: ${newer[2]:.2f}B revenue)")
                to_delete.append(bad_id)
            else:
                print(f"  {ticker}: No newer good record - keeping")

        if not to_delete:
            print("\nNo records to delete (all are the latest for their company)")
            return

        print(f"\n{len(to_delete)} records can be safely deleted")

        if args.save:
            for record_id in to_delete:
                await conn.execute(text('''
                    DELETE FROM company_financials WHERE id = :id
                '''), {'id': record_id})
            print(f"Deleted {len(to_delete)} records")
        else:
            print("\n[DRY RUN] Run with --save to delete")


if __name__ == "__main__":
    run_async(main())
