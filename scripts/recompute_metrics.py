#!/usr/bin/env python3
"""
Recompute CompanyMetrics for all companies in the database.

This script recalculates derived metrics (maturity profile, flags, etc.)
from existing data without re-running extraction.

Usage:
    python scripts/recompute_metrics.py                    # All companies
    python scripts/recompute_metrics.py --ticker AAPL      # Single company
    python scripts/recompute_metrics.py --dry-run          # Preview without saving
"""

import argparse
import asyncio
import os
import sys

from dotenv import load_dotenv

# Add parent dir to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

load_dotenv()

from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker

from app.core.config import get_settings
from app.models import Company
from app.services.metrics import recompute_metrics_for_company

settings = get_settings()


async def main():
    parser = argparse.ArgumentParser(description="Recompute company metrics")
    parser.add_argument("--ticker", help="Single ticker to process")
    parser.add_argument("--dry-run", action="store_true", help="Preview without saving")
    args = parser.parse_args()

    # Create async engine
    database_url = settings.database_url
    if database_url.startswith("postgresql://"):
        database_url = database_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    elif not database_url.startswith("postgresql+asyncpg://"):
        # Handle case where it already has asyncpg
        pass

    engine = create_async_engine(database_url, echo=False)
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with async_session() as db:
        # Get companies to process
        if args.ticker:
            result = await db.execute(
                select(Company).where(Company.ticker == args.ticker.upper())
            )
            companies = list(result.scalars().all())
            if not companies:
                print(f"Company {args.ticker} not found")
                return
        else:
            result = await db.execute(select(Company).order_by(Company.ticker))
            companies = list(result.scalars().all())

        print(f"Processing {len(companies)} companies...")
        if args.dry_run:
            print("(DRY RUN - no changes will be saved)")
        print()

        for company in companies:
            try:
                metrics = await recompute_metrics_for_company(db, company, args.dry_run)

                # Format output
                total_debt_b = (metrics["total_debt"] or 0) / 100_000_000_000
                wam = metrics["weighted_avg_maturity"]
                wam_str = f"{wam:.1f}y" if wam else "N/A"
                lev = metrics["leverage_ratio"]
                lev_str = f"{lev:.1f}x" if lev else "N/A"
                cov = metrics["interest_coverage"]
                cov_str = f"{cov:.1f}x" if cov else "N/A"

                flags = []
                if metrics["has_near_term_maturity"]:
                    flags.append("NEAR_MAT")
                if metrics["has_structural_sub"]:
                    flags.append("STRUCT_SUB")
                if metrics["has_floating_rate"]:
                    flags.append("FLOAT")
                if metrics["is_leveraged_loan"]:
                    flags.append("LEV>4x")

                print(f"  {company.ticker:6} | debt: ${total_debt_b:6.1f}B | "
                      f"lev: {lev_str:5} | cov: {cov_str:5} | "
                      f"WAM: {wam_str:5} | {' '.join(flags)}")

            except Exception as e:
                print(f"  {company.ticker:6} | ERROR: {e}")

        if not args.dry_run:
            await db.commit()
            print(f"\nCommitted changes for {len(companies)} companies")
        else:
            print(f"\nDry run complete - no changes saved")


if __name__ == "__main__":
    asyncio.run(main())
