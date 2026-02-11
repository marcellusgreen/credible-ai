#!/usr/bin/env python3
"""
Clean up duplicate debt instruments for travel/airline companies.

This script identifies and deactivates duplicate instruments based on:
1. Zero-value "bond" type duplicates from Feb 4 extraction
2. Duplicate notes with same coupon/maturity but different naming
3. Inactive instruments that should remain inactive
"""

import asyncio
import os
import sys
from dotenv import load_dotenv
load_dotenv()

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker


async def cleanup_zero_value_bonds(session, ticker: str) -> int:
    """Deactivate bond-type instruments with zero outstanding that are likely duplicates."""
    result = await session.execute(text('''
        UPDATE debt_instruments di
        SET is_active = false
        FROM companies c
        WHERE c.id = di.company_id
        AND c.ticker = :ticker
        AND di.is_active = true
        AND di.instrument_type = 'bond'
        AND (di.outstanding IS NULL OR di.outstanding = 0)
        RETURNING di.id
    '''), {'ticker': ticker})
    deactivated = result.fetchall()
    return len(deactivated)


async def find_duplicate_notes(session, ticker: str) -> list:
    """Find notes that appear to be duplicates based on coupon/maturity patterns."""
    import re

    result = await session.execute(text('''
        SELECT
            di.id,
            di.name,
            di.outstanding,
            di.maturity_date,
            di.created_at
        FROM debt_instruments di
        JOIN companies c ON c.id = di.company_id
        WHERE c.ticker = :ticker AND di.is_active = true
        AND di.instrument_type IN ('senior_notes', 'senior_secured_notes', 'subordinated_notes', 'convertible_notes')
        ORDER BY di.name
    '''), {'ticker': ticker})

    notes = result.fetchall()

    # Group by extracted coupon/year
    groups = {}
    for note in notes:
        name = note[1] or ''
        # Extract coupon rate
        coupon_match = re.search(r'(\d+\.?\d*)%', name)
        # Extract year
        year_match = re.search(r'20(\d{2})', name)

        if coupon_match and year_match:
            key = (coupon_match.group(1), year_match.group(1))
            if key not in groups:
                groups[key] = []
            groups[key].append(note)

    # Find duplicates (groups with more than one note)
    duplicates = []
    for key, notes in groups.items():
        if len(notes) > 1:
            # Sort by created_at, keep the oldest, mark others as duplicates
            sorted_notes = sorted(notes, key=lambda x: x[4])
            for dup in sorted_notes[1:]:
                duplicates.append(dup)

    return duplicates


async def deactivate_duplicates(session, duplicate_ids: list) -> int:
    """Deactivate the specified duplicate instruments."""
    if not duplicate_ids:
        return 0

    result = await session.execute(text('''
        UPDATE debt_instruments
        SET is_active = false
        WHERE id = ANY(:ids)
        RETURNING id
    '''), {'ids': duplicate_ids})
    return len(result.fetchall())


async def get_company_status(session, ticker: str) -> dict:
    """Get current status of a company's debt coverage."""
    result = await session.execute(text('''
        SELECT
            c.id,
            c.name,
            (SELECT total_debt / 100 / 1e9 FROM company_financials cf
             WHERE cf.company_id = c.id
             ORDER BY fiscal_year DESC, fiscal_quarter DESC LIMIT 1) as fin_total,
            COUNT(di.id) as inst_count,
            SUM(COALESCE(di.outstanding, 0)) / 100 / 1e9 as inst_total,
            SUM(CASE WHEN bp.price_source = 'TRACE' THEN 1 ELSE 0 END) as trace_count
        FROM companies c
        LEFT JOIN debt_instruments di ON di.company_id = c.id AND di.is_active = true
        LEFT JOIN bond_pricing bp ON bp.debt_instrument_id = di.id
        WHERE c.ticker = :ticker
        GROUP BY c.id, c.name
    '''), {'ticker': ticker})

    row = result.fetchone()
    if not row:
        return None

    fin_total = float(row[2]) if row[2] else 0
    inst_total = float(row[4]) if row[4] else 0
    gap_pct = ((inst_total - fin_total) / fin_total * 100) if fin_total > 0 else 0

    return {
        'ticker': ticker,
        'name': row[1],
        'fin_total': fin_total,
        'inst_count': row[3],
        'inst_total': inst_total,
        'gap_pct': gap_pct,
        'trace_count': row[5]
    }


async def cleanup_company(session, ticker: str, dry_run: bool = False) -> dict:
    """Clean up duplicates for a single company."""
    print(f'\n{"="*60}')
    print(f'Processing {ticker}...')

    # Get initial status
    before = await get_company_status(session, ticker)
    if not before:
        return {'ticker': ticker, 'status': 'NOT_FOUND'}

    print(f'  Before: {before["inst_count"]} instruments, ${before["inst_total"]:.2f}B (gap: {before["gap_pct"]:+.1f}%)')

    # Step 1: Clean up zero-value bonds
    if not dry_run:
        zero_bonds = await cleanup_zero_value_bonds(session, ticker)
        print(f'  Deactivated {zero_bonds} zero-value bond duplicates')
    else:
        zero_bonds = 0

    # Step 2: Find and clean duplicate notes
    duplicates = await find_duplicate_notes(session, ticker)
    if duplicates:
        print(f'  Found {len(duplicates)} duplicate notes:')
        for dup in duplicates[:5]:
            print(f'    - {dup[1][:50]}')
        if len(duplicates) > 5:
            print(f'    ... and {len(duplicates) - 5} more')

        if not dry_run:
            dup_ids = [str(d[0]) for d in duplicates]
            deactivated = await deactivate_duplicates(session, dup_ids)
            print(f'  Deactivated {deactivated} duplicate notes')

    if not dry_run:
        await session.commit()

    # Get final status
    after = await get_company_status(session, ticker)
    print(f'  After: {after["inst_count"]} instruments, ${after["inst_total"]:.2f}B (gap: {after["gap_pct"]:+.1f}%)')
    print(f'  TRACE pricing preserved: {after["trace_count"]} bonds')

    return {
        'ticker': ticker,
        'before': before,
        'after': after,
        'zero_bonds_removed': zero_bonds,
        'duplicates_removed': len(duplicates) if duplicates else 0
    }


async def main():
    import argparse
    parser = argparse.ArgumentParser(description='Clean up duplicate debt instruments')
    parser.add_argument('--ticker', type=str, help='Process single company')
    parser.add_argument('--all-travel', action='store_true', help='Process all travel/airline companies')
    parser.add_argument('--dry-run', action='store_true', help='Show what would be done without making changes')
    args = parser.parse_args()

    database_url = os.getenv('DATABASE_URL')
    if not database_url:
        print('Error: DATABASE_URL required')
        sys.exit(1)

    engine = create_async_engine(database_url, echo=False)
    async_session = async_sessionmaker(engine, expire_on_commit=False)

    travel_tickers = ['AAL', 'UAL', 'DAL', 'CCL', 'RCL', 'NCLH', 'BKNG', 'MAR']

    async with async_session() as session:
        if args.ticker:
            tickers = [args.ticker.upper()]
        elif args.all_travel:
            tickers = travel_tickers
        else:
            print('Usage: python cleanup_travel_duplicates.py [--ticker TICKER | --all-travel] [--dry-run]')
            await engine.dispose()
            return

        print(f'Processing {len(tickers)} companies...')
        if args.dry_run:
            print('DRY RUN - no changes will be made')

        results = []
        for ticker in tickers:
            result = await cleanup_company(session, ticker, dry_run=args.dry_run)
            results.append(result)

        # Summary
        print('\n' + '='*60)
        print('SUMMARY')
        print('='*60)
        for r in results:
            if 'after' in r:
                improvement = r['before']['gap_pct'] - r['after']['gap_pct']
                print(f'{r["ticker"]}: Gap {r["before"]["gap_pct"]:+.1f}% -> {r["after"]["gap_pct"]:+.1f}% '
                      f'(improved by {abs(improvement):.1f}pp)')

    await engine.dispose()


if __name__ == '__main__':
    asyncio.run(main())
