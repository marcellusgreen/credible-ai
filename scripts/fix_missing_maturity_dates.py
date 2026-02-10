#!/usr/bin/env python3
"""
Fix instruments with missing maturity dates by extracting from instrument names.

Many instruments have names like "4.50% Notes due 2030" but maturity_date is NULL.
This script extracts the year from the name and sets the maturity date.

Usage:
    python scripts/fix_missing_maturity_dates.py --dry-run
    python scripts/fix_missing_maturity_dates.py --save
"""

import argparse
import asyncio
import io
import os
import re
import sys
from datetime import date

if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from sqlalchemy import text
from app.core.database import async_session_maker


def extract_maturity_year(name: str) -> int | None:
    """Extract maturity year from instrument name."""
    if not name:
        return None

    # Pattern: "due 2030" or "due March 2030" or "due 3/15/2030"
    patterns = [
        r'due\s+(?:January|February|March|April|May|June|July|August|September|October|November|December)?\s*\d{0,2},?\s*(20\d{2})',
        r'due\s+(20\d{2})',
        r'due\s+\d{1,2}/\d{1,2}/(20\d{2})',
        r'due\s+fiscal\s+(20\d{2})',
        r'notes?\s+(20\d{2})',
        r'debentures?\s+(20\d{2})',
        r'\b(20\d{2})\s*notes?',
    ]

    for pattern in patterns:
        match = re.search(pattern, name, re.IGNORECASE)
        if match:
            year = int(match.group(1))
            if 2020 <= year <= 2100:  # Sanity check
                return year

    return None


async def fix_maturity_dates(dry_run: bool = True):
    """Fix missing maturity dates by extracting from names."""

    async with async_session_maker() as session:
        print("=" * 70)
        print("FIX MISSING MATURITY DATES")
        print("=" * 70)
        print(f"Mode: {'DRY RUN' if dry_run else 'SAVE'}")
        print()

        # Get instruments with NULL maturity but have names that might contain dates
        result = await session.execute(text('''
            SELECT id, name, instrument_type, interest_rate
            FROM debt_instruments
            WHERE is_active = true
              AND maturity_date IS NULL
              AND name IS NOT NULL
              AND name != ''
            ORDER BY name
        '''))
        instruments = result.fetchall()

        print(f"Found {len(instruments)} instruments with NULL maturity")
        print()

        fixed = []
        cannot_fix = []

        for inst in instruments:
            inst_id, name, inst_type, rate = inst
            year = extract_maturity_year(name)

            if year:
                # Default to December 31 of the year if no specific date
                maturity_date = date(year, 12, 31)
                fixed.append({
                    'id': inst_id,
                    'name': name,
                    'year': year,
                    'maturity_date': maturity_date
                })
            else:
                cannot_fix.append({'name': name, 'type': inst_type})

        print(f"CAN FIX: {len(fixed)} instruments")
        print("-" * 70)
        for item in fixed[:20]:
            print(f"  {item['name'][:50]} -> {item['year']}")
        if len(fixed) > 20:
            print(f"  ... and {len(fixed) - 20} more")

        print()
        print(f"CANNOT FIX: {len(cannot_fix)} instruments")
        print("-" * 70)
        for item in cannot_fix[:10]:
            print(f"  {item['name'][:60]}")
        if len(cannot_fix) > 10:
            print(f"  ... and {len(cannot_fix) - 10} more")

        # Save if not dry run
        if not dry_run and fixed:
            print()
            print("Saving changes...")
            for item in fixed:
                await session.execute(text('''
                    UPDATE debt_instruments
                    SET maturity_date = :maturity
                    WHERE id = :id
                '''), {'maturity': item['maturity_date'], 'id': item['id']})

            await session.commit()
            print(f"Updated {len(fixed)} instruments")

        return {'fixed': len(fixed), 'cannot_fix': len(cannot_fix)}


async def main():
    parser = argparse.ArgumentParser(description="Fix missing maturity dates")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be changed")
    parser.add_argument("--save", action="store_true", help="Save changes")
    args = parser.parse_args()

    if not args.dry_run and not args.save:
        parser.error("Either --dry-run or --save is required")

    result = await fix_maturity_dates(dry_run=not args.save)
    print(f"\nResult: {result}")


if __name__ == "__main__":
    asyncio.run(main())
