#!/usr/bin/env python3
"""Check MISSING_ALL companies extraction state for re-extraction planning."""
import asyncio
import os
import sys
import json

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv
load_dotenv()

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker


MISSING_ALL = ['C', 'COF', 'CPRT', 'CSGP', 'ET', 'FOX', 'MS', 'PANW', 'PG', 'PLD', 'TEAM', 'TTD', 'UAL', 'USB']


async def main():
    engine = create_async_engine(os.getenv('DATABASE_URL'))
    sm = async_sessionmaker(engine, expire_on_commit=False)

    async with sm() as session:
        print(f"{'Ticker':6s} {'CIK':>12s} {'Instruments':>6s} {'FH':>4s} {'SEC':>4s} {'TotalDebt':>10s} {'Cache?':>6s} {'ExtStatus':>30s}")
        print("-" * 90)

        for ticker in MISSING_ALL:
            r = await session.execute(text("""
                SELECT c.ticker, c.cik, c.name,
                    (SELECT COUNT(*) FROM debt_instruments di
                     WHERE di.company_id = c.id AND di.is_active = true) as total,
                    (SELECT COUNT(*) FROM debt_instruments di
                     WHERE di.company_id = c.id AND di.is_active = true
                     AND COALESCE(di.attributes->>'source', 'sec') = 'finnhub_discovery') as fh_count,
                    (SELECT COUNT(*) FROM debt_instruments di
                     WHERE di.company_id = c.id AND di.is_active = true
                     AND COALESCE(di.attributes->>'source', 'sec') != 'finnhub_discovery') as sec_count,
                    (SELECT total_debt FROM company_financials cf
                     WHERE cf.company_id = c.id AND cf.total_debt IS NOT NULL
                     ORDER BY cf.fiscal_year DESC, cf.fiscal_quarter DESC LIMIT 1) as total_debt,
                    cc.extraction_status
                FROM companies c
                LEFT JOIN company_cache cc ON cc.company_id = c.id
                WHERE c.ticker = :ticker
            """), {"ticker": ticker})
            row = r.fetchone()
            if not row:
                continue

            ticker, cik, name, total, fh, sec, total_debt, ext_status = row
            debt_str = f"${float(total_debt)/1e11:.1f}B" if total_debt else "N/A"

            # Check cache file
            from pathlib import Path
            cache_path = Path(f"results/{ticker.lower()}_iterative.json")
            has_cache = "YES" if cache_path.exists() else "no"

            # Get extraction status summary
            ext_summary = ""
            if ext_status:
                es = ext_status if isinstance(ext_status, dict) else json.loads(ext_status)
                core_status = es.get('core', {}).get('status', '?')
                ext_summary = f"core={core_status}"

            print(f"{ticker:6s} {cik or 'N/A':>12s} {total:6d} {fh:4d} {sec:4d} {debt_str:>10s} {has_cache:>6s} {ext_summary:>30s}")

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
