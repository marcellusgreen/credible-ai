#!/usr/bin/env python3
"""
Load ALL extraction results from JSON files into Neon database.

This script discovers all *_iterative.json files in the results directory
and loads them into the database.

Usage:
    python scripts/load_all_to_neon.py              # Load all
    python scripts/load_all_to_neon.py --ticker RIG # Load specific ticker
    python scripts/load_all_to_neon.py --dry-run    # Preview without loading
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

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from app.services.extraction import save_extraction_to_db


# CIK lookup for SEC filings (add as needed)
CIK_LOOKUP = {
    "AAPL": "0000320193",
    "MSFT": "0000789019",
    "GOOGL": "0001652044",
    "AMZN": "0001018724",
    "NVDA": "0001045810",
    "META": "0001326801",
    "TSLA": "0001318605",
    "JPM": "0000019617",
    "V": "0001403161",
    "UNH": "0000731766",
    "MA": "0001141391",
    "JNJ": "0000200406",
    "WMT": "0000104169",
    "PG": "0000080424",
    "HD": "0000354950",
    "BAC": "0000070858",
    "XOM": "0000034088",
    "CVX": "0000093410",
    "KO": "0000021344",
    "PEP": "0000077476",
    "COST": "0000909832",
    "ABBV": "0001551152",
    "MRK": "0000310158",
    "LLY": "0000059478",
    "AVGO": "0001730168",
    "TMO": "0000097745",
    "MCD": "0000063908",
    "CSCO": "0000858877",
    "ACN": "0001467373",
    "ABT": "0000001800",
    "DHR": "0000313616",
    "CRM": "0001108524",
    "AMD": "0000002488",
    "ADBE": "0000796343",
    "NKE": "0000320187",
    "TXN": "0000097476",
    "ORCL": "0001341439",
    "INTC": "0000050863",
    "NFLX": "0001065280",
    "IBM": "0000051143",
    "QCOM": "0000804328",
    "INTU": "0000896878",
    "AMGN": "0000318154",
    "SBUX": "0000829224",
    "GS": "0000886982",
    "MS": "0000895421",
    "WFC": "0000072971",
    "C": "0000831001",
    "BLK": "0001364742",
    "CAT": "0000018230",
    "DE": "0000315189",
    "BA": "0000012927",
    "GE": "0000040545",
    "RTX": "0000101829",
    "LMT": "0000936468",
    "HON": "0000773840",
    "UNP": "0000100885",
    "LOW": "0000060667",
    "MDLZ": "0001103982",
    "GILD": "0000882095",
    "BKNG": "0001075531",
    "AXP": "0000004962",
    "ISRG": "0001035267",
    "T": "0000732717",
    "VZ": "0000732712",
    "TMUS": "0001283699",
    "CHTR": "0001091667",
    "ATUS": "0001702780",
    "RIG": "0001451505",
    "CRWV": "0001769628",
    "VAL": "0001581673",
    "DO": "0000949039",
    "NE": "0001604164",
    "AAL": "0000006201",
    "UAL": "0000100517",
    "DAL": "0000027904",
    "CCL": "0000815097",
    "RCL": "0000884887",
    "NCLH": "0001513761",
    "MGM": "0000789570",
    "WYNN": "0001174922",
    "CZR": "0001590895",
    "LUMN": "0000018926",
    "DISH": "0001001082",
    "PARA": "0000813828",
    "WBD": "0001437107",
    "F": "0000037996",
    "GM": "0001467858",
    "OXY": "0000797468",
    "APA": "0000006769",
    "DVN": "0001090012",
    "COP": "0001163165",
    "SWN": "0000007340",
    "FANG": "0001539838",
    "HCA": "0000860730",
    "THC": "0000070318",
}


def convert_to_extraction_result(data: dict, ticker: str):
    """Convert JSON extraction data to ExtractionResult format."""
    from dataclasses import dataclass
    from typing import List, Optional

    @dataclass
    class EntityResult:
        name: str
        entity_type: str
        parent_name: Optional[str] = None
        jurisdiction: Optional[str] = None
        is_guarantor: bool = False
        is_borrower: bool = False
        is_vie: bool = False
        is_unrestricted: bool = False
        ownership_percentage: Optional[float] = None
        jv_partner_name: Optional[str] = None

    @dataclass
    class DebtResult:
        issuer_name: str
        name: str
        instrument_type: str
        seniority: str
        commitment: Optional[int] = None
        principal: Optional[int] = None
        outstanding: Optional[int] = None
        currency: str = "USD"
        interest_rate: Optional[int] = None
        rate_type: str = "fixed"
        spread_bps: Optional[int] = None
        benchmark: Optional[str] = None
        floor_bps: Optional[int] = None
        maturity_date: Optional[str] = None
        issue_date: Optional[str] = None
        is_drawn: bool = True
        cusip: Optional[str] = None
        isin: Optional[str] = None
        security_type: Optional[str] = None
        guarantor_names: Optional[List[str]] = None

    @dataclass
    class ExtractionResult:
        ticker: str
        entities: List[EntityResult]
        debt_instruments: List[DebtResult]

    # Convert entities
    entities = []
    for e in data.get("entities", []):
        entities.append(EntityResult(
            name=e.get("name", "Unknown"),
            entity_type=e.get("entity_type", "subsidiary"),
            parent_name=e.get("parent_name"),
            jurisdiction=e.get("jurisdiction"),
            is_guarantor=e.get("is_guarantor", False),
            is_borrower=e.get("is_borrower", False),
            is_vie=e.get("is_vie", False),
            is_unrestricted=e.get("is_unrestricted", False),
            ownership_percentage=e.get("ownership_percentage"),
            jv_partner_name=e.get("jv_partner_name"),
        ))

    # Convert debt instruments
    debts = []
    for d in data.get("debt_instruments", []):
        debts.append(DebtResult(
            issuer_name=d.get("issuer_name", data.get("entities", [{}])[0].get("name", "Unknown")),
            name=d.get("name", "Unknown"),
            instrument_type=d.get("instrument_type", "other"),
            seniority=d.get("seniority", "senior_unsecured"),
            commitment=d.get("commitment"),
            principal=d.get("principal"),
            outstanding=d.get("outstanding"),
            currency=d.get("currency", "USD"),
            interest_rate=d.get("interest_rate"),
            rate_type=d.get("rate_type", "fixed"),
            spread_bps=d.get("spread_bps"),
            benchmark=d.get("benchmark"),
            floor_bps=d.get("floor_bps"),
            maturity_date=d.get("maturity_date"),
            issue_date=d.get("issue_date"),
            is_drawn=d.get("is_drawn", True),
            cusip=d.get("cusip"),
            isin=d.get("isin"),
            security_type=d.get("security_type"),
            guarantor_names=d.get("guarantor_names"),
        ))

    return ExtractionResult(ticker=ticker, entities=entities, debt_instruments=debts)


def discover_extractions(results_dir: Path) -> list[tuple[str, Path]]:
    """Discover all extraction JSON files and return (ticker, path) tuples."""
    extractions = []

    for json_file in results_dir.glob("*_iterative.json"):
        ticker = json_file.stem.replace("_iterative", "").upper()
        extractions.append((ticker, json_file))

    return sorted(extractions, key=lambda x: x[0])


async def check_existing(db_session, ticker: str) -> bool:
    """Check if company already exists in database."""
    result = await db_session.execute(
        text("SELECT COUNT(*) FROM companies WHERE ticker = :ticker"),
        {"ticker": ticker}
    )
    count = result.scalar()
    return count > 0


async def load_extraction(
    ticker: str,
    json_path: Path,
    db_session,
    skip_existing: bool = True
) -> tuple[bool, str]:
    """Load a single extraction into database. Returns (success, message)."""

    # Check if already exists
    if skip_existing:
        exists = await check_existing(db_session, ticker)
        if exists:
            return True, "already exists"

    # Load JSON
    try:
        with open(json_path) as f:
            extraction_data = json.load(f)
    except Exception as e:
        return False, f"JSON read error: {e}"

    # Convert to ExtractionResult
    extraction_result = convert_to_extraction_result(extraction_data, ticker)

    # Get CIK if available
    cik = CIK_LOOKUP.get(ticker)

    # Save to database
    try:
        await save_extraction_to_db(db_session, extraction_result, ticker, cik)
        entity_count = len(extraction_data.get("entities", []))
        debt_count = len(extraction_data.get("debt_instruments", []))
        return True, f"{entity_count} entities, {debt_count} debts"
    except Exception as e:
        return False, f"DB error: {e}"


async def main():
    parser = argparse.ArgumentParser(description="Load extraction results to Neon database")
    parser.add_argument("--ticker", help="Specific ticker to load")
    parser.add_argument("--dry-run", action="store_true", help="Preview without loading")
    parser.add_argument("--force", action="store_true", help="Reload even if exists")
    parser.add_argument("--batch-size", type=int, default=10, help="Commit every N companies")
    args = parser.parse_args()

    load_dotenv()
    database_url = os.getenv("DATABASE_URL")

    if not database_url:
        print("ERROR: DATABASE_URL not set in .env")
        sys.exit(1)

    results_dir = Path(__file__).parent.parent / "results"

    # Discover extractions
    extractions = discover_extractions(results_dir)

    if args.ticker:
        extractions = [(t, p) for t, p in extractions if t == args.ticker.upper()]
        if not extractions:
            print(f"ERROR: No extraction found for {args.ticker}")
            sys.exit(1)

    print("=" * 70)
    print(f"LOADING {len(extractions)} EXTRACTIONS TO NEON DATABASE")
    print("=" * 70)

    if args.dry_run:
        print("\n[DRY RUN] Would load:")
        for ticker, path in extractions:
            print(f"  {ticker}: {path.name}")
        print(f"\nTotal: {len(extractions)} companies")
        return

    # Connect to database
    engine = create_async_engine(database_url, echo=False)
    async_session = async_sessionmaker(engine, expire_on_commit=False)

    loaded = 0
    skipped = 0
    failed = 0

    try:
        async with async_session() as session:
            for i, (ticker, json_path) in enumerate(extractions, 1):
                success, message = await load_extraction(
                    ticker, json_path, session,
                    skip_existing=not args.force
                )

                if success:
                    if "already exists" in message:
                        print(f"  [{i:3}/{len(extractions)}] {ticker}: SKIP ({message})")
                        skipped += 1
                    else:
                        print(f"  [{i:3}/{len(extractions)}] {ticker}: OK ({message})")
                        loaded += 1
                else:
                    print(f"  [{i:3}/{len(extractions)}] {ticker}: FAILED ({message})")
                    failed += 1

                # Commit in batches
                if i % args.batch_size == 0:
                    await session.commit()
                    print(f"    [Committed batch {i // args.batch_size}]")

            # Final commit
            await session.commit()

    finally:
        await engine.dispose()

    print("\n" + "=" * 70)
    print(f"SUMMARY: Loaded {loaded}, Skipped {skipped}, Failed {failed}")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(main())
