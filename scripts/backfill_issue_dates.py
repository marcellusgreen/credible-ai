#!/usr/bin/env python3
"""
Backfill issue_date for debt instruments that have maturity_date but no issue_date.

Uses common bond/loan tenors to estimate issue date:
- Senior notes: 10 years
- Secured notes: 7 years
- Term loans: 5-7 years
- Revolvers: 5 years

Usage:
    python scripts/backfill_issue_dates.py           # Preview changes
    python scripts/backfill_issue_dates.py --save    # Apply changes
"""

import argparse
import asyncio
import os
import re
import sys
from datetime import date

from dateutil.relativedelta import relativedelta
from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv()

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker

from app.core.config import get_settings
from app.models import DebtInstrument, Company

settings = get_settings()


# Default tenors by instrument type (in years)
DEFAULT_TENORS = {
    "senior_notes": 10,
    "senior_secured_notes": 7,
    "subordinated_notes": 10,
    "term_loan_b": 7,
    "term_loan_a": 5,
    "term_loan": 7,
    "revolver": 5,
    "abl": 5,
    "convertible_notes": 5,
    "commercial_paper": 1,
}


def estimate_issue_date(
    maturity_date: date,
    instrument_name: str,
    instrument_type: str,
) -> date:
    """Estimate issue date from maturity date and instrument type."""
    tenor_years = DEFAULT_TENORS.get(instrument_type.lower(), 7)

    # Check for tenor hints in name (e.g., "5-year", "10yr")
    tenor_match = re.search(r'(\d+)[-\s]?(?:year|yr)', instrument_name.lower())
    if tenor_match:
        tenor_years = int(tenor_match.group(1))

    # Calculate estimated issue date
    return maturity_date - relativedelta(years=tenor_years)


async def backfill_issue_dates(save: bool = False):
    """Backfill issue_date for debt instruments missing it."""

    database_url = settings.database_url
    if database_url.startswith("postgresql://"):
        database_url = database_url.replace("postgresql://", "postgresql+asyncpg://", 1)

    engine = create_async_engine(database_url, echo=False)
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with async_session() as db:
        # Get debt instruments with maturity but no issue date
        result = await db.execute(
            select(DebtInstrument, Company)
            .join(Company, DebtInstrument.company_id == Company.id)
            .where(DebtInstrument.issue_date.is_(None))
            .where(DebtInstrument.maturity_date.isnot(None))
            .where(DebtInstrument.is_active == True)
            .order_by(Company.ticker, DebtInstrument.name)
        )
        rows = result.all()

        print(f"Found {len(rows)} debt instruments to backfill")
        print()

        if not rows:
            print("Nothing to do.")
            await engine.dispose()
            return

        updates = []
        by_ticker = {}

        for debt, company in rows:
            estimated = estimate_issue_date(
                debt.maturity_date,
                debt.name,
                debt.instrument_type,
            )

            updates.append({
                "id": debt.id,
                "ticker": company.ticker,
                "name": debt.name[:50],
                "instrument_type": debt.instrument_type,
                "maturity_date": debt.maturity_date,
                "estimated_issue_date": estimated,
            })

            if company.ticker not in by_ticker:
                by_ticker[company.ticker] = 0
            by_ticker[company.ticker] += 1

        # Show summary by ticker
        print("Instruments to update by ticker:")
        for ticker, count in sorted(by_ticker.items()):
            print(f"  {ticker}: {count}")
        print()

        # Show sample updates
        print("Sample updates (first 20):")
        print("-" * 100)
        for u in updates[:20]:
            print(
                f"[{u['ticker']}] {u['name'][:40]:<40} | "
                f"{u['instrument_type']:<20} | "
                f"mat={u['maturity_date']} -> issue={u['estimated_issue_date']}"
            )
        print("-" * 100)
        print()

        if save:
            print("Applying updates...")
            count = 0
            for u in updates:
                await db.execute(
                    update(DebtInstrument)
                    .where(DebtInstrument.id == u["id"])
                    .values(
                        issue_date=u["estimated_issue_date"],
                        issue_date_estimated=True,  # Mark as estimated
                    )
                )
                count += 1

            await db.commit()
            print(f"Updated {count} debt instruments with estimated issue dates.")
            print("NOTE: issue_date_estimated=True for all updated records.")
        else:
            print("DRY RUN - no changes made. Use --save to apply updates.")

    await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Backfill issue_date for debt instruments")
    parser.add_argument("--save", action="store_true", help="Apply changes to database")
    args = parser.parse_args()

    asyncio.run(backfill_issue_dates(save=args.save))
