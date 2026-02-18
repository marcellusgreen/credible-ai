#!/usr/bin/env python3
"""Diagnose bonds with CUSIPs on Finnhub that lack current pricing."""

from script_utils import get_db_session, print_header, run_async
from sqlalchemy import text


async def main():
    print_header("PRICING GAP DIAGNOSIS")

    async with get_db_session() as db:
        # 1. Total bonds with CUSIPs
        r = await db.execute(text("""
            SELECT COUNT(*) FROM debt_instruments
            WHERE cusip IS NOT NULL AND is_active = true
        """))
        total_cusip = r.scalar()
        print(f"\nTotal active bonds with CUSIPs: {total_cusip}")

        # 2. Bonds with TRACE pricing (current)
        r = await db.execute(text("""
            SELECT COUNT(*) FROM debt_instruments di
            JOIN bond_pricing bp ON bp.debt_instrument_id = di.id
            WHERE di.cusip IS NOT NULL AND di.is_active = true
            AND bp.last_price IS NOT NULL AND bp.price_source = 'TRACE'
        """))
        has_trace = r.scalar()
        print(f"Bonds with current TRACE pricing: {has_trace}")

        # 3. Bonds with CUSIPs but NO current TRACE pricing
        r = await db.execute(text("""
            SELECT COUNT(*) FROM debt_instruments di
            LEFT JOIN bond_pricing bp ON bp.debt_instrument_id = di.id
            WHERE di.cusip IS NOT NULL AND di.is_active = true
            AND (bp.id IS NULL OR bp.last_price IS NULL OR bp.price_source != 'TRACE')
        """))
        no_trace = r.scalar()
        print(f"Bonds with CUSIP but NO current TRACE pricing: {no_trace}")

        # 4. Of those without current pricing, how many have historical pricing?
        r = await db.execute(text("""
            SELECT COUNT(DISTINCT di.id) FROM debt_instruments di
            LEFT JOIN bond_pricing bp ON bp.debt_instrument_id = di.id
            JOIN bond_pricing_history bph ON bph.debt_instrument_id = di.id
            WHERE di.cusip IS NOT NULL AND di.is_active = true
            AND (bp.id IS NULL OR bp.last_price IS NULL OR bp.price_source != 'TRACE')
            AND bph.price IS NOT NULL AND bph.price_source = 'TRACE'
        """))
        has_historical = r.scalar()
        print(f"  -> Of those, have historical TRACE pricing: {has_historical}")
        print(f"  -> Truly no pricing at all: {no_trace - has_historical}")

        # 5. Show some examples - bonds with historical but no current pricing
        r = await db.execute(text("""
            SELECT di.cusip, di.name, c.ticker,
                   bp.price_source as current_source,
                   bp.last_price as current_price,
                   MAX(bph.price_date) as latest_historical_date,
                   (SELECT bph2.price FROM bond_pricing_history bph2
                    WHERE bph2.debt_instrument_id = di.id AND bph2.price_source = 'TRACE'
                    ORDER BY bph2.price_date DESC LIMIT 1) as latest_historical_price
            FROM debt_instruments di
            JOIN companies c ON c.id = di.company_id
            LEFT JOIN bond_pricing bp ON bp.debt_instrument_id = di.id
            JOIN bond_pricing_history bph ON bph.debt_instrument_id = di.id
                AND bph.price_source = 'TRACE' AND bph.price IS NOT NULL
            WHERE di.cusip IS NOT NULL AND di.is_active = true
            AND (bp.id IS NULL OR bp.last_price IS NULL OR bp.price_source != 'TRACE')
            GROUP BY di.id, di.cusip, di.name, c.ticker, bp.price_source, bp.last_price
            ORDER BY MAX(bph.price_date) DESC
            LIMIT 20
        """))
        rows = r.fetchall()
        if rows:
            print(f"\nExamples - bonds with historical but no current TRACE pricing:")
            print(f"{'Ticker':<8} {'CUSIP':<12} {'Latest Hist Date':<18} {'Hist Price':<12} {'Curr Source':<14} {'Name'}")
            print("-" * 110)
            for row in rows:
                print(f"{row.ticker:<8} {row.cusip:<12} {str(row.latest_historical_date):<18} {str(row.latest_historical_price):<12} {str(row.current_source):<14} {row.name[:50]}")

        # 6. What price_source values exist for bonds without TRACE?
        r = await db.execute(text("""
            SELECT bp.price_source, COUNT(*) as cnt
            FROM debt_instruments di
            JOIN bond_pricing bp ON bp.debt_instrument_id = di.id
            WHERE di.cusip IS NOT NULL AND di.is_active = true
            AND bp.price_source != 'TRACE'
            GROUP BY bp.price_source
            ORDER BY cnt DESC
        """))
        rows = r.fetchall()
        if rows:
            print(f"\nNon-TRACE price_source breakdown:")
            for row in rows:
                print(f"  {row.price_source}: {row.cnt}")

        # 7. Staleness distribution for TRACE-priced bonds
        r = await db.execute(text("""
            SELECT
                CASE
                    WHEN bp.staleness_days <= 1 THEN '0-1 days'
                    WHEN bp.staleness_days <= 7 THEN '2-7 days'
                    WHEN bp.staleness_days <= 30 THEN '8-30 days'
                    WHEN bp.staleness_days <= 90 THEN '31-90 days'
                    ELSE '90+ days'
                END as staleness_bucket,
                COUNT(*) as cnt
            FROM bond_pricing bp
            JOIN debt_instruments di ON di.id = bp.debt_instrument_id
            WHERE di.is_active = true AND bp.price_source = 'TRACE' AND bp.last_price IS NOT NULL
            GROUP BY staleness_bucket
            ORDER BY MIN(bp.staleness_days)
        """))
        rows = r.fetchall()
        if rows:
            print(f"\nStaleness distribution (current TRACE pricing):")
            for row in rows:
                print(f"  {row.staleness_bucket}: {row.cnt}")

        # 8. Bonds with estimated pricing that could benefit from historical TRACE
        r = await db.execute(text("""
            SELECT COUNT(DISTINCT di.id) FROM debt_instruments di
            JOIN bond_pricing bp ON bp.debt_instrument_id = di.id
            JOIN bond_pricing_history bph ON bph.debt_instrument_id = di.id
            WHERE di.cusip IS NOT NULL AND di.is_active = true
            AND bp.price_source = 'estimated'
            AND bph.price_source = 'TRACE' AND bph.price IS NOT NULL
        """))
        estimated_with_hist = r.scalar()
        print(f"\nBonds with estimated pricing that have historical TRACE: {estimated_with_hist}")


if __name__ == "__main__":
    run_async(main())
