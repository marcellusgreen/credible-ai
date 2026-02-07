#!/usr/bin/env python3
"""
Deduplicate debt instruments by keeping the best record.

Identifies duplicate instruments (same company, name, maturity, rate) and keeps
the one with the most complete data (has amount, has CUSIP, etc.).

Usage:
    python scripts/deduplicate_instruments.py --dry-run
    python scripts/deduplicate_instruments.py --execute
"""

import argparse

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from script_utils import get_db_session, print_header, run_async


async def find_duplicates(db: AsyncSession) -> list[dict]:
    """Find all duplicate instrument sets."""
    result = await db.execute(text('''
        WITH dup_groups AS (
            SELECT company_id, name, maturity_date, interest_rate,
                   array_agg(id ORDER BY
                       -- Score: prefer records with more data
                       (CASE WHEN outstanding IS NOT NULL THEN 4 ELSE 0 END) +
                       (CASE WHEN cusip IS NOT NULL THEN 2 ELSE 0 END) +
                       (CASE WHEN isin IS NOT NULL THEN 1 ELSE 0 END)
                       DESC,
                       created_at ASC
                   ) as ids,
                   COUNT(*) as cnt
            FROM debt_instruments
            WHERE is_active = true
            GROUP BY company_id, name, maturity_date, interest_rate
            HAVING COUNT(*) > 1
        )
        SELECT dg.company_id, c.ticker, dg.name, dg.maturity_date, dg.interest_rate,
               dg.ids, dg.cnt
        FROM dup_groups dg
        JOIN companies c ON c.id = dg.company_id
        ORDER BY dg.cnt DESC, c.ticker
    '''))

    duplicates = []
    for row in result.fetchall():
        duplicates.append({
            'company_id': row[0],
            'ticker': row[1],
            'name': row[2] or '(empty)',
            'maturity': row[3],
            'rate': row[4],
            'ids': row[5],  # First ID is the one to keep
            'count': row[6]
        })

    return duplicates


async def get_instrument_details(db: AsyncSession, instrument_id: str) -> dict:
    """Get details for a single instrument."""
    result = await db.execute(text('''
        SELECT id, name, outstanding, cusip, isin, source_document_id, created_at
        FROM debt_instruments
        WHERE id = :id
    '''), {'id': instrument_id})
    row = result.fetchone()
    if row:
        return {
            'id': str(row[0]),
            'name': row[1],
            'outstanding': row[2],
            'cusip': row[3],
            'isin': row[4],
            'has_doc': row[5] is not None,
            'created': row[6]
        }
    return {}


async def deduplicate(db: AsyncSession, duplicates: list[dict], execute: bool = False) -> dict:
    """Remove duplicate instruments, keeping the best one."""
    stats = {
        'duplicate_sets': len(duplicates),
        'instruments_removed': 0,
        'by_ticker': {}
    }

    for dup in duplicates:
        ticker = dup['ticker']
        ids = dup['ids']
        keep_id = ids[0]  # First ID has best score
        remove_ids = ids[1:]  # Rest will be removed

        if ticker not in stats['by_ticker']:
            stats['by_ticker'][ticker] = 0
        stats['by_ticker'][ticker] += len(remove_ids)
        stats['instruments_removed'] += len(remove_ids)

        rate_str = f"{dup['rate']/100:.2f}%" if dup['rate'] else 'N/A'
        print(f"  {ticker}: \"{dup['name'][:40]}\" (rate={rate_str}, maturity={dup['maturity']}) - keeping 1, removing {len(remove_ids)}")

        if execute:
            # Handle guarantees - delete from removed instruments (they're duplicates)
            # Guarantees are duplicated too, so we just delete the ones pointing to removed instruments
            for remove_id in remove_ids:
                await db.execute(text('''
                    DELETE FROM guarantees
                    WHERE debt_instrument_id = :remove_id
                '''), {'remove_id': remove_id})

            # Delete from debt_instrument_documents too
            await db.execute(text('''
                DELETE FROM debt_instrument_documents
                WHERE debt_instrument_id = ANY(:ids)
            '''), {'ids': remove_ids})

            # Delete from collateral
            await db.execute(text('''
                DELETE FROM collateral
                WHERE debt_instrument_id = ANY(:ids)
            '''), {'ids': remove_ids})

            # Delete from bond_pricing
            await db.execute(text('''
                DELETE FROM bond_pricing
                WHERE debt_instrument_id = ANY(:ids)
            '''), {'ids': remove_ids})

            # Then mark duplicates as inactive (safer than delete)
            await db.execute(text('''
                UPDATE debt_instruments
                SET is_active = false,
                    attributes = COALESCE(attributes, '{}'::jsonb) || '{"deduplicated": true}'::jsonb
                WHERE id = ANY(:ids)
            '''), {'ids': remove_ids})

    if execute:
        await db.commit()

    return stats


async def main():
    parser = argparse.ArgumentParser(description="Deduplicate debt instruments")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be done (default)")
    parser.add_argument("--execute", action="store_true", help="Actually remove duplicates")
    parser.add_argument("--ticker", type=str, help="Process single company")
    args = parser.parse_args()

    if not args.execute:
        args.dry_run = True

    print_header("DEDUPLICATE DEBT INSTRUMENTS")
    print(f"Mode: {'EXECUTE' if args.execute else 'DRY RUN'}")

    async with get_db_session() as db:
        duplicates = await find_duplicates(db)

        if args.ticker:
            duplicates = [d for d in duplicates if d['ticker'] == args.ticker.upper()]

        print(f"\nFound {len(duplicates)} duplicate sets")

        if not duplicates:
            print("No duplicates to process")
            return

        print("\nProcessing duplicates:")
        stats = await deduplicate(db, duplicates, execute=args.execute)

        print("\n" + "=" * 70)
        print("SUMMARY")
        print("=" * 70)
        print(f"Duplicate sets processed: {stats['duplicate_sets']}")
        print(f"Instruments {'removed' if args.execute else 'would be removed'}: {stats['instruments_removed']}")
        print("\nBy company:")
        for ticker, count in sorted(stats['by_ticker'].items(), key=lambda x: -x[1]):
            print(f"  {ticker}: {count}")

        if not args.execute:
            print("\n[DRY RUN] Use --execute to apply changes")


if __name__ == "__main__":
    run_async(main())
