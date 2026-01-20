"""
Update bond pricing.

Pricing sources (in order of preference):
1. Finnhub API (TRACE data) - requires premium subscription
2. Estimated pricing based on treasury yields + credit spreads

Usage:
    python scripts/update_pricing.py                     # Update all prices
    python scripts/update_pricing.py --ticker AAPL       # Single company
    python scripts/update_pricing.py --stale-only        # Only stale prices
    python scripts/update_pricing.py --summary           # Show pricing summary
"""

import argparse
import asyncio
import os
import sys

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker

load_dotenv()


async def get_session():
    """Create database session."""
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise ValueError("DATABASE_URL not set")

    engine = create_async_engine(database_url)
    async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    return async_session(), engine


def format_price(price) -> str:
    """Format price for display."""
    if price is None:
        return "N/A"
    return f"{float(price):.3f}"


def format_yield(bps) -> str:
    """Format yield for display."""
    if bps is None:
        return "N/A"
    return f"{bps / 100:.2f}%"


def format_spread(bps, benchmark) -> str:
    """Format spread for display."""
    if bps is None:
        return "N/A"
    sign = "+" if bps >= 0 else ""
    return f"{sign}{bps}bps over {benchmark or 'TSY'}"


async def update_single_company(ticker: str):
    """Update pricing for a single company."""
    from app.services.bond_pricing import update_company_pricing

    print(f"\n{'='*60}")
    print(f"Updating pricing for {ticker}")
    print(f"{'='*60}")

    session, engine = await get_session()
    try:
        results = await update_company_pricing(session, ticker)

        print(f"\nResults:")
        print("-" * 60)
        print(f"  Bonds checked:      {results['bonds_checked']}")
        print(f"  Prices found (TRACE): {results.get('prices_found', 0)}")
        print(f"  Prices estimated:   {results.get('prices_estimated', 0)}")
        print(f"  Prices failed:      {results.get('prices_failed', 0)}")
        print(f"  Yields calculated:  {results['yields_calculated']}")

    finally:
        await session.close()
        await engine.dispose()


async def update_all(stale_only: bool = True, stale_days: int = 1, limit: int = 100):
    """Update pricing for all bonds."""
    from app.services.bond_pricing import (
        get_bonds_needing_pricing,
        get_bond_price,
        save_bond_pricing,
        REQUEST_DELAY,
    )
    from app.services.yield_calculation import calculate_ytm_and_spread

    print(f"\n{'='*60}")
    print(f"Updating pricing for all bonds")
    if stale_only:
        print(f"Mode: Stale only (>{stale_days} days)")
    else:
        print(f"Mode: All bonds (limit: {limit})")
    print(f"{'='*60}")

    session, engine = await get_session()
    try:
        bonds = await get_bonds_needing_pricing(
            session,
            stale_only=stale_only,
            stale_days=stale_days,
            limit=limit,
        )

        if not bonds:
            print("No bonds to update")
            return

        print(f"Found {len(bonds)} bonds to update")
        print()

        results = {
            "checked": 0,
            "prices_trace": 0,
            "prices_estimated": 0,
            "prices_failed": 0,
            "yields_calculated": 0,
        }

        for i, bond in enumerate(bonds):
            results["checked"] += 1
            bond_name = bond.name[:40] if bond.name else "Unknown"
            print(f"[{i+1}/{len(bonds)}] {bond_name}...")

            # Get price (Finnhub or estimated)
            price = await get_bond_price(
                cusip=bond.cusip,
                isin=bond.isin,
                coupon_rate_pct=bond.interest_rate / 100 if bond.interest_rate else None,
                maturity_date=bond.maturity_date,
                credit_rating=None,
            )

            if price.last_price:
                if price.is_estimated:
                    results["prices_estimated"] += 1
                    print(f"    Est. Price: {format_price(price.last_price)}")
                else:
                    results["prices_trace"] += 1
                    print(f"    TRACE Price: {format_price(price.last_price)}")

                # Calculate yield if we have required data
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
                        results["yields_calculated"] += 1
                        print(f"    YTM: {format_yield(ytm_bps)}")
                        print(f"    Spread: {format_spread(spread_bps, benchmark)}")
                    except Exception as e:
                        print(f"    Yield calc failed: {e}")

                # Save pricing
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
                results["prices_failed"] += 1
                print(f"    No price: {price.error or 'Unknown error'}")

            # Rate limit
            if i < len(bonds) - 1:
                await asyncio.sleep(REQUEST_DELAY)

        # Summary
        print(f"\n{'='*60}")
        print(f"SUMMARY")
        print(f"{'='*60}")
        print(f"Bonds checked:       {results['checked']}")
        print(f"Prices (TRACE):      {results['prices_trace']}")
        print(f"Prices (estimated):  {results['prices_estimated']}")
        print(f"Prices failed:       {results['prices_failed']}")
        print(f"Yields calculated:   {results['yields_calculated']}")

        total_priced = results['prices_trace'] + results['prices_estimated']
        success_rate = total_priced / results['checked'] * 100 if results['checked'] > 0 else 0
        print(f"Success rate:        {success_rate:.1f}%")

    finally:
        await session.close()
        await engine.dispose()


async def show_pricing_summary():
    """Show summary of current pricing data."""
    from sqlalchemy import select, func
    from app.models import BondPricing, DebtInstrument

    print(f"\n{'='*60}")
    print(f"PRICING DATA SUMMARY")
    print(f"{'='*60}")

    session, engine = await get_session()
    try:
        # Total bonds with CUSIPs
        total_with_cusip = await session.scalar(
            select(func.count()).select_from(DebtInstrument)
            .where(DebtInstrument.cusip.isnot(None))
            .where(DebtInstrument.is_active == True)
        )

        # Total pricing records
        total_pricing = await session.scalar(
            select(func.count()).select_from(BondPricing)
        )

        # Fresh pricing (< 1 day)
        fresh_pricing = await session.scalar(
            select(func.count()).select_from(BondPricing)
            .where(BondPricing.staleness_days < 1)
        )

        # Stale pricing (> 7 days)
        stale_pricing = await session.scalar(
            select(func.count()).select_from(BondPricing)
            .where(BondPricing.staleness_days > 7)
        )

        # With yields
        with_yields = await session.scalar(
            select(func.count()).select_from(BondPricing)
            .where(BondPricing.ytm_bps.isnot(None))
        )

        print(f"Bonds with CUSIPs:    {total_with_cusip}")
        print(f"Pricing records:      {total_pricing}")
        print(f"Fresh (<1 day):       {fresh_pricing}")
        print(f"Stale (>7 days):      {stale_pricing}")
        print(f"With yields:          {with_yields}")

        coverage = total_pricing / total_with_cusip * 100 if total_with_cusip > 0 else 0
        print(f"Coverage:             {coverage:.1f}%")

    finally:
        await session.close()
        await engine.dispose()


async def main():
    parser = argparse.ArgumentParser(
        description="Update bond pricing from FINRA TRACE"
    )
    parser.add_argument("--ticker", type=str, help="Stock ticker (e.g., AAPL)")
    parser.add_argument(
        "--stale-only",
        action="store_true",
        help="Only update stale prices",
    )
    parser.add_argument(
        "--stale-days",
        type=int,
        default=1,
        help="Days before price is considered stale",
    )
    parser.add_argument(
        "--summary",
        action="store_true",
        help="Show pricing data summary",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Update all bonds (not just stale)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=100,
        help="Maximum bonds to process (default: 100)",
    )

    args = parser.parse_args()

    # Check for Finnhub API key
    import os
    if os.getenv("FINNHUB_API_KEY"):
        print("Using Finnhub API for TRACE data (with estimated fallback)")
    else:
        print("Using estimated pricing (Finnhub API key not configured)")

    if args.summary:
        await show_pricing_summary()
    elif args.ticker:
        await update_single_company(args.ticker.upper())
    elif args.all or args.stale_only:
        await update_all(
            stale_only=args.stale_only,
            stale_days=args.stale_days,
            limit=args.limit,
        )
    else:
        parser.print_help()
        print("\nExamples:")
        print("  python scripts/update_pricing.py --ticker AAPL")
        print("  python scripts/update_pricing.py --stale-only")
        print("  python scripts/update_pricing.py --all --limit 50")
        print("  python scripts/update_pricing.py --summary")
        print()
        print("Pricing sources:")
        print("  1. Finnhub API (TRACE) - requires FINNHUB_API_KEY in .env")
        print("  2. Estimated pricing - based on treasury yields + credit spreads")


if __name__ == "__main__":
    asyncio.run(main())
