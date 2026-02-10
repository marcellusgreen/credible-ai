#!/usr/bin/env python3
"""
Populate Estimated Bond Pricing

Generates estimated bond prices for all debt instruments that have:
- Maturity date (in the future)
- Interest rate (coupon)

Uses treasury yields + credit spreads to estimate:
- Price (clean price as % of par)
- YTM (yield to maturity in basis points)
- Spread to treasury (in basis points)

This provides useful data for demo scenarios and testing while waiting
for real Finnhub/TRACE pricing data.

Usage:
    # Dry run - show what would be created
    python scripts/populate_estimated_pricing.py

    # Save to database
    python scripts/populate_estimated_pricing.py --save-db

    # Single company
    python scripts/populate_estimated_pricing.py --ticker CHTR --save-db

    # Limit number of bonds
    python scripts/populate_estimated_pricing.py --limit 100 --save-db
"""

import argparse
import asyncio
import sys
import os
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from sqlalchemy import text
from app.core.database import async_session_maker
from app.services.estimated_pricing import estimate_bond_price, normalize_rating


async def get_bonds_needing_pricing(
    session,
    ticker: str = None,
    limit: int = 0,
    skip_existing: bool = True
) -> list[dict]:
    """Get bonds that can receive estimated pricing."""

    where_clauses = [
        "d.maturity_date IS NOT NULL",
        "d.interest_rate IS NOT NULL",
        "d.maturity_date > CURRENT_DATE",
    ]

    if skip_existing:
        where_clauses.append(
            "d.id NOT IN (SELECT debt_instrument_id FROM bond_pricing WHERE debt_instrument_id IS NOT NULL)"
        )

    if ticker:
        where_clauses.append("c.ticker = :ticker")

    where_sql = " AND ".join(where_clauses)
    limit_sql = f"LIMIT {limit}" if limit > 0 else ""

    sql = f"""
        SELECT
            d.id,
            d.name,
            d.cusip,
            d.isin,
            d.interest_rate,
            d.maturity_date,
            d.seniority,
            c.ticker,
            c.name as company_name
        FROM debt_instruments d
        JOIN companies c ON d.company_id = c.id
        WHERE {where_sql}
        ORDER BY c.ticker, d.maturity_date
        {limit_sql}
    """

    params = {"ticker": ticker.upper()} if ticker else {}
    result = await session.execute(text(sql), params)

    bonds = []
    for row in result.fetchall():
        bonds.append({
            "id": row.id,
            "name": row.name,
            "cusip": row.cusip,
            "isin": row.isin,
            "interest_rate_bps": row.interest_rate,  # stored in bps
            "maturity_date": row.maturity_date,
            "seniority": row.seniority,
            "ticker": row.ticker,
            "company_name": row.company_name,
        })

    return bonds


def estimate_rating_from_seniority(seniority: str) -> str:
    """
    Estimate a credit rating based on seniority.

    This is a rough heuristic - secured bonds get better rating,
    subordinated get worse. Default to BB (high yield but not distressed).
    """
    if not seniority:
        return "BB"

    seniority = seniority.lower()

    if "secured" in seniority or "first" in seniority:
        return "BB+"  # Slightly better for secured
    elif "subordinat" in seniority or "junior" in seniority:
        return "B"  # Worse for subordinated
    else:
        return "BB"  # Default high yield


async def populate_estimated_pricing(
    session,
    bonds: list[dict],
    save_db: bool = False,
    verbose: bool = False
) -> dict:
    """
    Generate and optionally save estimated pricing for bonds.

    Returns stats dict with counts.
    """
    stats = {
        "total": len(bonds),
        "estimated": 0,
        "skipped": 0,
        "errors": 0,
        "saved": 0,
    }

    today = date.today()

    for i, bond in enumerate(bonds):
        if verbose or (i + 1) % 100 == 0:
            print(f"[{i+1}/{len(bonds)}] Processing {bond['ticker']} - {bond['name'][:40]}...", end="\r")

        try:
            # Convert interest rate from bps to percent
            coupon_pct = bond["interest_rate_bps"] / 100.0

            # Estimate rating from seniority (we don't have actual ratings)
            estimated_rating = estimate_rating_from_seniority(bond["seniority"])

            # Get estimated price
            result = await estimate_bond_price(
                coupon_rate_pct=coupon_pct,
                maturity_date=bond["maturity_date"],
                credit_rating=estimated_rating,
                cusip=bond["cusip"],
                debt_instrument_id=bond["id"],
            )

            if verbose:
                print(f"\n  {bond['ticker']}: {bond['name'][:50]}")
                print(f"    Coupon: {coupon_pct:.2f}%, Maturity: {bond['maturity_date']}")
                print(f"    Est. Price: {result.estimated_price}, YTM: {result.estimated_ytm_bps/100:.2f}%")
                print(f"    Spread: +{result.estimated_spread_bps}bps over {result.treasury_benchmark}")

            stats["estimated"] += 1

            if save_db:
                # Check if pricing already exists for this instrument
                existing = await session.execute(text("""
                    SELECT id FROM bond_pricing WHERE debt_instrument_id = :debt_id
                """), {"debt_id": str(bond["id"])})

                if existing.fetchone():
                    # Update existing record
                    await session.execute(text("""
                        UPDATE bond_pricing SET
                            last_price = :price,
                            last_trade_date = :trade_date,
                            ytm_bps = :ytm_bps,
                            spread_to_treasury_bps = :spread_bps,
                            treasury_benchmark = :benchmark,
                            price_source = 'estimated',
                            staleness_days = 0,
                            fetched_at = :now,
                            calculated_at = :now
                        WHERE debt_instrument_id = :debt_instrument_id
                    """), {
                        "debt_instrument_id": str(bond["id"]),
                        "price": float(result.estimated_price),
                        "trade_date": today,
                        "ytm_bps": result.estimated_ytm_bps,
                        "spread_bps": result.estimated_spread_bps,
                        "benchmark": result.treasury_benchmark,
                        "now": datetime.utcnow(),
                    })
                else:
                    # Insert new record
                    await session.execute(text("""
                        INSERT INTO bond_pricing (
                            id,
                            debt_instrument_id,
                            cusip,
                            last_price,
                            last_trade_date,
                            ytm_bps,
                            spread_to_treasury_bps,
                            treasury_benchmark,
                            price_source,
                            staleness_days,
                            fetched_at,
                            calculated_at
                        ) VALUES (
                            gen_random_uuid(),
                            :debt_instrument_id,
                            :cusip,
                            :price,
                            :trade_date,
                            :ytm_bps,
                            :spread_bps,
                            :benchmark,
                            'estimated',
                            0,
                            :now,
                            :now
                        )
                    """), {
                        "debt_instrument_id": str(bond["id"]),
                        "cusip": bond["cusip"],
                        "price": float(result.estimated_price),
                        "trade_date": today,
                        "ytm_bps": result.estimated_ytm_bps,
                        "spread_bps": result.estimated_spread_bps,
                        "benchmark": result.treasury_benchmark,
                        "now": datetime.utcnow(),
                    })
                stats["saved"] += 1

        except Exception as e:
            if verbose:
                print(f"\n  Error processing {bond['ticker']} {bond['name'][:30]}: {e}")
            stats["errors"] += 1

    if save_db:
        await session.commit()

    print()  # Clear progress line
    return stats


async def main():
    parser = argparse.ArgumentParser(
        description="Populate estimated bond pricing"
    )
    parser.add_argument(
        "--ticker",
        type=str,
        help="Process single company by ticker"
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Process all companies (default if no ticker specified)"
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Limit number of bonds to process (0 = unlimited)"
    )
    parser.add_argument(
        "--save-db",
        action="store_true",
        help="Save results to database (default is dry run)"
    )
    parser.add_argument(
        "--include-existing",
        action="store_true",
        help="Re-estimate pricing for bonds that already have pricing"
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Show detailed output"
    )

    args = parser.parse_args()

    print("=" * 70)
    print("POPULATE ESTIMATED BOND PRICING")
    print("=" * 70)

    async with async_session_maker() as session:
        # Get bonds needing pricing
        print("\nFinding bonds that need pricing...")
        bonds = await get_bonds_needing_pricing(
            session,
            ticker=args.ticker,
            limit=args.limit,
            skip_existing=not args.include_existing
        )

        if not bonds:
            print("No bonds found needing estimated pricing.")
            return

        print(f"Found {len(bonds)} bonds to estimate.")

        if not args.save_db:
            print("\n[DRY RUN - use --save-db to persist]")

        print("\nEstimating prices...")
        print("-" * 70)

        stats = await populate_estimated_pricing(
            session,
            bonds,
            save_db=args.save_db,
            verbose=args.verbose
        )

        # Summary
        print("\n" + "=" * 70)
        print("SUMMARY")
        print("=" * 70)
        print(f"  Total bonds:     {stats['total']}")
        print(f"  Estimated:       {stats['estimated']}")
        print(f"  Errors:          {stats['errors']}")
        if args.save_db:
            print(f"  Saved to DB:     {stats['saved']}")
        else:
            print(f"  (Dry run - nothing saved)")


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())
