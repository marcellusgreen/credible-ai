#!/usr/bin/env python3
"""
Backfill Historical Treasury Yields from Treasury.gov

This script populates the treasury_yield_history table with historical
US Treasury yield curve data for accurate spread calculations.

Data source: US Treasury.gov (free, public data)
Benchmarks: 1M, 3M, 6M, 1Y, 2Y, 3Y, 5Y, 7Y, 10Y, 20Y, 30Y

Usage:
    python scripts/backfill_treasury_yields.py --stats
    python scripts/backfill_treasury_yields.py --years 2023 2024 2025 2026
    python scripts/backfill_treasury_yields.py --from-year 2021 --to-year 2026
    python scripts/backfill_treasury_yields.py --dry-run
"""

import argparse
import asyncio
import os
import sys
from datetime import date

from dotenv import load_dotenv
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.treasury_yields import (
    backfill_treasury_yields,
    get_treasury_yield_stats,
    BENCHMARKS,
)

load_dotenv()


async def main():
    parser = argparse.ArgumentParser(
        description="Backfill historical treasury yields from Treasury.gov"
    )
    parser.add_argument("--years", type=int, nargs="+",
                        help="Specific years to fetch (e.g., --years 2023 2024 2025)")
    parser.add_argument("--from-year", type=int,
                        help="Start year for range (default: 3 years ago)")
    parser.add_argument("--to-year", type=int,
                        help="End year for range (default: current year)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Don't save to database")
    parser.add_argument("--stats", action="store_true",
                        help="Show current statistics only")

    args = parser.parse_args()

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
            stats = await get_treasury_yield_stats(session)

        print(f"\n{'='*60}")
        print("TREASURY YIELD HISTORY STATISTICS")
        print(f"{'='*60}")
        print(f"Total records:      {stats.total_records:,}")
        print(f"Unique dates:       {stats.unique_dates:,}")
        if stats.min_date:
            print(f"Date range:         {stats.min_date} to {stats.max_date}")
        else:
            print("Date range:         No data yet")
        print(f"Benchmarks:         {', '.join(stats.benchmarks_covered) if stats.benchmarks_covered else 'None'}")
        print(f"Expected benchmarks: {', '.join(BENCHMARKS)}")

        await engine.dispose()
        return

    # Determine year range
    current_year = date.today().year

    if args.years:
        from_year = min(args.years)
        to_year = max(args.years)
    else:
        from_year = args.from_year or (current_year - 3)
        to_year = args.to_year or current_year

    print(f"\n{'='*60}")
    print("BACKFILL TREASURY YIELDS")
    print(f"{'='*60}")
    print(f"Years: {from_year} to {to_year}")
    print(f"Dry run: {args.dry_run}")
    print()

    async with async_session() as session:
        stats = await backfill_treasury_yields(
            session,
            from_year=from_year,
            to_year=to_year,
            dry_run=args.dry_run,
        )

    await engine.dispose()

    # Summary
    print(f"\n{'='*60}")
    print("SUMMARY")
    print(f"{'='*60}")
    print(f"Years processed:    {stats['years_processed']}")
    print(f"Total yields found: {stats['total_yields']:,}")
    print(f"Records saved:      {stats['saved']:,}")

    if stats["errors"]:
        print(f"\nErrors ({len(stats['errors'])}):")
        for err in stats["errors"]:
            print(f"  - {err}")

    if args.dry_run:
        print("\n[DRY RUN - No data was saved]")


if __name__ == "__main__":
    asyncio.run(main())
