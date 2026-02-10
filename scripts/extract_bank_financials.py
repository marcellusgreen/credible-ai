#!/usr/bin/env python3
"""
Extract financial data for all banks/financial institutions.

Runs extraction one company at a time with delays to avoid rate limits.

Usage:
    python scripts/extract_bank_financials.py              # All banks
    python scripts/extract_bank_financials.py --ticker TFC # Single bank
    python scripts/extract_bank_financials.py --delay 30   # 30 sec between companies
"""

import asyncio
import os
import sys
import time

from dotenv import load_dotenv

# Add parent to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

load_dotenv()

from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker

from app.core.config import get_settings
from app.models import Company, CompanyFinancials
from app.services.financial_extraction import extract_ttm_financials, save_financials_to_db


async def main():
    import argparse

    parser = argparse.ArgumentParser(description="Extract financial data for banks")
    parser.add_argument("--ticker", help="Process single bank")
    parser.add_argument("--delay", type=int, default=10, help="Delay between companies (seconds)")
    parser.add_argument("--skip-existing", action="store_true", help="Skip if bank fields already populated")
    args = parser.parse_args()

    settings = get_settings()
    engine = create_async_engine(
        settings.database_url.replace("postgresql://", "postgresql+asyncpg://")
    )
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with async_session() as db:
        # Get all financial institutions
        if args.ticker:
            result = await db.execute(
                select(Company).where(
                    Company.ticker == args.ticker.upper(),
                    Company.is_financial_institution == True
                )
            )
        else:
            result = await db.execute(
                select(Company).where(Company.is_financial_institution == True)
                .order_by(Company.ticker)
            )
        banks = list(result.scalars())

    if not banks:
        print("No financial institutions found")
        return

    print(f"Processing {len(banks)} financial institutions")
    print(f"Delay between companies: {args.delay}s")
    print("=" * 60)

    total_extracted = 0

    for i, bank in enumerate(banks):
        print(f"\n[{i+1}/{len(banks)}] {bank.ticker} - {bank.name}")

        if args.skip_existing:
            # Check if bank fields already populated
            async with async_session() as db:
                result = await db.execute(
                    select(CompanyFinancials)
                    .where(
                        CompanyFinancials.company_id == bank.id,
                        CompanyFinancials.net_interest_income.isnot(None)
                    )
                    .limit(1)
                )
                if result.scalar_one_or_none():
                    print("  Skipping - bank fields already populated")
                    continue

        try:
            financials = await extract_ttm_financials(
                ticker=bank.ticker,
                cik=bank.cik or "",
                use_claude=False,  # Use Gemini (cheaper)
                is_financial_institution=True,
            )

            if financials:
                async with async_session() as db:
                    for fin in financials:
                        await save_financials_to_db(db, bank.ticker, fin)
                print(f"  Extracted and saved {len(financials)} quarters")
                total_extracted += len(financials)
            else:
                print("  No financials extracted")

        except Exception as e:
            print(f"  Error: {e}")
            if "429" in str(e) or "ResourceExhausted" in str(e):
                print(f"  Rate limited - waiting 60s before continuing...")
                time.sleep(60)

        # Delay between companies to avoid rate limits
        if i < len(banks) - 1:
            print(f"  Waiting {args.delay}s before next company...")
            time.sleep(args.delay)

    print("\n" + "=" * 60)
    print(f"Total quarters extracted: {total_extracted}")


if __name__ == "__main__":
    if sys.platform == 'win32':
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    asyncio.run(main())
