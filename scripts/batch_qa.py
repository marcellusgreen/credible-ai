#!/usr/bin/env python3
"""
Batch QA script for verifying extraction quality across all companies.

Runs the QA agent on each company's extraction and generates a summary report.

Usage:
    python scripts/batch_qa.py                    # All companies
    python scripts/batch_qa.py --limit 10         # First 10 companies
    python scripts/batch_qa.py --ticker AAPL      # Single company
    python scripts/batch_qa.py --failing-only     # Only companies with prior QA failures
    python scripts/batch_qa.py --skip-passing     # Skip companies with score >= 85

Environment variables:
    GEMINI_API_KEY - Required for QA checks
    SEC_API_KEY - Optional, for faster filing retrieval
"""

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime
from typing import Optional

# Force unbuffered output with UTF-8 encoding
sys.stdout.reconfigure(line_buffering=True, encoding='utf-8', errors='replace')
sys.stderr.reconfigure(line_buffering=True, encoding='utf-8', errors='replace')

# Add parent dir to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker

from app.models import Company, Entity, DebtInstrument, ExtractionMetadata
from app.services.qa_agent import QAAgent, QAReport
from app.services.extraction import SecApiClient, SECEdgarClient


async def get_companies_for_qa(
    session: AsyncSession,
    ticker: Optional[str] = None,
    skip_passing: bool = False,
    failing_only: bool = False,
) -> list[dict]:
    """Get companies to run QA on."""

    # Get all companies with entity/debt counts
    entity_subq = (
        select(Entity.company_id, func.count(Entity.id).label("entity_count"))
        .group_by(Entity.company_id)
        .subquery()
    )
    debt_subq = (
        select(DebtInstrument.company_id, func.count(DebtInstrument.id).label("debt_count"))
        .group_by(DebtInstrument.company_id)
        .subquery()
    )

    query = (
        select(
            Company.id,
            Company.ticker,
            Company.cik,
            Company.name,
            entity_subq.c.entity_count,
            debt_subq.c.debt_count,
        )
        .outerjoin(entity_subq, Company.id == entity_subq.c.company_id)
        .outerjoin(debt_subq, Company.id == debt_subq.c.company_id)
        .order_by(Company.ticker)
    )

    if ticker:
        query = query.where(Company.ticker == ticker.upper())

    result = await session.execute(query)

    companies = []
    for row in result.all():
        companies.append({
            "id": row.id,
            "ticker": row.ticker,
            "cik": row.cik,
            "name": row.name,
            "entity_count": row.entity_count or 0,
            "debt_count": row.debt_count or 0,
        })

    # Get prior QA scores from extraction metadata if filtering
    if skip_passing or failing_only:
        metadata_result = await session.execute(
            select(ExtractionMetadata.company_id, ExtractionMetadata.qa_score)
        )
        qa_scores = {str(row.company_id): row.qa_score for row in metadata_result.all()}

        filtered = []
        for c in companies:
            score = qa_scores.get(str(c["id"]))
            if failing_only and score is not None and score < 85:
                filtered.append(c)
            elif skip_passing and (score is None or score < 85):
                filtered.append(c)
            elif not skip_passing and not failing_only:
                filtered.append(c)
        companies = filtered

    return companies


async def build_extraction_from_db(session: AsyncSession, company_id) -> dict:
    """Build extraction dict from database for QA verification."""

    # Get company
    company_result = await session.execute(
        select(Company).where(Company.id == company_id)
    )
    company = company_result.scalar_one_or_none()
    if not company:
        return {}

    # Get entities
    entities_result = await session.execute(
        select(Entity).where(Entity.company_id == company_id)
    )
    entities = list(entities_result.scalars().all())

    # Build entity lookup for parent references
    entity_by_id = {str(e.id): e for e in entities}

    # Get debt instruments
    debt_result = await session.execute(
        select(DebtInstrument).where(DebtInstrument.company_id == company_id)
    )
    debt_instruments = list(debt_result.scalars().all())

    # Build extraction dict
    extraction = {
        "ticker": company.ticker,
        "company_name": company.name,
        "entities": [],
        "debt_instruments": [],
    }

    for e in entities:
        parent_name = None
        if e.parent_id and str(e.parent_id) in entity_by_id:
            parent_name = entity_by_id[str(e.parent_id)].name

        entity_dict = {
            "name": e.name,
            "entity_type": e.entity_type,
            "jurisdiction": e.jurisdiction,
            "is_guarantor": e.is_guarantor,
            "is_borrower": e.is_borrower,
            "is_vie": e.is_vie,
            "is_unrestricted": e.is_unrestricted,
            "owners": [{"parent_name": parent_name, "ownership_pct": 100}] if parent_name else [],
        }
        extraction["entities"].append(entity_dict)

    for d in debt_instruments:
        issuer_name = None
        if d.issuer_id and str(d.issuer_id) in entity_by_id:
            issuer_name = entity_by_id[str(d.issuer_id)].name

        debt_dict = {
            "name": d.name,
            "issuer_name": issuer_name,
            "outstanding": d.outstanding,
            "principal": d.principal,
            "interest_rate": d.interest_rate,  # in basis points
            "maturity_date": str(d.maturity_date) if d.maturity_date else None,
            "seniority": d.seniority,
            "rate_type": d.rate_type,
            "guarantor_names": [],  # Would need to join guarantees table
        }
        extraction["debt_instruments"].append(debt_dict)

    return extraction


async def run_qa_for_company(
    session: AsyncSession,
    company: dict,
    qa_agent: QAAgent,
    sec_client: Optional[SecApiClient],
    edgar_client: SECEdgarClient,
) -> tuple[Optional[QAReport], Optional[str]]:
    """Run QA for a single company. Returns (report, error_message)."""

    ticker = company["ticker"]
    cik = company["cik"]

    try:
        # Build extraction from database
        extraction = await build_extraction_from_db(session, company["id"])

        if not extraction.get("entities") and not extraction.get("debt_instruments"):
            return None, "No extraction data in database"

        # Download filings for QA
        filings = {}
        if sec_client:
            try:
                filings = await sec_client.get_all_relevant_filings(ticker)
                exhibit_21 = sec_client.get_exhibit_21(ticker)
                if exhibit_21:
                    filings["exhibit_21"] = exhibit_21
            except Exception as e:
                # Fall back to EDGAR
                pass

        if not filings:
            filings = await edgar_client.get_all_relevant_filings(cik)

        if not filings:
            return None, "No SEC filings found"

        # Run QA
        report = await qa_agent.run_qa(extraction, filings)
        return report, None

    except Exception as e:
        return None, str(e)[:200]


async def main():
    parser = argparse.ArgumentParser(description="Batch QA for all companies")
    parser.add_argument("--ticker", help="Single ticker to process")
    parser.add_argument("--limit", type=int, help="Limit number of companies")
    parser.add_argument("--start-from", type=int, default=0, help="Start from company N")
    parser.add_argument("--skip-passing", action="store_true",
                       help="Skip companies with QA score >= 85")
    parser.add_argument("--failing-only", action="store_true",
                       help="Only process companies with prior QA failures")
    parser.add_argument("--delay", type=int, default=2,
                       help="Delay between companies (seconds)")
    parser.add_argument("--save-reports", action="store_true",
                       help="Save individual QA reports to files")
    args = parser.parse_args()

    # Check for API keys
    gemini_api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    if not gemini_api_key:
        print("Error: GEMINI_API_KEY required for QA verification")
        sys.exit(1)

    sec_api_key = os.getenv("SEC_API_KEY")

    # Database connection
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        print("Error: DATABASE_URL not set")
        sys.exit(1)

    if database_url.startswith("postgresql://"):
        database_url = database_url.replace("postgresql://", "postgresql+asyncpg://", 1)

    # Create engine with connection recycling to handle long-running sessions
    engine = create_async_engine(
        database_url,
        echo=False,
        pool_pre_ping=True,  # Test connections before use
        pool_recycle=300,  # Recycle connections after 5 minutes
    )
    async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    # Initialize clients
    qa_agent = QAAgent(gemini_api_key)
    sec_client = SecApiClient(sec_api_key) if sec_api_key else None
    edgar_client = SECEdgarClient()

    # Get companies
    async with async_session() as session:
        companies = await get_companies_for_qa(
            session,
            ticker=args.ticker,
            skip_passing=args.skip_passing,
            failing_only=args.failing_only,
        )

    print(f"Found {len(companies)} companies for QA")

    # Apply start-from and limit
    if args.start_from > 0:
        companies = companies[args.start_from:]
        print(f"Starting from company {args.start_from}, {len(companies)} remaining")

    if args.limit:
        companies = companies[:args.limit]
        print(f"Limited to {len(companies)} companies")

    print(f"\nStarting batch QA...")
    print(f"Using SEC-API: {'Yes' if sec_client else 'No (EDGAR only)'}")
    print("=" * 80)

    # Results tracking
    results = {
        "passed": 0,
        "warned": 0,
        "failed": 0,
        "skipped": 0,
        "errors": 0,
    }

    all_reports = []
    failed_companies = []

    for i, company in enumerate(companies):
        ticker = company["ticker"]
        name = company["name"]

        print(f"\n[{i+1}/{len(companies)}] {ticker} - {name}")
        print(f"  Entities: {company['entity_count']}, Debt: {company['debt_count']}")

        if company["entity_count"] == 0 and company["debt_count"] == 0:
            print(f"  SKIPPED: No extraction data")
            results["skipped"] += 1
            continue

        async with async_session() as session:
            report, error = await run_qa_for_company(
                session, company, qa_agent, sec_client, edgar_client
            )

        if error:
            print(f"  ERROR: {error}")
            results["errors"] += 1
            failed_companies.append({"ticker": ticker, "error": error})
            continue

        if report:
            score = report.overall_score
            status = report.overall_status

            status_icon = {
                "pass": "[PASS]",
                "fail": "[FAIL]",
                "needs_review": "[WARN]",
            }.get(status, "[????]")

            print(f"  {status_icon} Score: {score:.0f}%")

            # Print check summaries
            for check in report.checks:
                check_icon = {
                    "pass": "OK",
                    "fail": "FAIL",
                    "warn": "WARN",
                    "skip": "SKIP",
                }.get(check.status.value, "????")
                print(f"    [{check_icon}] {check.name}")

            if status == "pass":
                results["passed"] += 1
            elif status == "fail":
                results["failed"] += 1
                failed_companies.append({
                    "ticker": ticker,
                    "score": score,
                    "issues": [c.message for c in report.checks if c.status.value == "fail"]
                })
            else:
                results["warned"] += 1

            all_reports.append({
                "ticker": ticker,
                "score": score,
                "status": status,
                "checks": [
                    {"name": c.name, "status": c.status.value, "message": c.message}
                    for c in report.checks
                ],
            })

            # Save individual report if requested
            if args.save_reports:
                os.makedirs("results/qa", exist_ok=True)
                report_path = f"results/qa/{ticker.lower()}_qa.json"
                with open(report_path, "w") as f:
                    json.dump(report.to_dict(), f, indent=2, default=str)

        # Delay between companies
        if i < len(companies) - 1:
            await asyncio.sleep(args.delay)

    # Summary
    print("\n" + "=" * 80)
    print("BATCH QA SUMMARY")
    print("=" * 80)
    print(f"  Total companies:  {len(companies)}")
    print(f"  Passed (>=85%):   {results['passed']}")
    print(f"  Needs review:     {results['warned']}")
    print(f"  Failed:           {results['failed']}")
    print(f"  Skipped:          {results['skipped']}")
    print(f"  Errors:           {results['errors']}")
    print(f"  Total QA cost:    ${qa_agent.total_cost:.4f}")

    if all_reports:
        avg_score = sum(r["score"] for r in all_reports) / len(all_reports)
        print(f"  Average score:    {avg_score:.1f}%")

    if failed_companies:
        print(f"\nFailed companies ({len(failed_companies)}):")
        for fc in failed_companies[:10]:
            if "score" in fc:
                print(f"  {fc['ticker']}: {fc['score']:.0f}% - {fc.get('issues', ['Unknown'])[0]}")
            else:
                print(f"  {fc['ticker']}: {fc['error']}")

    # Save summary report
    os.makedirs("results", exist_ok=True)
    summary_path = f"results/qa_summary_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    with open(summary_path, "w") as f:
        json.dump({
            "timestamp": datetime.now().isoformat(),
            "summary": results,
            "total_cost": qa_agent.total_cost,
            "average_score": avg_score if all_reports else None,
            "reports": all_reports,
            "failed_companies": failed_companies,
        }, f, indent=2, default=str)
    print(f"\nSaved summary to {summary_path}")

    # Cleanup
    await edgar_client.close()
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
