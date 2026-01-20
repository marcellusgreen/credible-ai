"""
Extract SEC Rule 13-01 Obligor Group Financial Information from SEC filings.

Usage:
    python scripts/extract_obligor_group.py --ticker CHTR
    python scripts/extract_obligor_group.py --ticker CHTR --filing-type 10-K
    python scripts/extract_obligor_group.py --ticker CHTR --save-db
    python scripts/extract_obligor_group.py --batch demo --save-db
"""

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker

load_dotenv()


# Demo companies with guaranteed debt structures
DEMO_COMPANIES = [
    {"ticker": "CHTR", "cik": "1091667"},   # Charter Communications
    {"ticker": "DAL", "cik": "0000027904"},  # Delta Air Lines
    {"ticker": "HCA", "cik": "0000860730"},  # HCA Healthcare
    {"ticker": "CCL", "cik": "0000815097"},  # Carnival
    {"ticker": "AAL", "cik": "0000006201"},  # American Airlines
]


async def get_session():
    """Create database session."""
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise ValueError("DATABASE_URL not set")

    engine = create_async_engine(database_url)
    async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    return async_session(), engine


def format_cents(value: int | None) -> str:
    """Format cents as dollars with appropriate suffix."""
    if value is None:
        return "N/A"

    dollars = value / 100
    if abs(dollars) >= 1_000_000_000:
        return f"${dollars / 1_000_000_000:,.1f}B"
    elif abs(dollars) >= 1_000_000:
        return f"${dollars / 1_000_000:,.1f}M"
    else:
        return f"${dollars:,.0f}"


def format_leakage(pct: float | None) -> str:
    """Format leakage percentage with risk indicator."""
    if pct is None:
        return "N/A"

    if pct >= 50:
        risk = "HIGH RISK"
    elif pct >= 25:
        risk = "MODERATE"
    elif pct >= 10:
        risk = "LOW"
    else:
        risk = "MINIMAL"

    return f"{pct:.1f}% ({risk})"


async def extract_single(
    ticker: str,
    cik: str | None = None,
    filing_type: str = "10-Q",
    save_db: bool = False,
    use_claude: bool = False,
):
    """Extract obligor group data for a single company."""
    from app.services.obligor_group_extraction import (
        extract_obligor_group,
        save_obligor_group_to_db,
        calculate_leakage,
    )

    print(f"\n{'='*60}")
    print(f"Extracting Rule 13-01 Obligor Group Data for {ticker}")
    print(f"Filing type: {filing_type}")
    print(f"{'='*60}")

    # Extract obligor group data
    result = await extract_obligor_group(
        ticker=ticker,
        cik=cik,
        filing_type=filing_type,
        use_claude=use_claude,
    )

    if not result:
        print(f"FAILED: Could not extract data for {ticker}")
        return None

    # Display results
    print(f"\nFiscal Period: Q{result.fiscal_quarter} {result.fiscal_year}")
    print(f"Period End: {result.period_end_date}")

    if result.found_disclosure:
        print(f"\n[OK] Found Rule 13-01 disclosure")
        print(f"  Note: {result.disclosure_note_number or 'Not specified'}")
        print(f"  Debt covered: {result.debt_description or 'Not specified'}")

        print("\n--- Obligor Group (Issuer + Guarantors) ---")
        print(f"  Total Assets:      {format_cents(result.og_total_assets)}")
        print(f"  Total Liabilities: {format_cents(result.og_total_liabilities)}")
        print(f"  Equity:            {format_cents(result.og_stockholders_equity)}")
        print(f"  Revenue:           {format_cents(result.og_revenue)}")
        print(f"  EBITDA:            {format_cents(result.og_ebitda)}")
        print(f"  Net Income:        {format_cents(result.og_net_income)}")

        print("\n--- Consolidated Totals ---")
        print(f"  Total Assets:      {format_cents(result.consolidated_total_assets)}")
        print(f"  Revenue:           {format_cents(result.consolidated_revenue)}")
        print(f"  EBITDA:            {format_cents(result.consolidated_ebitda)}")

        if result.non_guarantor_assets or result.non_guarantor_revenue:
            print("\n--- Non-Guarantor Subsidiaries ---")
            print(f"  Assets:            {format_cents(result.non_guarantor_assets)}")
            print(f"  Revenue:           {format_cents(result.non_guarantor_revenue)}")

        # Calculate and display leakage
        asset_leakage = calculate_leakage(result.og_total_assets, result.consolidated_total_assets)
        revenue_leakage = calculate_leakage(result.og_revenue, result.consolidated_revenue)
        ebitda_leakage = calculate_leakage(result.og_ebitda, result.consolidated_ebitda)

        print("\n--- ASSET LEAKAGE ANALYSIS ---")
        print(f"  Asset Leakage:     {format_leakage(float(asset_leakage) if asset_leakage else None)}")
        print(f"  Revenue Leakage:   {format_leakage(float(revenue_leakage) if revenue_leakage else None)}")
        print(f"  EBITDA Leakage:    {format_leakage(float(ebitda_leakage) if ebitda_leakage else None)}")

        if asset_leakage and float(asset_leakage) >= 25:
            print(f"\n  [!] WARNING: Significant asset leakage detected!")
            print(f"      {float(asset_leakage):.1f}% of consolidated assets are outside the Obligor Group")
            print(f"      Creditors cannot claim these assets in a default scenario")

    else:
        print(f"\n[NOT FOUND] No Rule 13-01 disclosure found")
        print(f"  Reason: {result.reason}")
        print(f"\n  This may indicate:")
        print(f"    - Company has no guaranteed debt")
        print(f"    - Guarantors are immaterial")
        print(f"    - Disclosure is in a different format")

    if result.uncertainties:
        print(f"\n--- Uncertainties ---")
        for u in result.uncertainties:
            print(f"  - {u}")

    # Save to database
    if save_db and result.found_disclosure:
        print("\n--- Saving to database ---")
        session, engine = await get_session()
        try:
            record = await save_obligor_group_to_db(session, ticker, result)
            if record:
                print(f"  Saved obligor group record for {ticker} Q{result.fiscal_quarter} {result.fiscal_year}")
                if record.asset_leakage_pct:
                    print(f"  Asset leakage: {record.asset_leakage_pct}%")
            else:
                print(f"  Failed to save - company not found in database")
        finally:
            await session.close()
            await engine.dispose()

    # Save to JSON file
    output = {
        "ticker": ticker,
        "extracted_at": datetime.now().isoformat(),
        "data": result.model_dump(),
    }

    os.makedirs("results", exist_ok=True)
    filename = f"results/{ticker}_obligor_group.json"
    with open(filename, "w") as f:
        json.dump(output, f, indent=2, default=str)
    print(f"\n  Saved to {filename}")

    return result


async def extract_batch(companies: list[dict], save_db: bool = False, delay: int = 5):
    """Extract obligor group data for multiple companies."""
    results = {}

    for i, company in enumerate(companies):
        ticker = company["ticker"]
        cik = company.get("cik")

        print(f"\n[{i+1}/{len(companies)}] Processing {ticker}...")

        try:
            result = await extract_single(
                ticker=ticker,
                cik=cik,
                save_db=save_db,
            )
            if result:
                if result.found_disclosure:
                    results[ticker] = "FOUND"
                else:
                    results[ticker] = "NO_DISCLOSURE"
            else:
                results[ticker] = "FAILED"
        except Exception as e:
            print(f"ERROR: {e}")
            results[ticker] = f"ERROR: {str(e)[:50]}"

        # Delay between requests
        if i < len(companies) - 1:
            print(f"\nWaiting {delay}s before next request...")
            await asyncio.sleep(delay)

    # Summary
    print("\n" + "="*60)
    print("BATCH EXTRACTION SUMMARY")
    print("="*60)
    for ticker, status in results.items():
        print(f"  {ticker}: {status}")

    return results


async def main():
    parser = argparse.ArgumentParser(
        description="Extract SEC Rule 13-01 Obligor Group Financial Information"
    )
    parser.add_argument("--ticker", type=str, help="Stock ticker (e.g., CHTR)")
    parser.add_argument("--cik", type=str, help="SEC CIK number")
    parser.add_argument(
        "--filing-type",
        type=str,
        default="10-Q",
        choices=["10-Q", "10-K"],
        help="Filing type to extract from",
    )
    parser.add_argument(
        "--save-db",
        action="store_true",
        help="Save results to database",
    )
    parser.add_argument(
        "--use-claude",
        action="store_true",
        help="Use Claude instead of Gemini for extraction",
    )
    parser.add_argument(
        "--batch",
        type=str,
        choices=["demo"],
        help="Run batch extraction for predefined company lists",
    )
    parser.add_argument(
        "--delay",
        type=int,
        default=5,
        help="Delay between requests in batch mode (seconds)",
    )

    args = parser.parse_args()

    if args.batch:
        if args.batch == "demo":
            await extract_batch(DEMO_COMPANIES, save_db=args.save_db, delay=args.delay)
    elif args.ticker:
        await extract_single(
            ticker=args.ticker.upper(),
            cik=args.cik,
            filing_type=args.filing_type,
            save_db=args.save_db,
            use_claude=args.use_claude,
        )
    else:
        parser.print_help()
        print("\nExamples:")
        print("  python scripts/extract_obligor_group.py --ticker CHTR")
        print("  python scripts/extract_obligor_group.py --ticker CHTR --filing-type 10-K")
        print("  python scripts/extract_obligor_group.py --ticker CHTR --save-db")
        print("  python scripts/extract_obligor_group.py --batch demo --save-db")


if __name__ == "__main__":
    asyncio.run(main())
