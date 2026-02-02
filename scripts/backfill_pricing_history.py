#!/usr/bin/env python3
"""
Backfill Historical Bond Pricing from Finnhub TRACE Data

This script populates the bond_pricing_history table with historical daily
prices going back up to 3 years (the limit of FINRA TRACE intraday data).

Usage:
    python scripts/backfill_pricing_history.py --stats
    python scripts/backfill_pricing_history.py --all --dry-run
    python scripts/backfill_pricing_history.py --all
    python scripts/backfill_pricing_history.py --ticker CHTR
    python scripts/backfill_pricing_history.py --all --days 365
    python scripts/backfill_pricing_history.py --all --resume-from US123456789
    python scripts/backfill_pricing_history.py --all --skip-yields  # Faster, no YTM calc

Rate Limits:
    Finnhub premium has 300 API calls/minute. With 0.25s delay between
    requests, we make ~240 calls/minute, staying safely under the limit.
"""

import argparse
import asyncio
import os
import sys
from datetime import date, timedelta

import httpx
from dotenv import load_dotenv
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.pricing_history import (
    get_bonds_with_isin,
    get_pricing_history_stats,
    backfill_bond_history,
    DEFAULT_BACKFILL_DAYS,
)
from app.services.bond_pricing import FINNHUB_API_KEY, REQUEST_DELAY
from app.services.treasury_yields import get_treasury_yield_stats

load_dotenv()


async def main():
    parser = argparse.ArgumentParser(
        description="Backfill historical bond pricing from Finnhub TRACE data"
    )
    parser.add_argument("--ticker", help="Process single company")
    parser.add_argument("--all", action="store_true", help="Process all bonds with ISINs")
    parser.add_argument("--days", type=int, default=DEFAULT_BACKFILL_DAYS,
                        help=f"Days of history to fetch (default: {DEFAULT_BACKFILL_DAYS})")
    parser.add_argument("--limit", type=int, help="Limit number of bonds to process")
    parser.add_argument("--dry-run", action="store_true", help="Don't save to database")
    parser.add_argument("--resume-from", help="Resume from specific ISIN")
    parser.add_argument("--skip-yields", action="store_true",
                        help="Skip YTM calculation (faster backfill)")
    parser.add_argument("--with-spreads", action="store_true",
                        help="Calculate spreads using historical treasury yields (requires treasury data)")
    parser.add_argument("--stats", action="store_true", help="Show current statistics only")

    args = parser.parse_args()

    if not args.ticker and not args.all and not args.stats:
        parser.error("Must specify --ticker, --all, or --stats")

    # Check for API key
    if not FINNHUB_API_KEY and not args.stats:
        print("Error: FINNHUB_API_KEY not set in environment")
        sys.exit(1)

    # Database connection
    database_url = os.getenv("DATABASE_URL", "")
    if database_url.startswith("postgres://"):
        database_url = database_url.replace("postgres://", "postgresql+asyncpg://", 1)
    elif database_url.startswith("postgresql://"):
        database_url = database_url.replace("postgresql://", "postgresql+asyncpg://", 1)

    if not database_url:
        print("Error: DATABASE_URL not set")
        sys.exit(1)

    engine = create_async_engine(database_url, echo=False)
    async_session = async_sessionmaker(engine, expire_on_commit=False)

    # Show stats
    if args.stats:
        async with async_session() as session:
            stats = await get_pricing_history_stats(session)

        print(f"\n{'='*60}")
        print("BOND PRICING HISTORY STATISTICS")
        print(f"{'='*60}")
        print(f"Bonds with ISINs:           {stats.isin_count:,}")
        print(f"History records:            {stats.history_count:,}")
        print(f"Instruments with history:   {stats.instruments_with_history:,}")
        print(f"Coverage:                   {stats.instruments_with_history}/{stats.isin_count} ({stats.coverage_pct:.1f}%)")
        if stats.min_date:
            print(f"Date range:                 {stats.min_date} to {stats.max_date}")
        else:
            print("Date range:                 No data yet")

        await engine.dispose()
        return

    # Calculate date range
    to_date = date.today()
    from_date = to_date - timedelta(days=args.days)

    print(f"\n{'='*60}")
    print("BACKFILL HISTORICAL BOND PRICING")
    print(f"{'='*60}")
    print(f"Date range: {from_date} to {to_date} ({args.days} days)")
    print(f"Dry run: {args.dry_run}")
    print(f"Calculate YTM: {not args.skip_yields}")
    print(f"Calculate spreads: {args.with_spreads}")
    if args.resume_from:
        print(f"Resuming from: {args.resume_from}")
    print()

    # Load treasury curves if requested
    treasury_curves = {}
    if args.with_spreads and not args.skip_yields:
        print("Loading historical treasury yields...")
        async with async_session() as session:
            treasury_stats = await get_treasury_yield_stats(session)
            if treasury_stats.total_records == 0:
                print("  WARNING: No treasury yield data found!")
                print("  Run: python scripts/backfill_treasury_yields.py --from-year 2021")
                print("  Continuing without spread calculations...")
            else:
                # Load all treasury curves for the date range
                from app.models import TreasuryYieldHistory
                from sqlalchemy import select
                result = await session.execute(
                    select(TreasuryYieldHistory)
                    .where(TreasuryYieldHistory.yield_date >= from_date)
                    .where(TreasuryYieldHistory.yield_date <= to_date)
                )
                for row in result.scalars().all():
                    if row.yield_date not in treasury_curves:
                        treasury_curves[row.yield_date] = {}
                    treasury_curves[row.yield_date][row.benchmark] = row.yield_pct
                print(f"  Loaded {len(treasury_curves)} days of treasury data")
        print()

    # Get bonds to process
    async with async_session() as session:
        bonds = await get_bonds_with_isin(
            session,
            ticker=args.ticker,
            limit=args.limit,
            resume_from=args.resume_from,
        )

    if not bonds:
        print("No bonds found with ISINs")
        await engine.dispose()
        return

    print(f"Found {len(bonds)} bonds to process")
    print()

    # Process bonds
    totals = {
        "processed": 0,
        "with_data": 0,
        "prices_found": 0,
        "prices_saved": 0,
        "errors": 0,
    }

    async with httpx.AsyncClient(timeout=30.0) as client:
        for i, bond in enumerate(bonds):
            async with async_session() as session:
                result = await backfill_bond_history(
                    session=session,
                    bond=bond,
                    from_date=from_date,
                    to_date=to_date,
                    client=client,
                    dry_run=args.dry_run,
                    calculate_yields=not args.skip_yields,
                    treasury_curves=treasury_curves if args.with_spreads else None,
                )

            totals["processed"] += 1
            totals["prices_found"] += result.prices_found
            totals["prices_saved"] += result.prices_saved

            if result.error:
                totals["errors"] += 1
                status = f"ERROR: {result.error}"
            elif result.prices_found > 0:
                totals["with_data"] += 1
                status = f"Found {result.prices_found} prices, saved {result.prices_saved}"
                if result.skipped_existing > 0:
                    status += f" (skipped {result.skipped_existing} existing)"
            else:
                status = "No data available"

            print(f"[{i+1}/{len(bonds)}] {result.isin}: {status}")

            # Rate limit
            if i < len(bonds) - 1:
                await asyncio.sleep(REQUEST_DELAY)

    await engine.dispose()

    # Summary
    print(f"\n{'='*60}")
    print("SUMMARY")
    print(f"{'='*60}")
    print(f"Bonds processed:      {totals['processed']}")
    print(f"Bonds with data:      {totals['with_data']}")
    print(f"Total prices found:   {totals['prices_found']:,}")
    print(f"Total prices saved:   {totals['prices_saved']:,}")
    print(f"Errors:               {totals['errors']}")

    if args.dry_run:
        print("\n[DRY RUN - No data was saved]")


if __name__ == "__main__":
    asyncio.run(main())
