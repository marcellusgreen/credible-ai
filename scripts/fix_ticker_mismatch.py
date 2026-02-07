#!/usr/bin/env python3
"""
Fix ticker mismatches in company_metrics table.

Some records have CIK numbers instead of tickers. This script:
1. Identifies duplicate entries (both CIK and ticker exist for same company)
2. Deletes the CIK-based duplicates
3. Updates any remaining CIK-based entries to use proper ticker

Usage:
    python scripts/fix_ticker_mismatch.py --dry-run  # Preview changes
    python scripts/fix_ticker_mismatch.py            # Apply fix
"""

import argparse

from sqlalchemy import text

from script_utils import get_db_session, print_header, run_async


async def main():
    parser = argparse.ArgumentParser(description="Fix ticker mismatches")
    parser.add_argument("--dry-run", action="store_true", help="Preview without changing")
    args = parser.parse_args()

    print_header("FIX TICKER MISMATCHES")

    async with get_db_session() as session:
        conn = await session.connection()
        # Step 1: Find duplicates (same company_id, different tickers)
        print("Step 1: Finding duplicate entries...")
        result = await conn.execute(text("""
            SELECT cm.ticker as cik_ticker, c.ticker as real_ticker, c.name
            FROM company_metrics cm
            JOIN companies c ON cm.company_id = c.id
            WHERE cm.ticker != c.ticker
            AND EXISTS (
                SELECT 1 FROM company_metrics cm2
                WHERE cm2.company_id = cm.company_id
                AND cm2.ticker = c.ticker
            )
        """))
        duplicates = result.fetchall()

        print(f"Found {len(duplicates)} duplicate CIK entries to delete:")
        for cik, ticker, name in duplicates:
            print(f"  DELETE {cik} (keep {ticker}) - {name}")

        # Step 2: Find entries that just need renaming (no duplicate)
        print("\nStep 2: Finding entries to rename...")
        result = await conn.execute(text("""
            SELECT cm.ticker as old_ticker, c.ticker as new_ticker, c.name
            FROM company_metrics cm
            JOIN companies c ON cm.company_id = c.id
            WHERE cm.ticker != c.ticker
            AND NOT EXISTS (
                SELECT 1 FROM company_metrics cm2
                WHERE cm2.company_id = cm.company_id
                AND cm2.ticker = c.ticker
            )
        """))
        to_rename = result.fetchall()

        print(f"Found {len(to_rename)} entries to rename:")
        for old, new, name in to_rename:
            print(f"  {old} -> {new} ({name})")

        if args.dry_run:
            print("\n[DRY RUN] No changes made.")
            return

        # Step 3: Delete duplicates
        if duplicates:
            print("\nStep 3: Deleting duplicate CIK entries...")
            result = await conn.execute(text("""
                DELETE FROM company_metrics cm
                USING companies c
                WHERE cm.company_id = c.id
                AND cm.ticker != c.ticker
                AND EXISTS (
                    SELECT 1 FROM company_metrics cm2
                    WHERE cm2.company_id = cm.company_id
                    AND cm2.ticker = c.ticker
                )
            """))
            print(f"  Deleted {result.rowcount} duplicate entries.")

        # Step 4: Rename remaining CIK entries
        if to_rename:
            print("\nStep 4: Renaming remaining CIK entries...")
            result = await conn.execute(text("""
                UPDATE company_metrics cm
                SET ticker = c.ticker
                FROM companies c
                WHERE cm.company_id = c.id
                AND cm.ticker != c.ticker
            """))
            print(f"  Renamed {result.rowcount} entries.")

        print("\n[DONE] Database fixed!")


if __name__ == "__main__":
    run_async(main())
