#!/usr/bin/env python3
"""
Backfill outstanding amounts from Finnhub bond profile API.

This script fetches bond profile data from Finnhub for bonds that have ISINs
but are missing outstanding amounts, and updates the database.

Usage:
    python scripts/backfill_finnhub_amounts.py [--ticker TICKER] [--limit N] [--dry-run]
"""

import argparse
import asyncio
import os
import sys

# Add parent directory to path for imports (must be before app imports)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv
load_dotenv()

# Set up API key BEFORE importing bond_pricing
from app.core.config import get_settings
_settings = get_settings()
if _settings.finnhub_api_key:
    os.environ["FINNHUB_API_KEY"] = _settings.finnhub_api_key

from sqlalchemy import select

from script_utils import get_db_session, print_header, run_async
from app.models import Company, DebtInstrument
from app.services.bond_pricing import fetch_finnhub_bond_profile, FINNHUB_API_KEY, REQUEST_DELAY


async def backfill_amounts(
    ticker: str = None,
    limit: int = 100,
    dry_run: bool = False,
    include_with_amounts: bool = False,
):
    """
    Backfill outstanding amounts from Finnhub for bonds with ISINs.

    Args:
        ticker: Optional ticker to filter to a specific company
        limit: Maximum number of bonds to process
        dry_run: If True, don't save changes to database
        include_with_amounts: If True, also update bonds that already have amounts
    """
    if not FINNHUB_API_KEY:
        print("ERROR: FINNHUB_API_KEY environment variable not set")
        return

    print_header("BACKFILL FINNHUB AMOUNTS")

    async with get_db_session() as db:
        # Build query for bonds with ISINs
        query = (
            select(DebtInstrument, Company.ticker)
            .join(Company, Company.id == DebtInstrument.company_id)
            .where(DebtInstrument.is_active == True)
            .where(DebtInstrument.isin.isnot(None))
        )

        if not include_with_amounts:
            # Only bonds missing outstanding amounts
            query = query.where(DebtInstrument.outstanding.is_(None))

        if ticker:
            query = query.where(Company.ticker == ticker.upper())

        query = query.order_by(Company.ticker, DebtInstrument.maturity_date).limit(limit)

        result = await db.execute(query)
        bonds = result.fetchall()

        print(f"Found {len(bonds)} bonds with ISINs" +
              (" (missing amounts)" if not include_with_amounts else ""))

        if dry_run:
            print("DRY RUN - no changes will be saved")

        print()

        # Stats
        stats = {
            "processed": 0,
            "updated": 0,
            "no_data": 0,
            "errors": 0,
            "skipped": 0,
        }

        current_ticker = None

        for bond, bond_ticker in bonds:
            # Print company header
            if bond_ticker != current_ticker:
                current_ticker = bond_ticker
                print(f"\n=== {current_ticker} ===")

            stats["processed"] += 1

            # Fetch profile from Finnhub
            profile = await fetch_finnhub_bond_profile(bond.isin)

            if profile.error:
                print(f"  [{bond.isin}] ERROR: {profile.error}")
                stats["errors"] += 1

                # If rate limited, wait longer
                if "rate limit" in profile.error.lower():
                    print("  Rate limited, waiting 60 seconds...")
                    await asyncio.sleep(60)
                continue

            if not profile.amount_outstanding and not profile.original_offering:
                print(f"  [{bond.isin}] No amount data available")
                stats["no_data"] += 1
                await asyncio.sleep(REQUEST_DELAY)
                continue

            # Determine which amount to use
            # Prefer amount_outstanding, fall back to original_offering
            amount_dollars = profile.amount_outstanding or profile.original_offering
            amount_cents = amount_dollars * 100 if amount_dollars else None

            # Check if update is needed
            if bond.outstanding == amount_cents:
                print(f"  [{bond.isin}] Already up to date: ${amount_dollars:,.0f}")
                stats["skipped"] += 1
                await asyncio.sleep(REQUEST_DELAY)
                continue

            # Format for display
            old_amount = f"${bond.outstanding/100:,.0f}" if bond.outstanding else "None"
            new_amount = f"${amount_dollars:,.0f}" if amount_dollars else "None"
            source = "outstanding" if profile.amount_outstanding else "original"

            bond_name = bond.name[:40] if bond.name else "Unknown"
            print(f"  [{bond.isin}] {bond_name}")
            print(f"      {old_amount} -> {new_amount} (from {source})")

            if not dry_run:
                # Update the bond
                bond.outstanding = amount_cents
                if not bond.principal and profile.original_offering:
                    bond.principal = profile.original_offering * 100

                # Also update CUSIP if we got one and don't have it
                if profile.cusip and not bond.cusip:
                    bond.cusip = profile.cusip

                await db.commit()

            stats["updated"] += 1

            # Rate limit
            await asyncio.sleep(REQUEST_DELAY)

        print("\n" + "=" * 60)
        print("SUMMARY")
        print("=" * 60)
        print(f"Processed:  {stats['processed']}")
        print(f"Updated:    {stats['updated']}")
        print(f"No data:    {stats['no_data']}")
        print(f"Errors:     {stats['errors']}")
        print(f"Skipped:    {stats['skipped']}")

        if dry_run:
            print("\n(DRY RUN - no changes were saved)")


async def check_finnhub_access():
    """Test Finnhub API access with a sample ISIN."""
    print("Testing Finnhub API access...")

    # Use a known Apple bond ISIN
    test_isin = "US037833EP27"
    profile = await fetch_finnhub_bond_profile(test_isin)

    if profile.error:
        print(f"ERROR: {profile.error}")
        if "premium" in profile.error.lower():
            print("\nFinnhub bond data requires a premium subscription.")
            print("See: https://finnhub.io/pricing")
        return False

    print(f"SUCCESS! Retrieved profile for {test_isin}")
    print(f"  Amount Outstanding: ${profile.amount_outstanding:,.0f}" if profile.amount_outstanding else "  Amount Outstanding: N/A")
    print(f"  Original Offering:  ${profile.original_offering:,.0f}" if profile.original_offering else "  Original Offering: N/A")
    print(f"  Coupon: {profile.coupon}%")
    print(f"  Maturity: {profile.maturity_date}")
    return True


async def main():
    parser = argparse.ArgumentParser(description="Backfill outstanding amounts from Finnhub")
    parser.add_argument("--ticker", help="Process only bonds for this ticker")
    parser.add_argument("--limit", type=int, default=100, help="Maximum bonds to process")
    parser.add_argument("--dry-run", action="store_true", help="Don't save changes")
    parser.add_argument("--include-with-amounts", action="store_true",
                       help="Also update bonds that already have amounts")
    parser.add_argument("--test", action="store_true", help="Test Finnhub API access")
    args = parser.parse_args()

    if args.test:
        await check_finnhub_access()
        return

    await backfill_amounts(
        ticker=args.ticker,
        limit=args.limit,
        dry_run=args.dry_run,
        include_with_amounts=args.include_with_amounts,
    )


if __name__ == "__main__":
    run_async(main())
