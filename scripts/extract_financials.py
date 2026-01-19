"""
Extract quarterly financial data from SEC 10-Q/10-K filings.

Usage:
    python scripts/extract_financials.py --ticker AAPL
    python scripts/extract_financials.py --ticker AAPL --filing-type 10-K
    python scripts/extract_financials.py --ticker AAPL --save-db
    python scripts/extract_financials.py --ticker AAPL --ttm --save-db  # Extract 4 quarters
    python scripts/extract_financials.py --batch demo --save-db
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


# Demo companies for testing
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


async def extract_single(
    ticker: str,
    cik: str | None = None,
    filing_type: str = "10-Q",
    save_db: bool = False,
    use_claude: bool = False,
):
    """Extract financials for a single company."""
    from app.services.financial_extraction import extract_financials, save_financials_to_db

    print(f"\n{'='*60}")
    print(f"Extracting financials for {ticker}")
    print(f"Filing type: {filing_type}")
    print(f"{'='*60}")

    # Extract financials
    result = await extract_financials(
        ticker=ticker,
        cik=cik,
        filing_type=filing_type,
        use_claude=use_claude,
    )

    if not result:
        print(f"FAILED: Could not extract financials for {ticker}")
        return None

    # Display results
    print(f"\nFiscal Period: Q{result.fiscal_quarter} {result.fiscal_year}")
    print(f"Period End: {result.period_end_date}")

    print("\n--- Income Statement ---")
    print(f"  Revenue:           {format_cents(result.revenue)}")
    print(f"  Cost of Revenue:   {format_cents(result.cost_of_revenue)}")
    print(f"  Gross Profit:      {format_cents(result.gross_profit)}")
    print(f"  Operating Income:  {format_cents(result.operating_income)}")
    print(f"  Interest Expense:  {format_cents(result.interest_expense)}")
    print(f"  Net Income:        {format_cents(result.net_income)}")
    print(f"  D&A:               {format_cents(result.depreciation_amortization)}")
    print(f"  EBITDA:            {format_cents(result.ebitda)}")

    print("\n--- Balance Sheet ---")
    print(f"  Cash:              {format_cents(result.cash_and_equivalents)}")
    print(f"  Current Assets:    {format_cents(result.total_current_assets)}")
    print(f"  Total Assets:      {format_cents(result.total_assets)}")
    print(f"  Current Liab:      {format_cents(result.total_current_liabilities)}")
    print(f"  Total Debt:        {format_cents(result.total_debt)}")
    print(f"  Total Liab:        {format_cents(result.total_liabilities)}")
    print(f"  Equity:            {format_cents(result.stockholders_equity)}")

    print("\n--- Cash Flow ---")
    print(f"  Operating CF:      {format_cents(result.operating_cash_flow)}")
    print(f"  Investing CF:      {format_cents(result.investing_cash_flow)}")
    print(f"  Financing CF:      {format_cents(result.financing_cash_flow)}")
    print(f"  CapEx:             {format_cents(result.capex)}")

    if result.uncertainties:
        print(f"\n--- Uncertainties ---")
        for u in result.uncertainties:
            print(f"  - {u}")

    # Calculate ratios if we have the data
    if result.ebitda and result.total_debt:
        leverage = result.total_debt / result.ebitda
        print(f"\n--- Calculated Ratios (using quarterly EBITDA) ---")
        print(f"  Leverage (Debt/EBITDA): {leverage:.2f}x")

        if result.cash_and_equivalents:
            net_debt = result.total_debt - result.cash_and_equivalents
            net_leverage = net_debt / result.ebitda
            print(f"  Net Leverage:           {net_leverage:.2f}x")

        if result.interest_expense:
            coverage = result.ebitda / result.interest_expense
            print(f"  Interest Coverage:      {coverage:.2f}x")

    # Save to database
    if save_db:
        print("\n--- Saving to database ---")
        session, engine = await get_session()
        try:
            record = await save_financials_to_db(session, ticker, result)
            if record:
                print(f"  Saved financial record for {ticker} Q{result.fiscal_quarter} {result.fiscal_year}")
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
    filename = f"results/{ticker}_financials.json"
    with open(filename, "w") as f:
        json.dump(output, f, indent=2)
    print(f"\n  Saved to {filename}")

    return result


async def extract_batch(companies: list[dict], save_db: bool = False, delay: int = 5):
    """Extract financials for multiple companies."""
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
            results[ticker] = "SUCCESS" if result else "FAILED"
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


async def extract_ttm(
    ticker: str,
    cik: str | None = None,
    save_db: bool = False,
    use_claude: bool = False,
):
    """Extract trailing twelve months (4 quarters) of financials."""
    from app.services.financial_extraction import extract_ttm_financials, save_financials_to_db

    print(f"\n{'='*60}")
    print(f"Extracting TTM financials for {ticker}")
    print(f"{'='*60}")

    # Extract all quarters
    results = await extract_ttm_financials(
        ticker=ticker,
        cik=cik,
        use_claude=use_claude,
    )

    if not results:
        print(f"FAILED: Could not extract any financials for {ticker}")
        return

    # Display summary
    print(f"\n{'='*60}")
    print(f"TTM SUMMARY FOR {ticker}")
    print(f"{'='*60}")
    print(f"Quarters extracted: {len(results)}")

    for r in results:
        ebitda_str = format_cents(r.ebitda) if r.ebitda else format_cents(r.operating_income) + " (OpInc)"
        da_str = format_cents(r.depreciation_amortization) if r.depreciation_amortization else "N/A"
        print(f"  Q{r.fiscal_quarter} {r.fiscal_year}: Rev={format_cents(r.revenue)}, EBITDA={ebitda_str}, D&A={da_str}")

    # Calculate TTM totals
    if len(results) >= 4:
        ttm_revenue = sum(r.revenue or 0 for r in results[:4])
        ttm_ebitda = sum(r.ebitda or r.operating_income or 0 for r in results[:4])
        ttm_interest = sum(r.interest_expense or 0 for r in results[:4])

        print(f"\n--- TTM Totals (last 4 quarters) ---")
        print(f"  TTM Revenue:          {format_cents(ttm_revenue)}")
        print(f"  TTM EBITDA:           {format_cents(ttm_ebitda)}")
        print(f"  TTM Interest Expense: {format_cents(ttm_interest)}")

        if ttm_ebitda > 0 and ttm_interest > 0:
            coverage = ttm_ebitda / ttm_interest
            print(f"  Interest Coverage:    {coverage:.1f}x")

    # Save to database
    if save_db:
        print(f"\n--- Saving to database ---")
        session, engine = await get_session()
        try:
            for result in results:
                await save_financials_to_db(session, ticker, result)
                print(f"  Saved Q{result.fiscal_quarter} {result.fiscal_year}")
        finally:
            await session.close()
            await engine.dispose()

    # Save to JSON
    output = {
        "ticker": ticker,
        "extracted_at": datetime.now().isoformat(),
        "quarters": [
            {
                "fiscal_year": r.fiscal_year,
                "fiscal_quarter": r.fiscal_quarter,
                "revenue": r.revenue,
                "ebitda": r.ebitda,
                "operating_income": r.operating_income,
                "depreciation_amortization": r.depreciation_amortization,
                "interest_expense": r.interest_expense,
                "net_income": r.net_income,
            }
            for r in results
        ],
    }

    output_dir = "results"
    os.makedirs(output_dir, exist_ok=True)
    output_file = f"{output_dir}/{ticker}_ttm_financials.json"
    with open(output_file, "w") as f:
        json.dump(output, f, indent=2)
    print(f"\n  Saved to {output_file}")


async def main():
    parser = argparse.ArgumentParser(
        description="Extract quarterly financial data from SEC filings"
    )
    parser.add_argument("--ticker", type=str, help="Stock ticker (e.g., AAPL)")
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
    parser.add_argument(
        "--ttm",
        action="store_true",
        help="Extract trailing twelve months (4 quarters) of data",
    )

    args = parser.parse_args()

    if args.batch:
        if args.batch == "demo":
            await extract_batch(DEMO_COMPANIES, save_db=args.save_db, delay=args.delay)
    elif args.ttm and args.ticker:
        await extract_ttm(
            ticker=args.ticker.upper(),
            cik=args.cik,
            save_db=args.save_db,
            use_claude=args.use_claude,
        )
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
        print("  python scripts/extract_financials.py --ticker AAPL")
        print("  python scripts/extract_financials.py --ticker AAPL --filing-type 10-K")
        print("  python scripts/extract_financials.py --ticker AAPL --save-db")
        print("  python scripts/extract_financials.py --batch demo --save-db")


if __name__ == "__main__":
    asyncio.run(main())
