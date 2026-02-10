#!/usr/bin/env python3
"""
Backfill 8 quarters of financial data for all companies.

Runs overnight to extract 8 10-Qs for each company that has < 8 quarters.

Usage:
    python scripts/backfill_all_quarters.py
    python scripts/backfill_all_quarters.py --limit 10  # Test with 10 companies
"""

import asyncio
import os
import sys
import time
from datetime import datetime

from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv()

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker

from app.core.config import get_settings
from app.models import Company, CompanyFinancials
from app.services.financial_extraction import extract_ttm_financials, save_financials_to_db


async def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, help="Limit companies to process")
    args = parser.parse_args()

    settings = get_settings()
    engine = create_async_engine(settings.database_url.replace("postgresql://", "postgresql+asyncpg://"))
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    # Get companies with < 8 quarters
    async with async_session() as db:
        result = await db.execute(
            select(
                Company.id,
                Company.ticker,
                Company.cik,
                Company.is_financial_institution,
                func.count(CompanyFinancials.id).label("quarters")
            )
            .outerjoin(CompanyFinancials, CompanyFinancials.company_id == Company.id)
            .group_by(Company.id, Company.ticker, Company.cik, Company.is_financial_institution)
            .having(func.count(CompanyFinancials.id) < 8)
            .order_by(Company.ticker)
        )
        companies = list(result)

    if args.limit:
        companies = companies[:args.limit]

    total = len(companies)
    print(f"Starting extraction for {total} companies at {datetime.now()}")
    print("=" * 60)

    success = 0
    errors = 0
    start_time = time.time()

    for i, (cid, ticker, cik, is_bank, current_quarters) in enumerate(companies):
        print(f"\n[{i+1}/{total}] {ticker} (currently {current_quarters} quarters)")

        try:
            financials = await extract_ttm_financials(
                ticker=ticker,
                cik=cik or "",
                is_financial_institution=is_bank or False,
            )

            if financials:
                async with async_session() as db:
                    for fin in financials:
                        await save_financials_to_db(db, ticker, fin)
                print(f"  Extracted {len(financials)} quarters")
                success += 1
            else:
                print(f"  No data extracted")
                errors += 1

        except Exception as e:
            err_msg = str(e)[:100]
            print(f"  Error: {err_msg}")
            errors += 1

            if "429" in str(e) or "ResourceExhausted" in str(e):
                print("  Rate limited - waiting 60s...")
                time.sleep(60)

        # Brief delay between companies
        time.sleep(3)

        # Progress update every 10 companies
        if (i + 1) % 10 == 0:
            elapsed = time.time() - start_time
            rate = (i + 1) / elapsed * 60  # companies per minute
            remaining = (total - i - 1) / rate if rate > 0 else 0
            print(f"\n  --- Progress: {i+1}/{total} | {rate:.1f}/min | ~{remaining:.0f} min remaining ---\n")

    elapsed = time.time() - start_time
    print("\n" + "=" * 60)
    print(f"Completed at {datetime.now()}")
    print(f"Duration: {elapsed/60:.1f} minutes")
    print(f"Success: {success}, Errors: {errors}")


if __name__ == "__main__":
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    asyncio.run(main())
