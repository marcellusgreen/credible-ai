#!/usr/bin/env python3
"""
Fix is_unrestricted flag on root entities.

Root/parent companies should NEVER be marked as "unrestricted".
"Unrestricted subsidiary" is a specific legal term for subsidiaries
that are excluded from credit agreement covenants.

Usage:
    python scripts/fix_unrestricted_roots.py           # Dry run
    python scripts/fix_unrestricted_roots.py --save    # Apply fix
"""

import argparse

from sqlalchemy import text

from script_utils import get_db_session, print_header, run_async


async def main():
    parser = argparse.ArgumentParser(description="Fix is_unrestricted on root entities")
    parser.add_argument("--save", action="store_true", help="Apply fixes to database")
    args = parser.parse_args()

    print_header("FIX UNRESTRICTED ROOTS")

    async with get_db_session() as session:
        conn = await session.connection()
        # Find root entities marked as unrestricted
        result = await conn.execute(text('''
            SELECT c.ticker, e.name, e.id
            FROM entities e
            JOIN companies c ON c.id = e.company_id
            WHERE e.is_unrestricted = true AND e.is_root = true
            ORDER BY c.ticker
        '''))
        rows = result.fetchall()

        print(f"Found {len(rows)} root entities incorrectly marked as unrestricted:")
        for row in rows:
            print(f"  {row[0]}: {row[1]}")

        if args.save:
            # Fix them
            result = await conn.execute(text('''
                UPDATE entities
                SET is_unrestricted = false
                WHERE is_unrestricted = true AND is_root = true
            '''))
            print(f"\nFixed {result.rowcount} entities (set is_unrestricted=false)")
        else:
            print(f"\n[DRY RUN] Would fix {len(rows)} entities")
            print("Run with --save to apply")


if __name__ == "__main__":
    run_async(main())
