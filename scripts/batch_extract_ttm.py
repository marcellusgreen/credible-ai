#!/usr/bin/env python3
"""
Batch extract TTM (Trailing Twelve Months) financials for all companies.

Usage:
    python scripts/batch_extract_ttm.py                    # All companies
    python scripts/batch_extract_ttm.py --limit 10         # First 10 companies
    python scripts/batch_extract_ttm.py --skip-existing    # Skip companies with 4 quarters
    python scripts/batch_extract_ttm.py --use-claude       # Use Claude instead of Gemini
"""

import argparse
import asyncio
import os
import sys
from datetime import datetime

# Force unbuffered output with UTF-8 encoding
sys.stdout.reconfigure(line_buffering=True, encoding='utf-8', errors='replace')
sys.stderr.reconfigure(line_buffering=True, encoding='utf-8', errors='replace')

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker

from app.models import Company, CompanyFinancials


async def get_companies_with_financial_counts(session: AsyncSession) -> list[dict]:
    """Get all companies with their financial quarter counts."""
    # Subquery to count financials per company
    subq = (
        select(
            CompanyFinancials.company_id,
            func.count(CompanyFinancials.id).label("quarter_count")
        )
        .group_by(CompanyFinancials.company_id)
        .subquery()
    )

    # Join with companies
    result = await session.execute(
        select(Company.ticker, Company.cik, Company.name, subq.c.quarter_count)
        .outerjoin(subq, Company.id == subq.c.company_id)
        .order_by(Company.ticker)
    )

    companies = []
    for row in result.all():
        companies.append({
            "ticker": row.ticker,
            "cik": row.cik,
            "name": row.name,
            "quarter_count": row.quarter_count or 0
        })

    return companies


async def main():
    parser = argparse.ArgumentParser(description="Batch extract TTM financials")
    parser.add_argument("--limit", type=int, help="Limit number of companies to process")
    parser.add_argument("--start-from", type=int, default=0,
                       help="Start from company number (0-indexed)")
    parser.add_argument("--skip-existing", action="store_true",
                       help="Skip companies that already have 4 quarters")
    parser.add_argument("--use-claude", action="store_true",
                       help="Use Claude instead of Gemini")
    parser.add_argument("--delay", type=int, default=3,
                       help="Delay between companies (seconds)")
    parser.add_argument("--save-db", action="store_true", default=True,
                       help="Save to database (default: True)")
    args = parser.parse_args()

    # Import here to avoid circular imports
    from app.services.financial_extraction import extract_ttm_financials, save_financials_to_db

    # Create database connection
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise ValueError("DATABASE_URL not set")

    # Convert to async URL if needed
    if database_url.startswith("postgresql://"):
        database_url = database_url.replace("postgresql://", "postgresql+asyncpg://", 1)

    engine = create_async_engine(database_url, echo=False)
    async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    # Get companies
    async with async_session() as session:
        companies = await get_companies_with_financial_counts(session)

    print(f"Found {len(companies)} companies in database")

    # Filter if skip-existing
    if args.skip_existing:
        companies = [c for c in companies if c["quarter_count"] < 4]
        print(f"After filtering (skip companies with 4+ quarters): {len(companies)} companies")

    # Apply start-from
    if args.start_from > 0:
        companies = companies[args.start_from:]
        print(f"Starting from company {args.start_from}, {len(companies)} companies remaining")

    # Apply limit
    if args.limit:
        companies = companies[:args.limit]
        print(f"Limited to {len(companies)} companies")

    print(f"\nStarting TTM extraction...")
    print(f"Using {'Claude' if args.use_claude else 'Gemini'} for extraction")
    print(f"Delay between companies: {args.delay}s")
    print("=" * 70)

    results = {"success": 0, "failed": 0, "skipped": 0}
    failed_tickers = []

    for i, company in enumerate(companies):
        ticker = company["ticker"]
        cik = company["cik"]
        name = company["name"]
        existing_quarters = company["quarter_count"]

        print(f"\n[{i+1}/{len(companies)}] {ticker} - {name}")
        print(f"  CIK: {cik}, Existing quarters: {existing_quarters}")

        if not cik:
            print(f"  SKIPPED: No CIK")
            results["skipped"] += 1
            continue

        try:
            # Extract TTM financials
            quarters = await extract_ttm_financials(
                ticker=ticker,
                cik=cik,
                use_claude=args.use_claude,
            )

            if not quarters:
                print(f"  FAILED: No quarters extracted")
                results["failed"] += 1
                failed_tickers.append(ticker)
                continue

            print(f"  Extracted {len(quarters)} quarters:")
            for q in quarters:
                ebitda_str = f"${q.ebitda/100_000_000_000:.1f}B" if q.ebitda else "N/A"
                print(f"    Q{q.fiscal_quarter} {q.fiscal_year}: EBITDA={ebitda_str}")

            # Save to database
            if args.save_db:
                async with async_session() as session:
                    saved = 0
                    for q in quarters:
                        record = await save_financials_to_db(session, ticker, q)
                        if record:
                            saved += 1
                    await session.commit()
                    print(f"  Saved {saved} quarters to database")

            results["success"] += 1

        except Exception as e:
            print(f"  ERROR: {str(e)[:100]}")
            results["failed"] += 1
            failed_tickers.append(ticker)

        # Delay between requests (except for last one)
        if i < len(companies) - 1:
            await asyncio.sleep(args.delay)

    # Summary
    print("\n" + "=" * 70)
    print("BATCH TTM EXTRACTION SUMMARY")
    print("=" * 70)
    print(f"  Total companies: {len(companies)}")
    print(f"  Successful:      {results['success']}")
    print(f"  Failed:          {results['failed']}")
    print(f"  Skipped:         {results['skipped']}")

    if failed_tickers:
        print(f"\n  Failed tickers: {', '.join(failed_tickers)}")

    print("\nNext step: Run recompute_metrics.py to update leverage ratios")
    print("  python scripts/recompute_metrics.py")

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
