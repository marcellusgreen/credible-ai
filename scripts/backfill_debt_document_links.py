#!/usr/bin/env python3
"""
Backfill debt instrument to document links.

Matches debt instruments (bonds, loans) to their governing legal documents
(indentures, credit agreements) using the document_matching service.

Usage:
    # Single company
    python scripts/backfill_debt_document_links.py --ticker CHTR

    # All companies
    python scripts/backfill_debt_document_links.py --all

    # Dry run (don't save to database)
    python scripts/backfill_debt_document_links.py --all --dry-run

    # With minimum confidence threshold
    python scripts/backfill_debt_document_links.py --all --min-confidence 0.5

Environment variables:
    DATABASE_URL - PostgreSQL connection string
"""

import argparse
import io
import sys
from collections import defaultdict

# Handle Windows encoding
if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

from sqlalchemy import select, func

from script_utils import get_db_session, print_header, run_async
from app.models import Company, DebtInstrument, DocumentSection, DebtInstrumentDocument
from app.services.document_matching import (
    match_debt_instruments_to_documents,
    store_document_links,
    CompanyMatchReport,
    BOND_TYPES,
    LOAN_TYPES,
)


async def get_companies_with_debt(session) -> list[Company]:
    """Get all companies that have debt instruments."""
    result = await session.execute(
        select(Company)
        .join(DebtInstrument)
        .where(DebtInstrument.is_active == True)
        .distinct()
        .order_by(Company.ticker)
    )
    return list(result.scalars().all())


async def get_company_by_ticker(session, ticker: str) -> Company:
    """Get a single company by ticker."""
    result = await session.execute(
        select(Company).where(Company.ticker == ticker.upper())
    )
    return result.scalar_one_or_none()


async def get_company_stats(session, company_id) -> dict:
    """Get debt/document stats for a company."""
    # Count debt instruments by type
    result = await session.execute(
        select(DebtInstrument.instrument_type, func.count(DebtInstrument.id))
        .where(DebtInstrument.company_id == company_id)
        .where(DebtInstrument.is_active == True)
        .group_by(DebtInstrument.instrument_type)
    )
    type_counts = {row[0]: row[1] for row in result.fetchall()}

    # Count documents by type
    result = await session.execute(
        select(DocumentSection.section_type, func.count(DocumentSection.id))
        .where(DocumentSection.company_id == company_id)
        .where(DocumentSection.section_type.in_(["indenture", "credit_agreement"]))
        .group_by(DocumentSection.section_type)
    )
    doc_counts = {row[0]: row[1] for row in result.fetchall()}

    # Count existing links
    result = await session.execute(
        select(func.count(DebtInstrumentDocument.id))
        .join(DebtInstrument)
        .where(DebtInstrument.company_id == company_id)
    )
    existing_links = result.scalar() or 0

    return {
        "instrument_types": type_counts,
        "document_types": doc_counts,
        "existing_links": existing_links,
    }


async def backfill_company(
    session,
    company: Company,
    min_confidence: float,
    dry_run: bool,
    replace_existing: bool,
) -> dict:
    """
    Backfill document links for a single company.

    Returns summary dict with stats and any errors.
    """
    ticker = company.ticker

    try:
        # Get company stats first
        stats = await get_company_stats(session, company.id)

        bonds_count = sum(
            count for itype, count in stats["instrument_types"].items()
            if itype.lower() in BOND_TYPES or any(bt in itype.lower() for bt in BOND_TYPES)
        )
        loans_count = sum(
            count for itype, count in stats["instrument_types"].items()
            if itype.lower() in LOAN_TYPES or any(lt in itype.lower() for lt in LOAN_TYPES)
        )
        indentures = stats["document_types"].get("indenture", 0)
        credit_agreements = stats["document_types"].get("credit_agreement", 0)

        # Skip if no documents to match against
        if indentures == 0 and credit_agreements == 0:
            return {
                "ticker": ticker,
                "status": "skip",
                "reason": "no_documents",
                "bonds": bonds_count,
                "loans": loans_count,
            }

        # Run matching algorithm
        report = await match_debt_instruments_to_documents(
            session,
            company.id,
            min_confidence=min_confidence,
        )

        if dry_run:
            high_conf_matches = [m for m in report.matches if m.match_confidence >= 0.70]
            low_conf_matches = [m for m in report.matches if 0.50 <= m.match_confidence < 0.70]
            return {
                "ticker": ticker,
                "status": "dry_run",
                "total_instruments": report.total_instruments,
                "matched_high_confidence": len(high_conf_matches),
                "matched_low_confidence": len(low_conf_matches),
                "unmatched": report.unmatched,
                "bonds": bonds_count,
                "loans": loans_count,
                "indentures": indentures,
                "credit_agreements": credit_agreements,
            }

        # Store links
        created = await store_document_links(
            session,
            report.matches,
            created_by="algorithm",
            replace_existing=replace_existing,
        )

        return {
            "ticker": ticker,
            "status": "success",
            "total_instruments": report.total_instruments,
            "matched_high_confidence": report.matched_high_confidence,
            "matched_low_confidence": report.matched_low_confidence,
            "unmatched": report.unmatched,
            "links_created": created,
            "bonds": bonds_count,
            "loans": loans_count,
            "indentures": indentures,
            "credit_agreements": credit_agreements,
        }

    except Exception as e:
        error_msg = str(e)[:200].encode('ascii', 'replace').decode('ascii')
        return {
            "ticker": ticker,
            "status": "error",
            "reason": error_msg,
        }


async def main():
    parser = argparse.ArgumentParser(
        description="Backfill debt instrument to document links"
    )
    parser.add_argument("--ticker", help="Single company ticker")
    parser.add_argument("--all", action="store_true", help="Process all companies")
    parser.add_argument("--dry-run", action="store_true", help="Don't save to database")
    parser.add_argument(
        "--min-confidence",
        type=float,
        default=0.50,
        help="Minimum confidence to store link (default: 0.50)"
    )
    parser.add_argument(
        "--replace",
        action="store_true",
        help="Replace existing links (default: skip companies with links)"
    )
    parser.add_argument("--offset", type=int, default=0, help="Skip first N companies")
    parser.add_argument("--limit", type=int, help="Max companies to process")
    args = parser.parse_args()

    if not any([args.ticker, args.all]):
        parser.print_help()
        print("\nError: Specify --ticker or --all")
        sys.exit(1)

    print_header("BACKFILL DEBT INSTRUMENT -> DOCUMENT LINKS")

    async with get_db_session() as session:
        # Get companies to process
        if args.ticker:
            company = await get_company_by_ticker(session, args.ticker)
            if not company:
                print(f"Error: Company {args.ticker} not found")
                sys.exit(1)
            companies = [company]
        else:
            companies = await get_companies_with_debt(session)
            print(f"Found {len(companies)} companies with debt instruments")

        # Apply offset and limit
        if args.offset > 0:
            companies = companies[args.offset:]
            print(f"Skipping first {args.offset}, processing from #{args.offset + 1}")
        if args.limit:
            companies = companies[:args.limit]
            print(f"Limiting to {args.limit} companies")

        print(f"\nCompanies: {len(companies)}")
        print(f"Dry run: {args.dry_run}")
        print(f"Min confidence: {args.min_confidence}")
        print(f"Replace existing: {args.replace}")
        print()

        # Process companies
        results = []
        for i, company in enumerate(companies):
            print(f"[{i+1}/{len(companies)}] {company.ticker}...", end=" ", flush=True)

            result = await backfill_company(
                session,
                company,
                min_confidence=args.min_confidence,
                dry_run=args.dry_run,
                replace_existing=args.replace,
            )
            results.append(result)

            # Print inline status
            if result["status"] == "success":
                created = result.get("links_created", 0)
                high = result.get("matched_high_confidence", 0)
                low = result.get("matched_low_confidence", 0)
                unmatched = result.get("unmatched", 0)
                print(f"OK ({created} links: {high} high, {low} low conf, {unmatched} unmatched)")
            elif result["status"] == "dry_run":
                high = result.get("matched_high_confidence", 0)
                low = result.get("matched_low_confidence", 0)
                unmatched = result.get("unmatched", 0)
                print(f"[DRY RUN] ({high} high, {low} low conf, {unmatched} unmatched)")
            elif result["status"] == "skip":
                print(f"SKIP ({result.get('reason', 'unknown')})")
            else:
                print(f"ERROR: {result.get('reason', 'unknown')[:50]}")

        # Summary
        print(f"\n{'='*80}")
        print("SUMMARY")
        print(f"{'='*80}")

        success = [r for r in results if r["status"] == "success"]
        skipped = [r for r in results if r["status"] == "skip"]
        errors = [r for r in results if r["status"] == "error"]
        dry_runs = [r for r in results if r["status"] == "dry_run"]

        print(f"\nCompanies processed:")
        print(f"  Success:  {len(success)}")
        print(f"  Skipped:  {len(skipped)}")
        print(f"  Errors:   {len(errors)}")
        if dry_runs:
            print(f"  Dry run:  {len(dry_runs)}")

        # Aggregate match stats
        total_instruments = sum(r.get("total_instruments", 0) for r in success + dry_runs)
        total_high_conf = sum(r.get("matched_high_confidence", 0) for r in success + dry_runs)
        total_low_conf = sum(r.get("matched_low_confidence", 0) for r in success + dry_runs)
        total_unmatched = sum(r.get("unmatched", 0) for r in success + dry_runs)
        total_links_created = sum(r.get("links_created", 0) for r in success)

        print(f"\nMatching results:")
        print(f"  Total instruments:      {total_instruments:>6}")
        print(f"  High confidence (>=0.7):{total_high_conf:>6}")
        print(f"  Low confidence (0.5-0.7):{total_low_conf:>5}")
        print(f"  Unmatched (<0.5):       {total_unmatched:>6}")

        if not args.dry_run:
            print(f"\n  Links created:          {total_links_created:>6}")

        # Coverage calculation
        if total_instruments > 0:
            coverage_pct = (total_high_conf + total_low_conf) / total_instruments * 100
            print(f"\n  Coverage rate:          {coverage_pct:>5.1f}%")

        # Companies with full coverage
        full_coverage = [
            r for r in success + dry_runs
            if r.get("unmatched", 0) == 0 and r.get("total_instruments", 0) > 0
        ]
        partial_coverage = [
            r for r in success + dry_runs
            if r.get("unmatched", 0) > 0 and (r.get("matched_high_confidence", 0) + r.get("matched_low_confidence", 0)) > 0
        ]
        no_coverage = [
            r for r in success + dry_runs
            if (r.get("matched_high_confidence", 0) + r.get("matched_low_confidence", 0)) == 0 and r.get("total_instruments", 0) > 0
        ]

        print(f"\nCompany coverage breakdown:")
        print(f"  Full coverage (100%):   {len(full_coverage):>6}")
        print(f"  Partial coverage:       {len(partial_coverage):>6}")
        print(f"  No matches:             {len(no_coverage):>6}")

        # List companies with gaps
        if partial_coverage or no_coverage:
            print(f"\n{'='*80}")
            print("COMPANIES NEEDING ATTENTION")
            print(f"{'='*80}")

            if no_coverage:
                print("\nNo matches found (need document extraction or manual linking):")
                for r in sorted(no_coverage, key=lambda x: x["ticker"]):
                    print(f"  {r['ticker']}: {r.get('total_instruments', 0)} instruments, "
                          f"{r.get('indentures', 0)} indentures, {r.get('credit_agreements', 0)} credit agreements")

            if partial_coverage:
                print("\nPartial coverage (some instruments unmatched):")
                for r in sorted(partial_coverage, key=lambda x: -x.get("unmatched", 0))[:20]:
                    matched = r.get("matched_high_confidence", 0) + r.get("matched_low_confidence", 0)
                    total = r.get("total_instruments", 0)
                    print(f"  {r['ticker']}: {matched}/{total} matched ({r.get('unmatched', 0)} unmatched)")

        if errors:
            print(f"\n{'='*80}")
            print("ERRORS")
            print(f"{'='*80}")
            for r in errors:
                print(f"  {r['ticker']}: {r.get('reason', 'unknown')}")


if __name__ == "__main__":
    run_async(main())
