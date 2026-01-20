#!/usr/bin/env python3
"""
Re-run QA on specific companies to verify if discrepancies are real or false positives.

Usage:
    python scripts/rerun_qa.py --ticker TTWO
    python scripts/rerun_qa.py --tickers TTWO,KDP,ODFL,KSS
    python scripts/rerun_qa.py --scale-errors  # Re-run all companies with scale errors
"""

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

from app.services.qa_agent import QAAgent


# Companies with scale errors (>=85% difference) from our analysis
SCALE_ERROR_TICKERS = [
    "TTWO",   # 7 discrepancies - QA false positive (verified)
    "KDP",    # Commercial paper 10x error
    "ODFL",   # Series B Notes 10x error
    "KSS",    # Mixed errors
    "INTU",   # QA math error (expected_cents = $5)
    "MSFT",   # QA math error
    "DO",     # QA math error
    "CHS",    # QA math error
    "TJX",    # 3 discrepancies
    "VRSK",   # 4 discrepancies (small % errors)
    "WMT",    # 4 discrepancies
]


async def get_extraction_from_db(session: AsyncSession, ticker: str) -> dict:
    """Build extraction dict from database."""

    # Get company
    result = await session.execute(
        text("SELECT id, ticker, name FROM companies WHERE ticker = :ticker"),
        {"ticker": ticker}
    )
    company = result.fetchone()
    if not company:
        return {}

    company_id = company[0]

    # Get entities
    result = await session.execute(
        text("""
            SELECT id, name, entity_type, jurisdiction, is_guarantor, is_borrower,
                   is_vie, is_unrestricted, parent_id
            FROM entities WHERE company_id = :company_id
        """),
        {"company_id": company_id}
    )
    entities_raw = result.fetchall()

    # Build entity lookup
    entity_by_id = {str(e[0]): e for e in entities_raw}

    # Get debt instruments
    result = await session.execute(
        text("""
            SELECT id, name, outstanding, principal, interest_rate, maturity_date,
                   seniority, rate_type, issuer_id
            FROM debt_instruments WHERE company_id = :company_id
        """),
        {"company_id": company_id}
    )
    debt_raw = result.fetchall()

    # Build extraction dict
    extraction = {
        "ticker": ticker,
        "company_name": company[2],
        "entities": [],
        "debt_instruments": [],
    }

    for e in entities_raw:
        eid, name, etype, jurisdiction, is_guarantor, is_borrower, is_vie, is_unrestricted, parent_id = e
        parent_name = None
        if parent_id and str(parent_id) in entity_by_id:
            parent_name = entity_by_id[str(parent_id)][1]

        extraction["entities"].append({
            "name": name,
            "entity_type": etype,
            "jurisdiction": jurisdiction,
            "is_guarantor": is_guarantor,
            "is_borrower": is_borrower,
            "is_vie": is_vie,
            "is_unrestricted": is_unrestricted,
            "owners": [{"parent_name": parent_name, "ownership_pct": 100}] if parent_name else [],
        })

    for d in debt_raw:
        did, name, outstanding, principal, rate, maturity, seniority, rate_type, issuer_id = d
        issuer_name = None
        if issuer_id and str(issuer_id) in entity_by_id:
            issuer_name = entity_by_id[str(issuer_id)][1]

        extraction["debt_instruments"].append({
            "name": name,
            "issuer_name": issuer_name,
            "outstanding": outstanding,
            "principal": principal,
            "interest_rate": rate,
            "maturity_date": str(maturity) if maturity else None,
            "seniority": seniority,
            "rate_type": rate_type,
            "guarantor_names": [],
        })

    return extraction


async def get_filings_from_db(session: AsyncSession, ticker: str) -> dict:
    """Get filing content from document_sections table."""

    result = await session.execute(
        text("""
            SELECT ds.section_type, ds.content, ds.filing_date
            FROM document_sections ds
            JOIN companies c ON ds.company_id = c.id
            WHERE c.ticker = :ticker
            ORDER BY ds.filing_date DESC
        """),
        {"ticker": ticker}
    )

    filings = {}
    seen_types = set()

    for row in result.fetchall():
        section_type, content, filing_date = row
        # Only take most recent of each type
        if section_type not in seen_types:
            key = f"{section_type}_{filing_date}"
            filings[key] = content
            seen_types.add(section_type)

    return filings


async def run_qa_for_ticker(ticker: str, session: AsyncSession, qa_agent: QAAgent) -> dict:
    """Run QA for a single ticker."""

    print(f"\n{'='*60}")
    print(f"Running QA for {ticker}")
    print(f"{'='*60}")

    # Get extraction from DB
    extraction = await get_extraction_from_db(session, ticker)
    if not extraction.get("entities") and not extraction.get("debt_instruments"):
        return {"ticker": ticker, "error": "No extraction data in database"}

    print(f"  Entities: {len(extraction.get('entities', []))}")
    print(f"  Debt instruments: {len(extraction.get('debt_instruments', []))}")

    # Get filings from DB
    filings = await get_filings_from_db(session, ticker)
    if not filings:
        return {"ticker": ticker, "error": "No filing content in database"}

    print(f"  Filing sections: {list(filings.keys())}")

    # Run QA
    report = await qa_agent.run_qa(extraction, filings)

    # Print results
    print(f"\n  Overall Score: {report.overall_score:.0f}%")
    print(f"  Status: {report.overall_status}")

    for check in report.checks:
        icon = {"pass": "PASS", "fail": "FAIL", "warn": "WARN", "skip": "SKIP"}[check.status.value]
        print(f"    [{icon}] {check.name}: {check.message}")

        # Show debt verification details
        if check.name == "Debt Verification" and check.details:
            discrepancies = check.details.get("discrepancies", [])
            if discrepancies:
                print(f"        Discrepancies:")
                for d in discrepancies[:5]:
                    print(f"          - {d.get('instrument')}: extracted={d.get('extracted_cents')}, expected={d.get('filing_amount_cents')}")

    return report.to_dict()


async def main():
    parser = argparse.ArgumentParser(description="Re-run QA on specific companies")
    parser.add_argument("--ticker", help="Single ticker")
    parser.add_argument("--tickers", help="Comma-separated tickers")
    parser.add_argument("--scale-errors", action="store_true", help="Run all scale error companies")
    parser.add_argument("--save", action="store_true", help="Save QA reports to files")
    args = parser.parse_args()

    # Determine tickers to process
    if args.ticker:
        tickers = [args.ticker.upper()]
    elif args.tickers:
        tickers = [t.strip().upper() for t in args.tickers.split(",")]
    elif args.scale_errors:
        tickers = SCALE_ERROR_TICKERS
    else:
        print("Error: Specify --ticker, --tickers, or --scale-errors")
        sys.exit(1)

    # Check API key
    gemini_api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    if not gemini_api_key:
        print("Error: GEMINI_API_KEY required")
        sys.exit(1)

    # Database connection
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        print("Error: DATABASE_URL not set")
        sys.exit(1)

    if database_url.startswith("postgresql://"):
        database_url = database_url.replace("postgresql://", "postgresql+asyncpg://", 1)

    engine = create_async_engine(database_url, echo=False)
    async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    # Initialize QA agent
    qa_agent = QAAgent(gemini_api_key)

    # Run QA for each ticker
    results = []
    for ticker in tickers:
        async with async_session() as session:
            result = await run_qa_for_ticker(ticker, session, qa_agent)
            results.append(result)

        # Small delay between companies
        if ticker != tickers[-1]:
            await asyncio.sleep(1)

    # Summary
    print(f"\n{'='*60}")
    print("SUMMARY")
    print(f"{'='*60}")

    for r in results:
        if "error" in r:
            print(f"  {r['ticker']}: ERROR - {r['error']}")
        else:
            score = r.get("overall_score", 0)
            status = r.get("overall_status", "unknown")
            print(f"  {r['ticker']}: {score:.0f}% ({status})")

    print(f"\nTotal QA cost: ${qa_agent.total_cost:.4f}")

    # Save results if requested
    if args.save:
        os.makedirs("results/qa_rerun", exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        for r in results:
            if "error" not in r:
                ticker = r.get("ticker", "unknown")
                path = f"results/qa_rerun/{ticker.lower()}_qa_{timestamp}.json"
                with open(path, "w") as f:
                    json.dump(r, f, indent=2, default=str)
                print(f"Saved: {path}")

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
