#!/usr/bin/env python3
"""
CLI script for extracting company data from SEC filings.

Usage:
    python scripts/extract.py --cik 0001193125 --ticker NVDA
    python scripts/extract.py --cik 0001804220 --ticker CRWV
"""

import argparse
import asyncio
import os
import sys
from datetime import datetime

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

from app.models import Base
from app.services.extraction import ExtractionService, save_extraction_to_db


async def run_extraction(
    database_url: str,
    anthropic_api_key: str,
    cik: str,
    ticker: str,
    skip_db: bool = False,
    sec_api_key: str = None,
):
    """Run the full extraction pipeline."""
    print(f"\n{'='*60}")
    print(f"Extracting: {ticker} (CIK: {cik})")
    print(f"{'='*60}\n")

    # Initialize extraction service
    service = ExtractionService(anthropic_api_key, sec_api_key=sec_api_key)

    try:
        # Step 1: Download and extract
        print("Step 1: Downloading 10-K from SEC EDGAR...")
        start_time = datetime.now()

        extraction = await service.extract_company(cik, ticker)

        elapsed = (datetime.now() - start_time).total_seconds()
        print(f"  [OK] Extraction complete in {elapsed:.1f}s")
        print(f"  - Company: {extraction.company_name}")
        print(f"  - Entities found: {len(extraction.entities)}")
        print(f"  - Debt instruments found: {len(extraction.debt_instruments)}")

        if extraction.uncertainties:
            print(f"  - Uncertainties: {len(extraction.uncertainties)}")
            for u in extraction.uncertainties[:3]:
                print(f"    • {u}")

        # Print entity summary
        print("\n  Entities:")
        for e in extraction.entities[:10]:
            parent = f" (parent: {e.parent_name})" if e.parent_name else " (root)"
            guarantor = " [G]" if e.is_guarantor else ""
            print(f"    • {e.name} ({e.entity_type}){parent}{guarantor}")
        if len(extraction.entities) > 10:
            print(f"    ... and {len(extraction.entities) - 10} more")

        # Print debt summary
        print("\n  Debt Instruments:")
        for d in extraction.debt_instruments[:5]:
            amount = d.outstanding or d.principal or 0
            amount_str = f"${amount / 100:,.0f}" if amount else "N/A"
            print(f"    • {d.name}")
            print(f"      {d.seniority}, {d.security_type or 'unsecured'}, {amount_str}")
            print(f"      Issuer: {d.issuer_name}, Guarantors: {len(d.guarantor_names)}")
        if len(extraction.debt_instruments) > 5:
            print(f"    ... and {len(extraction.debt_instruments) - 5} more")

        if skip_db:
            print("\n  [WARN] Skipping database save (--skip-db flag)")
            return

        # Step 2: Save to database
        print("\nStep 2: Saving to database...")

        engine = create_async_engine(database_url, echo=False)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        session_maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

        async with session_maker() as db:
            company_id = await save_extraction_to_db(
                db=db,
                extraction=extraction,
                ticker=ticker,
                cik=cik,
                filing_date=datetime.now().date(),
            )
            print(f"  [OK] Saved to database (company_id: {company_id})")

        await engine.dispose()

    finally:
        await service.close()

    print(f"\n{'='*60}")
    print("Extraction complete!")
    print(f"{'='*60}\n")


def main():
    parser = argparse.ArgumentParser(description="Extract company data from SEC 10-K filings")
    parser.add_argument("--cik", required=True, help="SEC CIK number (e.g., 0001804220)")
    parser.add_argument("--ticker", required=True, help="Stock ticker (e.g., CRWV)")
    parser.add_argument("--skip-db", action="store_true", help="Skip database save (extraction only)")
    parser.add_argument("--database-url", help="Database URL (or set DATABASE_URL env var)")

    args = parser.parse_args()

    # Load environment variables
    load_dotenv()

    # Get API keys
    anthropic_api_key = os.getenv("ANTHROPIC_API_KEY")
    if not anthropic_api_key:
        print("Error: ANTHROPIC_API_KEY environment variable is required")
        sys.exit(1)

    # SEC-API.io key (optional, but recommended for faster extraction)
    sec_api_key = os.getenv("SEC_API_KEY")
    if not sec_api_key:
        print("Note: SEC_API_KEY not set. Using direct SEC EDGAR (may be rate-limited).")
        print("      Get a free API key at: https://sec-api.io/")

    database_url = args.database_url or os.getenv("DATABASE_URL")
    if not database_url and not args.skip_db:
        print("Error: DATABASE_URL environment variable is required (or use --skip-db)")
        sys.exit(1)

    # Run extraction
    asyncio.run(
        run_extraction(
            database_url=database_url or "",
            anthropic_api_key=anthropic_api_key,
            sec_api_key=sec_api_key,
            cik=args.cik,
            ticker=args.ticker,
            skip_db=args.skip_db,
        )
    )


if __name__ == "__main__":
    main()
