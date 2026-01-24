#!/usr/bin/env python3
"""
Fix instruments with missing interest rates by extracting from instrument names.

Many instruments have names like "4.50% Senior Notes due 2030" but interest_rate is NULL.
This script extracts the rate from the name and sets it.

Usage:
    python scripts/fix_missing_interest_rates.py --dry-run
    python scripts/fix_missing_interest_rates.py --save
"""

import argparse
import asyncio
import io
import os
import re
import sys

if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from sqlalchemy import text
from app.core.database import async_session_maker


def extract_interest_rate(name: str) -> float | None:
    """Extract interest rate from instrument name. Returns rate in basis points."""
    if not name:
        return None

    # Patterns for fixed rates like "4.50%" or "4.5%"
    patterns = [
        r'(\d+\.?\d*)\s*%\s*(?:Senior|Notes|Debentures|Bonds)',  # 4.50% Senior Notes
        r'Fixed-rate\s+(\d+\.?\d*)\s*%',  # Fixed-rate 3.300%
        r'^(\d+\.?\d*)\s*%',  # Starts with rate like "5.50% January 15, 2040"
        r'(\d+\.?\d*)\s*%\s*(?:due|notes|bonds|debentures)',  # 4.50% due 2030
        r'(\d+\.?\d*)\s*%\s+\d{4}',  # 4.50% 2030
    ]

    for pattern in patterns:
        match = re.search(pattern, name, re.IGNORECASE)
        if match:
            rate = float(match.group(1))
            # Sanity check: rates should be between 0.1% and 20%
            if 0.1 <= rate <= 20:
                # Convert to basis points (multiply by 100)
                return int(rate * 100)

    return None


async def fix_interest_rates(dry_run: bool = True):
    """Fix missing interest rates by extracting from names."""

    async with async_session_maker() as session:
        print("=" * 70)
        print("FIX MISSING INTEREST RATES")
        print("=" * 70)
        print(f"Mode: {'DRY RUN' if dry_run else 'SAVE'}")
        print()

        # Get instruments with NULL interest_rate but have names that might contain rates
        result = await session.execute(text('''
            SELECT id, name, instrument_type, maturity_date
            FROM debt_instruments
            WHERE is_active = true
              AND interest_rate IS NULL
              AND name IS NOT NULL
              AND name != ''
              AND name LIKE '%!%%' ESCAPE '!'
            ORDER BY name
        '''))
        instruments = result.fetchall()

        print(f"Found {len(instruments)} instruments with NULL rate and '%' in name")
        print()

        fixed = []
        cannot_fix = []

        for inst in instruments:
            inst_id, name, inst_type, maturity = inst
            rate_bps = extract_interest_rate(name)

            if rate_bps:
                fixed.append({
                    'id': inst_id,
                    'name': name,
                    'rate_bps': rate_bps,
                    'rate_pct': rate_bps / 100
                })
            else:
                cannot_fix.append({'name': name, 'type': inst_type})

        print(f"CAN FIX: {len(fixed)} instruments")
        print("-" * 70)
        for item in fixed[:20]:
            print(f"  {item['name'][:50]} -> {item['rate_pct']:.2f}% ({item['rate_bps']} bps)")
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
                    SET interest_rate = :rate
                    WHERE id = :id
                '''), {'rate': item['rate_bps'], 'id': item['id']})

            await session.commit()
            print(f"Updated {len(fixed)} instruments")

        return {'fixed': len(fixed), 'cannot_fix': len(cannot_fix)}


async def main():
    parser = argparse.ArgumentParser(description="Fix missing interest rates")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be changed")
    parser.add_argument("--save", action="store_true", help="Save changes")
    args = parser.parse_args()

    if not args.dry_run and not args.save:
        parser.error("Either --dry-run or --save is required")

    result = await fix_interest_rates(dry_run=not args.save)
    print(f"\nResult: {result}")


if __name__ == "__main__":
    asyncio.run(main())
