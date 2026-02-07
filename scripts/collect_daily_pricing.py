#!/usr/bin/env python3
"""
Collect Daily Bond Pricing

This script runs as a daily cron job to:
1. Update current prices in bond_pricing table (for all tiers)
2. Save end-of-day snapshot to bond_pricing_history (for Business tier historical data)

Recommended cron schedule: 9:00 PM ET (after 4-hour TRACE delay clears)
    0 21 * * 1-5 python scripts/collect_daily_pricing.py

Usage:
    python scripts/collect_daily_pricing.py
    python scripts/collect_daily_pricing.py --current-only
    python scripts/collect_daily_pricing.py --history-only
    python scripts/collect_daily_pricing.py --dry-run
    python scripts/collect_daily_pricing.py --ticker CHTR
"""

import argparse
import asyncio
import sys
from datetime import datetime

from script_utils import (
    get_db_session,
    print_header,
    run_async,
)

from app.services.pricing_history import copy_current_to_history
from app.services.bond_pricing import (
    get_bonds_needing_pricing,
    get_bond_price,
    save_bond_pricing,
    REQUEST_DELAY,
)
from app.services.yield_calculation import calculate_ytm_and_spread


async def update_current_prices(
    session,
    ticker: str = None,
    dry_run: bool = False,
) -> dict:
    """Update current prices in bond_pricing table."""
    # Get bonds needing updates
    bonds = await get_bonds_needing_pricing(
        session,
        ticker=ticker,
        stale_only=True,
        stale_days=0,  # Update all
        limit=1000,
    )

    stats = {
        "bonds_checked": len(bonds),
        "prices_updated": 0,
        "prices_failed": 0,
        "yields_calculated": 0,
    }

    for bond in bonds:
        price = await get_bond_price(
            cusip=bond.cusip,
            isin=bond.isin,
            coupon_rate_pct=bond.interest_rate / 100 if bond.interest_rate else None,
            maturity_date=bond.maturity_date,
        )

        if price.last_price:
            stats["prices_updated"] += 1

            # Calculate yield
            ytm_bps = None
            spread_bps = None
            benchmark = None

            if bond.interest_rate and bond.maturity_date:
                try:
                    ytm_bps, spread_bps, benchmark = await calculate_ytm_and_spread(
                        price=float(price.last_price),
                        coupon_rate=bond.interest_rate / 100,
                        maturity_date=bond.maturity_date,
                    )
                    stats["yields_calculated"] += 1
                except Exception:
                    pass

            if not dry_run:
                await save_bond_pricing(
                    session=session,
                    debt_instrument_id=bond.id,
                    cusip=bond.cusip,
                    price=price,
                    ytm_bps=ytm_bps,
                    spread_bps=spread_bps,
                    treasury_benchmark=benchmark,
                )
        else:
            stats["prices_failed"] += 1

        # Rate limit
        await asyncio.sleep(REQUEST_DELAY)

    return stats


async def main():
    parser = argparse.ArgumentParser(
        description="Collect daily bond pricing (cron job)"
    )
    parser.add_argument("--ticker", help="Process single company")
    parser.add_argument("--current-only", action="store_true",
                        help="Only update current prices, don't save to history")
    parser.add_argument("--history-only", action="store_true",
                        help="Only save to history, don't fetch new prices")
    parser.add_argument("--dry-run", action="store_true",
                        help="Don't save to database")
    parser.add_argument("--date", help="Date for history snapshot (YYYY-MM-DD)")

    args = parser.parse_args()

    # Parse date if provided
    snapshot_date = None
    if args.date:
        try:
            snapshot_date = datetime.strptime(args.date, "%Y-%m-%d").date()
        except ValueError:
            print(f"Error: Invalid date format: {args.date}")
            sys.exit(1)

    print_header("DAILY BOND PRICING COLLECTION")
    print(f"Time: {datetime.now().isoformat()}")
    print(f"Dry run: {args.dry_run}")
    if args.ticker:
        print(f"Ticker: {args.ticker}")
    print()

    async with get_db_session() as session:
        # Step 1: Update current prices (unless --history-only)
        if not args.history_only:
            print("Step 1: Updating current prices...")
            current_stats = await update_current_prices(
                session,
                ticker=args.ticker,
                dry_run=args.dry_run,
            )
            print(f"  Bonds checked:     {current_stats['bonds_checked']}")
            print(f"  Prices updated:    {current_stats['prices_updated']}")
            print(f"  Prices failed:     {current_stats['prices_failed']}")
            print(f"  Yields calculated: {current_stats['yields_calculated']}")
            print()

        # Step 2: Copy to history (unless --current-only)
        if not args.current_only:
            print("Step 2: Saving to history...")
            history_stats = await copy_current_to_history(
                session,
                price_date=snapshot_date,
                dry_run=args.dry_run,
            )
            print(f"  Current prices:    {history_stats.total_current}")
            print(f"  Copied to history: {history_stats.copied}")
            print(f"  Skipped existing:  {history_stats.skipped_existing}")
            print(f"  Errors:            {history_stats.errors}")
            print()

    print("=" * 60)
    print("COMPLETE")
    print("=" * 60)

    if args.dry_run:
        print("\n[DRY RUN - No data was saved]")


if __name__ == "__main__":
    run_async(main())
