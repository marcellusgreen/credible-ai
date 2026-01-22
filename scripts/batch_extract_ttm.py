#!/usr/bin/env python3
"""
Batch extract TTM financials for companies missing quarters.

Usage:
    python scripts/batch_extract_ttm.py                    # All companies needing TTM
    python scripts/batch_extract_ttm.py --max 10           # Limit to 10 companies
    python scripts/batch_extract_ttm.py --dry-run          # Preview without extracting
"""

import argparse
import asyncio
import os
import sys
import subprocess

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker

from app.core.config import get_settings
from app.models import Company, CompanyFinancials

settings = get_settings()


async def get_companies_needing_ttm():
    """Get list of companies with fewer than 4 quarters of financial data."""
    database_url = settings.database_url
    if database_url.startswith('postgresql://'):
        database_url = database_url.replace('postgresql://', 'postgresql+asyncpg://', 1)
    
    engine = create_async_engine(database_url, echo=False)
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    
    async with async_session() as db:
        companies = (await db.execute(select(Company).order_by(Company.ticker))).scalars().all()
        
        needing_ttm = []
        for company in companies:
            if not company.cik:
                continue
                
            result = await db.execute(
                select(func.count(CompanyFinancials.id))
                .where(CompanyFinancials.company_id == company.id)
            )
            count = result.scalar()
            
            if count < 4:
                needing_ttm.append((company.ticker, company.cik, count))
        
        return needing_ttm


def extract_ttm(ticker: str, dry_run: bool = False) -> bool:
    """Run TTM extraction for a single company."""
    cmd = [
        sys.executable, 
        "scripts/extract_financials.py",
        "--ticker", ticker,
        "--ttm",
        "--save-db"
    ]
    
    if dry_run:
        print(f"  [DRY RUN] Would run: {' '.join(cmd)}")
        return True
    
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=300  # 5 minute timeout per company
        )
        if result.returncode == 0:
            return True
        else:
            # Show actual error, not just warnings
            error_msg = result.stderr or result.stdout
            print(f"  Error (exit code {result.returncode}): {error_msg[:300]}")
            return False
    except subprocess.TimeoutExpired:
        print(f"  Timeout after 5 minutes")
        return False
    except Exception as e:
        print(f"  Exception: {e}")
        return False


async def main():
    parser = argparse.ArgumentParser(description="Batch extract TTM financials")
    parser.add_argument("--max", type=int, help="Maximum companies to process")
    parser.add_argument("--dry-run", action="store_true", help="Preview without extracting")
    args = parser.parse_args()
    
    companies = await get_companies_needing_ttm()
    
    print(f"Found {len(companies)} companies needing TTM extraction")
    print()
    
    if args.max:
        companies = companies[:args.max]
        print(f"Limited to {args.max} companies")
        print()
    
    success = 0
    failed = 0
    
    for ticker, cik, current_qtrs in companies:
        print(f"Processing {ticker} (CIK: {cik}, current quarters: {current_qtrs})...")
        
        if extract_ttm(ticker, args.dry_run):
            success += 1
            print(f"  [OK] Success")
        else:
            failed += 1
            print(f"  [FAIL] Failed")
    
    print()
    print(f"Complete: {success} succeeded, {failed} failed")


if __name__ == "__main__":
    asyncio.run(main())
