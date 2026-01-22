#!/usr/bin/env python3
"""
Iterative extraction with QA feedback loop.

This is the recommended extraction method for first-time company extractions.
It extracts:
  1. Corporate structure (entities, ownership hierarchy)
  2. Debt instruments (bonds, loans, credit facilities)
  3. Document sections (for full-text search)
  4. TTM financials (4 quarters of financial data for leverage ratios)

Usage:
    python scripts/extract_iterative.py --ticker AAPL --cik 0000320193 --save-db
    python scripts/extract_iterative.py --ticker AAPL --cik 0000320193 --threshold 90 --save-db
    python scripts/extract_iterative.py --ticker AAPL --cik 0000320193 --skip-financials --save-db

Environment variables:
    GEMINI_API_KEY - Required for extraction and QA
    ANTHROPIC_API_KEY - Required for Claude escalation
    SEC_API_KEY - Optional, for faster filing retrieval
    DATABASE_URL - Required for --save-db
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

from app.services.iterative_extraction import IterativeExtractionService, IterativeExtractionResult
from app.services.extraction import SecApiClient, SECEdgarClient


async def download_filings(ticker: str, cik: str, sec_api_key: str = None) -> dict[str, str]:
    """Download all relevant filings."""
    filings = {}

    if sec_api_key:
        print(f"  Downloading filings via SEC-API...")
        sec_client = SecApiClient(sec_api_key)
        # Pass CIK as fallback if ticker search fails
        filings = await sec_client.get_all_relevant_filings(ticker, cik=cik)
        exhibit_21 = sec_client.get_exhibit_21(ticker)
        if exhibit_21:
            filings['exhibit_21'] = exhibit_21
        print(f"    Downloaded {len(filings)} filings")
    else:
        print(f"  Downloading filings via SEC EDGAR...")
        edgar = SECEdgarClient()
        filings = await edgar.get_all_relevant_filings(cik)
        await edgar.close()
        print(f"    Downloaded {len(filings)} filings")

    return filings


async def run_iterative_extraction(
    ticker: str,
    cik: str,
    gemini_api_key: str,
    anthropic_api_key: str,
    sec_api_key: str = None,
    quality_threshold: float = 85.0,
    max_iterations: int = 3,
    save_results: bool = True,
    save_to_db: bool = False,
    database_url: str = None,
    skip_financials: bool = False,
) -> IterativeExtractionResult:
    """
    Run iterative extraction with feedback loop.
    """
    print(f"\n{'='*60}")
    print(f"ITERATIVE EXTRACTION: {ticker} (CIK: {cik})")
    print(f"{'='*60}")
    print(f"  Quality threshold: {quality_threshold}%")
    print(f"  Max iterations: {max_iterations}")

    # Download filings
    filings = await download_filings(ticker, cik, sec_api_key)

    if not filings:
        print("Error: No filings found")
        sys.exit(1)

    # Run iterative extraction
    service = IterativeExtractionService(
        gemini_api_key=gemini_api_key,
        anthropic_api_key=anthropic_api_key,
        sec_api_key=sec_api_key,
        max_iterations=max_iterations,
        quality_threshold=quality_threshold,
    )

    result = await service.extract_with_feedback(
        ticker=ticker,
        cik=cik,
        filings=filings,
    )

    # Print results
    print(f"\n{'='*60}")
    print("EXTRACTION COMPLETE")
    print(f"{'='*60}")
    print(f"Ticker: {result.ticker}")
    print(f"Final QA Score: {result.final_qa_score:.0f}%")
    print(f"Iterations: {len(result.iterations)}")
    print(f"Final Model: {result.final_model}")
    print(f"Total Cost: ${result.total_cost:.4f}")
    print(f"Total Duration: {result.total_duration:.1f}s")

    print(f"\nExtraction Summary:")
    print(f"  Entities: {len(result.extraction.get('entities', []))}")
    print(f"  Debt instruments: {len(result.extraction.get('debt_instruments', []))}")
    print(f"  Uncertainties: {len(result.extraction.get('uncertainties', []))}")

    print(f"\nIteration History:")
    for iter_result in result.iterations:
        action_str = iter_result.action.value.replace("_", " ").title()
        print(f"  [{iter_result.iteration}] {action_str}: "
              f"Score {iter_result.qa_score:.0f}%, Cost ${iter_result.cost:.4f}")
        if iter_result.issues_fixed:
            for fix in iter_result.issues_fixed:
                print(f"       - {fix}")

    print(f"\nQA Report Summary:")
    for check in result.qa_report.checks:
        status_icon = {
            "pass": "[PASS]",
            "fail": "[FAIL]",
            "warn": "[WARN]",
            "skip": "[SKIP]",
        }.get(check.status.value, "[????]")
        print(f"  {status_icon} {check.name}: {check.message}")

    # Save results
    if save_results:
        os.makedirs("results", exist_ok=True)

        # Save extraction
        extraction_path = f"results/{ticker.lower()}_iterative.json"
        with open(extraction_path, 'w') as f:
            json.dump(result.extraction, f, indent=2, default=str)
        print(f"\n  Saved extraction to {extraction_path}")

        # Save full result
        result_path = f"results/{ticker.lower()}_iterative_result.json"
        result_dict = {
            "ticker": result.ticker,
            "final_qa_score": result.final_qa_score,
            "total_cost": result.total_cost,
            "total_duration": result.total_duration,
            "final_model": result.final_model,
            "iterations": [
                {
                    "iteration": ir.iteration,
                    "action": ir.action.value,
                    "qa_score": ir.qa_score,
                    "issues_fixed": ir.issues_fixed,
                    "cost": ir.cost,
                    "duration_seconds": ir.duration_seconds,
                }
                for ir in result.iterations
            ],
            "qa_report": result.qa_report.to_dict(),
        }
        with open(result_path, 'w') as f:
            json.dump(result_dict, f, indent=2)
        print(f"  Saved result to {result_path}")

    # Save to database
    if save_to_db and database_url:
        print(f"\n  Saving to database...")
        from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
        from app.services.extraction import save_extraction_to_db
        from app.services.section_extraction import extract_and_store_sections

        # Convert to ExtractionResult format
        from scripts.extract_tiered import convert_to_extraction_result
        extraction_result = convert_to_extraction_result(result.extraction, ticker)

        engine = create_async_engine(database_url, echo=False)
        async_session = async_sessionmaker(engine, expire_on_commit=False)

        async with async_session() as session:
            company_id = await save_extraction_to_db(session, extraction_result, ticker, cik=cik)
            await session.commit()
            print(f"    [OK] Saved extraction to database")

            # Extract and store document sections for full-text search
            try:
                sections_stored = await extract_and_store_sections(
                    db=session,
                    company_id=company_id,
                    filings_content=filings,
                )
                if sections_stored > 0:
                    print(f"    [OK] Stored {sections_stored} document sections for search")
            except Exception as e:
                print(f"    [WARN] Section extraction failed: {e}")

        await engine.dispose()

    # Extract TTM financials (4 quarters) unless skipped
    # This runs after main extraction is complete and saved
    if save_to_db and database_url and not skip_financials:
        print(f"\n  Extracting TTM financials (4 quarters)...")
        try:
            from app.services.financial_extraction import extract_ttm_financials, save_financials_to_db

            ttm_results = await extract_ttm_financials(
                ticker=ticker,
                cik=cik,
                use_claude=False,  # Use Gemini by default for cost
            )

            if ttm_results:
                # Save each quarter to database
                engine = create_async_engine(database_url, echo=False)
                async_session = async_sessionmaker(engine, expire_on_commit=False)

                async with async_session() as session:
                    for fin_result in ttm_results:
                        await save_financials_to_db(session, ticker, fin_result)
                    await session.commit()

                await engine.dispose()
                print(f"    [OK] Extracted and saved {len(ttm_results)} quarters of financials")
            else:
                print(f"    [WARN] No financials extracted")
        except Exception as e:
            print(f"    [WARN] Financial extraction failed: {e}")

    print(f"\n{'='*60}")

    return result


def main():
    parser = argparse.ArgumentParser(
        description="Iterative extraction with QA feedback loop"
    )
    parser.add_argument("--ticker", required=True, help="Stock ticker (e.g., AAPL)")
    parser.add_argument("--cik", required=True, help="SEC CIK number")
    parser.add_argument("--threshold", type=float, default=85.0,
                       help="QA quality threshold (default: 85)")
    parser.add_argument("--max-iterations", type=int, default=3,
                       help="Maximum fix iterations (default: 3)")
    parser.add_argument("--no-save", action="store_true",
                       help="Don't save results to files")
    parser.add_argument("--save-db", action="store_true",
                       help="Save to database")
    parser.add_argument("--skip-financials", action="store_true",
                       help="Skip TTM financial extraction")

    args = parser.parse_args()

    # Load environment
    load_dotenv()

    gemini_api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    if not gemini_api_key:
        print("Error: GEMINI_API_KEY required")
        sys.exit(1)

    anthropic_api_key = os.getenv("ANTHROPIC_API_KEY")
    if not anthropic_api_key:
        print("Warning: ANTHROPIC_API_KEY not set, Claude escalation disabled")

    sec_api_key = os.getenv("SEC_API_KEY")
    database_url = os.getenv("DATABASE_URL")

    # Run
    asyncio.run(
        run_iterative_extraction(
            ticker=args.ticker,
            cik=args.cik,
            gemini_api_key=gemini_api_key,
            anthropic_api_key=anthropic_api_key,
            sec_api_key=sec_api_key,
            quality_threshold=args.threshold,
            max_iterations=args.max_iterations,
            save_results=not args.no_save,
            save_to_db=args.save_db,
            database_url=database_url,
            skip_financials=args.skip_financials,
        )
    )


if __name__ == "__main__":
    main()
