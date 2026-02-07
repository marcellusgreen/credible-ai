#!/usr/bin/env python3
"""
Batch extract financials for multiple companies.

Usage:
    python scripts/batch_extract_financials.py --tickers AAPL,MSFT,GOOGL
    python scripts/batch_extract_financials.py --high-debt  # Companies with >$5B debt
    python scripts/batch_extract_financials.py --all        # All companies without financials
"""

import argparse
import asyncio
import sys
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from script_utils import get_db_session, print_header, run_async
from app.core.config import get_settings
from app.models import Company, CompanyFinancials, CompanyMetrics
from app.services.financial_extraction import extract_financials, save_financials_to_db

settings = get_settings()


async def get_companies_needing_financials(db, high_debt_only: bool = False) -> list[str]:
    """Get tickers of companies that need financials extracted."""
    fin_subq = select(CompanyFinancials.company_id).distinct()

    if high_debt_only:
        # Companies with >$5B debt
        result = await db.execute(
            select(Company.ticker)
            .join(CompanyMetrics, CompanyMetrics.company_id == Company.id)
            .where(~Company.id.in_(fin_subq))
            .where(CompanyMetrics.total_debt > 500_000_000_000)  # >$5B in cents
            .order_by(CompanyMetrics.total_debt.desc())
        )
    else:
        result = await db.execute(
            select(Company.ticker)
            .where(~Company.id.in_(fin_subq))
            .order_by(Company.ticker)
        )

    return [r[0] for r in result.all()]


async def extract_single(ticker: str, session) -> dict:
    """Extract financials for a single company."""
    try:
        result = await extract_financials(ticker)
        if result:
            saved = await save_financials_to_db(session, ticker, result)
            if saved:
                return {"ticker": ticker, "status": "success", "revenue": result.revenue}
            else:
                return {"ticker": ticker, "status": "error", "reason": "save failed"}
        else:
            return {"ticker": ticker, "status": "error", "reason": "extraction failed"}
    except Exception as e:
        return {"ticker": ticker, "status": "error", "reason": str(e)[:100]}


async def main():
    parser = argparse.ArgumentParser(description="Batch extract financials")
    parser.add_argument("--tickers", help="Comma-separated tickers")
    parser.add_argument("--high-debt", action="store_true", help="Companies with >$5B debt")
    parser.add_argument("--all", action="store_true", help="All companies without financials")
    parser.add_argument("--limit", type=int, default=50, help="Max companies to process")
    parser.add_argument("--offset", type=int, default=0, help="Skip first N companies")
    args = parser.parse_args()

    if not any([args.tickers, args.high_debt, args.all]):
        parser.print_help()
        print("\nError: Specify --tickers, --high-debt, or --all")
        sys.exit(1)

    # Still need engine for async_sessionmaker within the loop
    database_url = settings.database_url
    if database_url.startswith("postgres://"):
        database_url = database_url.replace("postgres://", "postgresql+asyncpg://", 1)
    elif database_url.startswith("postgresql://") and "+asyncpg" not in database_url:
        database_url = database_url.replace("postgresql://", "postgresql+asyncpg://", 1)

    engine = create_async_engine(database_url, echo=False)
    async_session = async_sessionmaker(engine, expire_on_commit=False)

    async with get_db_session() as db:
        if args.tickers:
            tickers = [t.strip().upper() for t in args.tickers.split(",")]
        else:
            tickers = await get_companies_needing_financials(db, args.high_debt)

    # Apply offset and limit
    if args.offset > 0:
        tickers = tickers[args.offset:]
    if args.limit:
        tickers = tickers[:args.limit]

    print_header("BATCH EXTRACT FINANCIALS")
    print(f"Companies: {len(tickers)}")
    print(f"Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print()

    results = []
    for i, ticker in enumerate(tickers):
        print(f"[{i+1}/{len(tickers)}] {ticker}...", end=" ", flush=True)

        async with async_session() as db:
            result = await extract_single(ticker, db)
            results.append(result)

            if result["status"] == "success":
                rev = result.get("revenue", 0)
                rev_b = rev / 100_000_000_000 if rev else 0
                print(f"OK (rev: ${rev_b:.1f}B)")
            else:
                print(f"FAILED: {result.get('reason', 'unknown')[:50]}")

        # Rate limiting
        if i < len(tickers) - 1:
            await asyncio.sleep(1)  # 1 second between extractions

    # Summary
    print()
    print("=" * 60)
    print("SUMMARY")
    print("=" * 60)

    success = [r for r in results if r["status"] == "success"]
    errors = [r for r in results if r["status"] == "error"]

    print(f"Success: {len(success)}")
    print(f"Errors: {len(errors)}")

    if errors:
        print("\nFailed companies:")
        for r in errors:
            print(f"  {r['ticker']}: {r.get('reason', 'unknown')[:60]}")

    await engine.dispose()


if __name__ == "__main__":
    run_async(main())
