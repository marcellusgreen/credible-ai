#!/usr/bin/env python3
"""
Refresh data for companies with new SEC filings.

Checks EDGAR for new 10-K, 10-Q, and 8-K filings, then runs the
appropriate extraction steps to update the database.

Usage:
    python scripts/refresh_filings.py --all                # Check all companies
    python scripts/refresh_filings.py --ticker AAPL        # Check specific company
    python scripts/refresh_filings.py --all --dry-run      # Show what would be updated
    python scripts/refresh_filings.py --ticker AAPL --force  # Force refresh even if up-to-date
"""

import argparse

from script_utils import (
    get_db_session,
    print_header,
    print_summary,
    run_async,
)

from app.services.filing_monitor import FilingMonitor
from app.services.filing_refresh import FilingRefreshService


async def main():
    parser = argparse.ArgumentParser(
        description="Check for new SEC filings and refresh data"
    )
    parser.add_argument("--ticker", help="Single ticker to check")
    parser.add_argument("--all", action="store_true", help="Check all companies")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be updated without processing",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Force refresh even if filing already processed",
    )
    args = parser.parse_args()

    if not args.ticker and not args.all:
        print("Specify --ticker or --all")
        return

    print_header("SEC FILING REFRESH")

    # Step 1: Check for new filings
    print("Scanning EDGAR for new filings...")
    monitor = FilingMonitor()

    try:
        async with get_db_session() as db:
            new_filings = await monitor.check_all_companies(
                db, ticker_filter=args.ticker
            )
    finally:
        await monitor.close()

    if not new_filings:
        print("\nNo new filings detected.")
        if args.force and args.ticker:
            print(
                f"\n--force specified but no filings found for {args.ticker}. "
                "Ensure the company has a valid CIK."
            )
        return

    # Deduplicate: keep newest filing per (company_id, form_type)
    seen = {}
    for filing in new_filings:
        key = (filing.company_id, filing.form_type)
        if key not in seen or filing.filing_date > seen[key].filing_date:
            seen[key] = filing
    deduped = list(seen.values())

    # Print summary of detected filings
    print(f"\nFound {len(deduped)} new filing(s):")
    print("-" * 70)
    for filing in sorted(deduped, key=lambda f: (f.ticker, f.form_type)):
        print(
            f"  {filing.ticker:6} | {filing.form_type:5} | "
            f"{filing.filing_date} | {filing.accession_number}"
        )
    print()

    if args.dry_run:
        print("(DRY RUN - no changes will be made)")
        return

    # Step 2: Process each filing
    print("Processing filings...")
    print("=" * 70)

    service = FilingRefreshService()
    stats = {
        "processed": 0,
        "succeeded": 0,
        "failed": 0,
        "total_steps_run": 0,
        "total_steps_failed": 0,
    }

    for filing in deduped:
        print(f"\n  {filing.ticker} ({filing.form_type} {filing.filing_date})")
        print(f"  {'~' * 40}")

        try:
            result = await service.refresh_for_filing(filing)
            stats["processed"] += 1
            stats["total_steps_run"] += len(result.steps_run)
            stats["total_steps_failed"] += len(result.steps_failed)

            if result.success:
                stats["succeeded"] += 1
                print(
                    f"    OK: {len(result.steps_run)} steps in "
                    f"{result.duration_seconds:.1f}s"
                )
            else:
                stats["failed"] += 1
                print(
                    f"    PARTIAL: {len(result.steps_run)} OK, "
                    f"{len(result.steps_failed)} failed "
                    f"({', '.join(result.steps_failed)})"
                )
        except Exception as e:
            stats["failed"] += 1
            print(f"    ERROR: {e}")

    print_summary(stats)


if __name__ == "__main__":
    run_async(main())
