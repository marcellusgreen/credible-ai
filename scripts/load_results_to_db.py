#!/usr/bin/env python3
"""
Load existing extraction results from JSON files into the database.

Usage:
    python scripts/load_results_to_db.py
    python scripts/load_results_to_db.py --ticker AAPL
"""

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

# Add parent to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from app.services.extraction import save_extraction_to_db
from scripts.extract_tiered import convert_to_extraction_result


# Known companies with their CIKs
COMPANIES = {
    "AAPL": "0000320193",
    "CRWV": "0001769628",
    "RIG": "0001451505",
    "ATUS": "0001702780",
    # Failed extractions to retry
    "AMZN": "0001018724",
    "BAC": "0000070858",
    "BA": "0000012927",
    "CAT": "0000018230",
    "MGM": "0000789570",
    "WYNN": "0001174922",
    "THC": "0000070318",
    "KDP": "0001418135",
    "HSY": "0000047111",
    "VNO": "0000899629",
    "SLG": "0001040971",
    "RCL": "0000884887",
    "NCLH": "0001513761",
}


async def load_extraction(ticker: str, results_dir: Path, database_url: str) -> bool:
    """Load a single extraction from JSON into database."""
    ticker = ticker.upper()

    # Find the iterative extraction file
    json_path = results_dir / f"{ticker.lower()}_iterative.json"
    if not json_path.exists():
        # Try alternate names
        json_path = results_dir / f"{ticker.lower()}_extraction.json"
        if not json_path.exists():
            print(f"  [SKIP] No extraction file found for {ticker}")
            return False

    print(f"\n  Loading {ticker} from {json_path.name}...")

    with open(json_path) as f:
        extraction_data = json.load(f)

    # Convert to ExtractionResult format
    extraction_result = convert_to_extraction_result(extraction_data, ticker)

    # Get CIK
    cik = COMPANIES.get(ticker)

    # Save to database
    engine = create_async_engine(database_url, echo=False)
    async_session = async_sessionmaker(engine, expire_on_commit=False)

    try:
        async with async_session() as session:
            await save_extraction_to_db(session, extraction_result, ticker, cik)
            await session.commit()
            print(f"    [OK] Loaded {ticker}: {len(extraction_data.get('entities', []))} entities, {len(extraction_data.get('debt_instruments', []))} debt instruments")
            return True
    except Exception as e:
        print(f"    [ERROR] Failed to load {ticker}: {e}")
        return False
    finally:
        await engine.dispose()


async def main():
    parser = argparse.ArgumentParser(description="Load extraction results to database")
    parser.add_argument("--ticker", help="Specific ticker to load (default: all)")
    args = parser.parse_args()

    load_dotenv()
    database_url = os.getenv("DATABASE_URL")

    if not database_url:
        print("ERROR: DATABASE_URL not set in environment")
        sys.exit(1)

    results_dir = Path(__file__).parent.parent / "results"

    print("="*60)
    print("LOADING EXTRACTIONS TO DATABASE")
    print("="*60)

    if args.ticker:
        # Load single ticker
        tickers = [args.ticker.upper()]
    else:
        # Load all available
        tickers = list(COMPANIES.keys())

    loaded = 0
    failed = 0

    for ticker in tickers:
        success = await load_extraction(ticker, results_dir, database_url)
        if success:
            loaded += 1
        else:
            failed += 1

    print(f"\n{'='*60}")
    print(f"SUMMARY: Loaded {loaded}, Failed/Skipped {failed}")
    print("="*60)


if __name__ == "__main__":
    asyncio.run(main())
