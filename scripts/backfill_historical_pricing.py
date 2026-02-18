#!/usr/bin/env python3
"""
Backfill bonds that have estimated pricing with their most recent historical TRACE price.

This is a one-time script to fix bonds where:
- bond_pricing has price_source='estimated'
- bond_pricing_history has real TRACE prices available

After this backfill, the scheduler will maintain these automatically via the
historical TRACE fallback in get_bond_price().
"""

from script_utils import get_db_session, print_header, print_summary, run_async
from sqlalchemy import text


async def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="Show what would change without saving")
    parser.add_argument("--save", action="store_true", help="Apply changes to database")
    args = parser.parse_args()

    if not args.dry_run and not args.save:
        print("Use --dry-run to preview or --save to apply changes")
        return

    print_header("BACKFILL HISTORICAL TRACE PRICING")

    async with get_db_session() as db:
        # Find bonds with estimated pricing that have historical TRACE
        r = await db.execute(text("""
            SELECT
                bp.id as pricing_id,
                bp.debt_instrument_id,
                bp.cusip,
                bp.last_price as est_price,
                bp.price_source as current_source,
                di.name as bond_name,
                c.ticker,
                latest_hist.price as hist_price,
                latest_hist.price_date as hist_date,
                latest_hist.ytm_bps as hist_ytm_bps,
                latest_hist.spread_bps as hist_spread_bps,
                latest_hist.volume as hist_volume
            FROM bond_pricing bp
            JOIN debt_instruments di ON di.id = bp.debt_instrument_id
            JOIN companies c ON c.id = di.company_id
            JOIN LATERAL (
                SELECT bph.price, bph.price_date, bph.ytm_bps, bph.spread_bps, bph.volume
                FROM bond_pricing_history bph
                WHERE bph.debt_instrument_id = bp.debt_instrument_id
                AND bph.price IS NOT NULL
                AND bph.price_source = 'TRACE'
                ORDER BY bph.price_date DESC
                LIMIT 1
            ) latest_hist ON true
            WHERE di.is_active = true
            AND di.cusip IS NOT NULL
            AND bp.price_source = 'estimated'
            ORDER BY c.ticker, di.name
        """))
        rows = r.fetchall()

        if not rows:
            print("\nNo bonds to update - all estimated bonds lack historical TRACE data")
            return

        print(f"\nFound {len(rows)} bonds with estimated pricing that have historical TRACE data\n")
        print(f"{'Ticker':<8} {'CUSIP':<12} {'Hist Date':<12} {'Est Price':<11} {'TRACE Price':<12} {'Bond Name'}")
        print("-" * 100)

        updated = 0
        for row in rows:
            print(f"{row.ticker:<8} {row.cusip:<12} {str(row.hist_date):<12} {str(row.est_price):<11} {str(row.hist_price):<12} {row.bond_name[:40]}")

            if args.save:
                from datetime import date as date_type
                staleness = (date_type.today() - row.hist_date).days
                await db.execute(text("""
                    UPDATE bond_pricing
                    SET last_price = :price,
                        last_trade_date = :trade_date,
                        last_trade_volume = :volume,
                        ytm_bps = :ytm_bps,
                        spread_to_treasury_bps = :spread_bps,
                        price_source = 'TRACE',
                        staleness_days = :staleness,
                        fetched_at = NOW()
                    WHERE id = :pricing_id
                """), {
                    "price": row.hist_price,
                    "trade_date": row.hist_date,
                    "volume": row.hist_volume,
                    "ytm_bps": row.hist_ytm_bps,
                    "spread_bps": row.hist_spread_bps,
                    "staleness": staleness,
                    "pricing_id": row.pricing_id,
                })
                updated += 1

        if args.save:
            await db.commit()
            print(f"\nUpdated {updated} bonds from estimated -> TRACE (historical)")
        else:
            print(f"\n[DRY RUN] Would update {len(rows)} bonds from estimated -> TRACE (historical)")


if __name__ == "__main__":
    run_async(main())
