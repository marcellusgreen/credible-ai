#!/usr/bin/env python3
"""Quick analysis of MISSING outstanding amounts."""
from sqlalchemy import text
from script_utils import get_db_session, print_header, run_async


async def main():
    print_header("MISSING OUTSTANDING ANALYSIS")
    async with get_db_session() as session:
        # SEC zero/null by instrument type
        result = await session.execute(text("""
            SELECT instrument_type, COUNT(*) as cnt
            FROM debt_instruments
            WHERE is_active = true
              AND COALESCE(attributes->>'source', 'sec') <> 'finnhub_discovery'
              AND (outstanding IS NULL OR outstanding = 0)
            GROUP BY instrument_type
            ORDER BY cnt DESC
        """))
        print("\nSEC zero/null outstanding by instrument type:")
        for r in result.fetchall():
            print(f"  {str(r[0]):25s}: {r[1]}")

        # Finnhub zero instruments with near-match SEC instrument that has amount
        result = await session.execute(text("""
            SELECT COUNT(DISTINCT fh.id)
            FROM debt_instruments fh
            JOIN debt_instruments sec ON sec.company_id = fh.company_id
                AND sec.is_active = true
                AND COALESCE(sec.attributes->>'source', 'sec') <> 'finnhub_discovery'
                AND sec.outstanding > 0
                AND ABS(COALESCE(sec.interest_rate, 0) - COALESCE(fh.interest_rate, 0)) <= 5
                AND sec.maturity_date IS NOT NULL AND fh.maturity_date IS NOT NULL
                AND ABS(EXTRACT(YEAR FROM sec.maturity_date) - EXTRACT(YEAR FROM fh.maturity_date)) <= 1
            WHERE fh.is_active = true
              AND fh.attributes->>'source' = 'finnhub_discovery'
              AND (fh.outstanding IS NULL OR fh.outstanding = 0)
        """))
        r = result.fetchone()
        print(f"\nFinnhub zero with near-match SEC instrument (has amount): {r[0]}")

        # How many Finnhub zero instruments are unique (no SEC match)?
        result = await session.execute(text("""
            SELECT COUNT(DISTINCT fh.id)
            FROM debt_instruments fh
            WHERE fh.is_active = true
              AND fh.attributes->>'source' = 'finnhub_discovery'
              AND (fh.outstanding IS NULL OR fh.outstanding = 0)
              AND NOT EXISTS (
                  SELECT 1 FROM debt_instruments sec
                  WHERE sec.company_id = fh.company_id
                    AND sec.is_active = true
                    AND COALESCE(sec.attributes->>'source', 'sec') <> 'finnhub_discovery'
                    AND sec.outstanding > 0
                    AND ABS(COALESCE(sec.interest_rate, 0) - COALESCE(fh.interest_rate, 0)) <= 5
                    AND sec.maturity_date IS NOT NULL AND fh.maturity_date IS NOT NULL
                    AND ABS(EXTRACT(YEAR FROM sec.maturity_date) - EXTRACT(YEAR FROM fh.maturity_date)) <= 1
              )
        """))
        r = result.fetchone()
        print(f"Finnhub zero with NO near-match SEC instrument: {r[0]}")

        # Breakdown: how many of those are bonds (senior_notes) vs other types?
        result = await session.execute(text("""
            SELECT fh.instrument_type, COUNT(*) as cnt
            FROM debt_instruments fh
            WHERE fh.is_active = true
              AND fh.attributes->>'source' = 'finnhub_discovery'
              AND (fh.outstanding IS NULL OR fh.outstanding = 0)
            GROUP BY fh.instrument_type
            ORDER BY cnt DESC
        """))
        print(f"\nFinnhub zero/null by instrument type:")
        for r in result.fetchall():
            print(f"  {str(r[0]):25s}: {r[1]}")

        # What % of cache files have amounts for missing instruments?
        import json
        from pathlib import Path

        result = await session.execute(text("""
            SELECT c.ticker, c.id
            FROM companies c
            JOIN debt_instruments di ON di.company_id = c.id AND di.is_active = true
            WHERE di.outstanding IS NULL OR di.outstanding = 0
            GROUP BY c.id, c.ticker
            HAVING COUNT(*) >= 3
            ORDER BY COUNT(*) DESC
            LIMIT 30
        """))
        companies = result.fetchall()

        cache_hits = 0
        cache_misses = 0
        print(f"\nCache file availability for top 30 companies with missing amounts:")
        for tick, cid in companies:
            cache_path = Path(f"results/{tick.lower()}_iterative.json")
            if cache_path.exists():
                with open(cache_path) as f:
                    data = json.load(f)
                instruments = data.get("debt_instruments", [])
                has_amounts = sum(1 for i in instruments if any(
                    i.get(k) for k in ["outstanding", "principal", "outstanding_amount",
                                       "outstanding_amount_cents", "principal_amount",
                                       "principal_amount_cents", "face_value_cents"]
                ))
                print(f"  {tick:6s}: cache YES, {len(instruments)} instruments, {has_amounts} with amounts")
                cache_hits += 1
            else:
                print(f"  {tick:6s}: cache NO")
                cache_misses += 1
        print(f"\n  Cache files found: {cache_hits}/{cache_hits + cache_misses}")


if __name__ == "__main__":
    run_async(main())
