#!/usr/bin/env python3
"""
QA script for verifying extraction accuracy.

Usage:
    # Run extraction + QA in one step
    python scripts/qa_extraction.py --ticker CRWV --cik 0001769628

    # Run QA on saved extraction JSON
    python scripts/qa_extraction.py --ticker CRWV --cik 0001769628 --extraction-file results/crwv.json

    # Skip extraction, only run QA (re-downloads filings)
    python scripts/qa_extraction.py --ticker CRWV --cik 0001769628 --qa-only

Environment variables:
    GEMINI_API_KEY - Required for QA checks
    ANTHROPIC_API_KEY - Required for extraction (if not --qa-only)
    SEC_API_KEY - Optional, for faster filing retrieval
"""

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime

from dotenv import load_dotenv

# Add parent dir to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.qa_agent import QAAgent, QAReport
from app.services.tiered_extraction import TieredExtractionService
from app.services.extraction import SecApiClient, SECEdgarClient


async def run_extraction_with_qa(
    ticker: str,
    cik: str,
    gemini_api_key: str,
    anthropic_api_key: str = None,
    deepseek_api_key: str = None,
    sec_api_key: str = None,
    extraction_file: str = None,
    qa_only: bool = False,
    save_results: bool = True,
) -> tuple[dict, QAReport]:
    """
    Run extraction and QA verification.

    Args:
        ticker: Stock ticker
        cik: SEC CIK number
        gemini_api_key: Required for QA
        anthropic_api_key: Required for extraction
        deepseek_api_key: Optional Tier 1
        sec_api_key: Optional for fast filing retrieval
        extraction_file: Load extraction from file instead of running
        qa_only: Skip extraction, only run QA
        save_results: Save results to files

    Returns:
        (extraction_result, qa_report)
    """
    print(f"\n{'='*60}")
    print(f"EXTRACTION + QA: {ticker} (CIK: {cik})")
    print(f"{'='*60}")

    extraction = None
    filings = {}

    # Step 1: Get or load extraction
    if extraction_file and os.path.exists(extraction_file):
        print(f"\n  Loading extraction from {extraction_file}...")
        with open(extraction_file, 'r') as f:
            extraction = json.load(f)
        print(f"    Loaded: {len(extraction.get('entities', []))} entities, "
              f"{len(extraction.get('debt_instruments', []))} debt instruments")

    elif not qa_only:
        print(f"\n  Running tiered extraction...")

        if not anthropic_api_key:
            print("Error: ANTHROPIC_API_KEY required for extraction")
            sys.exit(1)

        service = TieredExtractionService(
            anthropic_api_key=anthropic_api_key,
            gemini_api_key=gemini_api_key,
            deepseek_api_key=deepseek_api_key,
            sec_api_key=sec_api_key,
            tier1_model="gemini",
        )

        try:
            extraction, metrics = await service.extract_company(
                ticker=ticker,
                cik=cik,
            )
            print(f"    Extracted: {len(extraction.get('entities', []))} entities, "
                  f"{len(extraction.get('debt_instruments', []))} debt instruments")
            print(f"    Cost: ${metrics.total_cost:.4f}")
        finally:
            await service.close()

    # Step 2: Download filings for QA (always needed)
    print(f"\n  Downloading filings for QA verification...")

    if sec_api_key:
        sec_client = SecApiClient(sec_api_key)
        filings = await sec_client.get_all_relevant_filings(ticker)
        exhibit_21 = sec_client.get_exhibit_21(ticker)
        if exhibit_21:
            filings['exhibit_21'] = exhibit_21
        print(f"    Downloaded {len(filings)} filings via SEC-API")
    else:
        edgar = SECEdgarClient()
        filings = await edgar.get_all_relevant_filings(cik)
        await edgar.close()
        print(f"    Downloaded {len(filings)} filings via SEC EDGAR")

    if not filings:
        print("Error: No filings found for QA verification")
        sys.exit(1)

    # If qa_only and no extraction loaded, we need to run extraction first
    if extraction is None:
        if qa_only:
            print("Error: --qa-only requires --extraction-file with saved extraction")
            sys.exit(1)

    # Step 3: Run QA
    print(f"\n  Running QA verification...")
    qa_agent = QAAgent(gemini_api_key)
    qa_report = await qa_agent.run_qa(extraction, filings)

    # Step 4: Print results
    print(f"\n{'='*60}")
    print("QA REPORT")
    print(f"{'='*60}")
    print(f"Ticker: {qa_report.ticker}")
    print(f"Overall Score: {qa_report.overall_score:.0f}%")
    print(f"Overall Status: {qa_report.overall_status.upper()}")
    print(f"QA Cost: ${qa_agent.total_cost:.4f}")

    print(f"\nChecks:")
    for check in qa_report.checks:
        status_icon = {
            "pass": "[PASS]",
            "fail": "[FAIL]",
            "warn": "[WARN]",
            "skip": "[SKIP]",
        }.get(check.status.value, "[????]")
        print(f"  {status_icon} {check.name}: {check.message}")
        if check.details and check.status.value in ("fail", "warn"):
            for key, value in check.details.items():
                if value:
                    print(f"         {key}: {value}")

    if qa_report.recommendations:
        print(f"\nRecommendations:")
        for rec in qa_report.recommendations:
            print(f"  - {rec}")

    print(f"\nSummary: {qa_report.summary}")

    # Step 5: Save results
    if save_results:
        os.makedirs("results", exist_ok=True)

        # Save extraction
        extraction_path = f"results/{ticker.lower()}_extraction.json"
        with open(extraction_path, 'w') as f:
            json.dump(extraction, f, indent=2, default=str)
        print(f"\n  Saved extraction to {extraction_path}")

        # Save QA report
        qa_path = f"results/{ticker.lower()}_qa_report.json"
        with open(qa_path, 'w') as f:
            json.dump(qa_report.to_dict(), f, indent=2)
        print(f"  Saved QA report to {qa_path}")

    print(f"\n{'='*60}")
    print("COMPLETE")
    print(f"{'='*60}")

    return extraction, qa_report


def main():
    parser = argparse.ArgumentParser(
        description="Run extraction and QA verification"
    )
    parser.add_argument("--ticker", required=True, help="Stock ticker (e.g., CRWV)")
    parser.add_argument("--cik", required=True, help="SEC CIK number (e.g., 0001769628)")
    parser.add_argument("--extraction-file", help="Load extraction from JSON file")
    parser.add_argument("--qa-only", action="store_true",
                       help="Skip extraction, only run QA (requires --extraction-file)")
    parser.add_argument("--no-save", action="store_true",
                       help="Don't save results to files")

    args = parser.parse_args()

    # Load environment
    load_dotenv()

    gemini_api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    if not gemini_api_key:
        print("Error: GEMINI_API_KEY required for QA verification")
        sys.exit(1)

    anthropic_api_key = os.getenv("ANTHROPIC_API_KEY")
    deepseek_api_key = os.getenv("DEEPSEEK_API_KEY")
    sec_api_key = os.getenv("SEC_API_KEY")

    # Run
    asyncio.run(
        run_extraction_with_qa(
            ticker=args.ticker,
            cik=args.cik,
            gemini_api_key=gemini_api_key,
            anthropic_api_key=anthropic_api_key,
            deepseek_api_key=deepseek_api_key,
            sec_api_key=sec_api_key,
            extraction_file=args.extraction_file,
            qa_only=args.qa_only,
            save_results=not args.no_save,
        )
    )


if __name__ == "__main__":
    main()
