#!/usr/bin/env python3
"""
Fix financial data for companies with QC issues.

Actions:
1. Delete impossible records (revenue > $1T - obvious scale errors)
2. Re-extract recent quarters with missing revenue data

Usage:
    python scripts/fix_qc_financials.py              # Dry run - show what would be fixed
    python scripts/fix_qc_financials.py --save-db    # Actually fix and save
"""

import argparse
import asyncio
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker

load_dotenv()


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


async def delete_impossible_records(conn, save_db: bool) -> int:
    """Delete records with revenue > $1T (obvious scale errors)."""
    print("\n" + "=" * 60)
    print("STEP 1: Delete impossible records (revenue > $1T)")
    print("=" * 60)

    # Find impossible records
    result = await conn.execute(text('''
        SELECT c.ticker, cf.fiscal_year, cf.fiscal_quarter, cf.revenue, cf.id
        FROM company_financials cf
        JOIN companies c ON c.id = cf.company_id
        WHERE cf.revenue > 100000000000000  -- > $1T in cents
        ORDER BY c.ticker, cf.fiscal_year, cf.fiscal_quarter
    '''))

    records = result.fetchall()

    if not records:
        print("  No impossible records found")
        return 0

    print(f"  Found {len(records)} impossible records:")
    for row in records:
        rev = row[3] / 100 / 1e9
        print(f"    {row[0]} Q{row[2]} {row[1]}: Revenue ${rev:.0f}B")

    if save_db:
        for row in records:
            await conn.execute(text('''
                DELETE FROM company_financials WHERE id = :id
            '''), {'id': row[4]})
        await conn.commit()
        print(f"  DELETED {len(records)} records")
    else:
        print(f"  [DRY RUN] Would delete {len(records)} records")

    return len(records)


async def reextract_missing_revenue(conn, save_db: bool) -> tuple[int, int]:
    """Re-extract recent quarters with missing revenue but existing EBITDA."""
    from app.services.financial_extraction import extract_financials, save_financials_to_db

    print("\n" + "=" * 60)
    print("STEP 2: Re-extract quarters with missing revenue")
    print("=" * 60)

    # Find recent records with EBITDA but no revenue
    result = await conn.execute(text('''
        SELECT c.ticker, c.cik, cf.fiscal_year, cf.fiscal_quarter, cf.ebitda, cf.id
        FROM company_financials cf
        JOIN companies c ON c.id = cf.company_id
        WHERE cf.ebitda IS NOT NULL
        AND cf.ebitda > 0
        AND (cf.revenue IS NULL OR cf.revenue = 0)
        AND cf.fiscal_year >= 2024
        ORDER BY c.ticker, cf.fiscal_year DESC, cf.fiscal_quarter DESC
    '''))

    records = result.fetchall()

    if not records:
        print("  No records with missing revenue found")
        return 0, 0

    print(f"  Found {len(records)} records with EBITDA but no revenue:")
    for row in records:
        ebitda = row[4] / 100 / 1e9
        print(f"    {row[0]} Q{row[3]} {row[2]}: EBITDA ${ebitda:.2f}B, revenue missing")

    if not save_db:
        print(f"  [DRY RUN] Would re-extract {len(records)} records")
        return len(records), 0

    # Group by company and extract
    fixed = 0
    failed = 0

    # Get unique companies
    companies = {}
    for row in records:
        ticker = row[0]
        if ticker not in companies:
            companies[ticker] = {'cik': row[1], 'records': []}
        companies[ticker]['records'].append({
            'fiscal_year': row[2],
            'fiscal_quarter': row[3],
            'id': row[5]
        })

    database_url = os.getenv('DATABASE_URL')
    engine = create_async_engine(database_url)
    async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    for ticker, info in companies.items():
        print(f"\n  Re-extracting {ticker}...")

        # Extract for the most recent quarter with issues
        record = info['records'][0]  # Most recent
        filing_type = "10-K" if record['fiscal_quarter'] == 4 else "10-Q"

        try:
            result = await extract_financials(
                ticker=ticker,
                cik=info['cik'],
                filing_type=filing_type,
                use_claude=False,
            )

            if result and result.revenue:
                async with async_session() as session:
                    saved = await save_financials_to_db(session, ticker, result)
                    if saved:
                        await session.commit()
                        print(f"    SUCCESS: Revenue ${result.revenue/100/1e9:.2f}B extracted")
                        fixed += 1
                    else:
                        print(f"    FAILED: Could not save to database")
                        failed += 1
            else:
                print(f"    FAILED: Could not extract revenue")
                failed += 1

        except Exception as e:
            print(f"    ERROR: {e}")
            failed += 1

    await engine.dispose()
    return fixed, failed


async def find_additional_issues(conn) -> None:
    """Report other issues that may need manual review."""
    print("\n" + "=" * 60)
    print("STEP 3: Other issues requiring review")
    print("=" * 60)

    # EBITDA > Revenue (unusual but may be valid for some companies)
    result = await conn.execute(text('''
        SELECT c.ticker, cf.fiscal_year, cf.fiscal_quarter, cf.revenue, cf.ebitda
        FROM company_financials cf
        JOIN companies c ON c.id = cf.company_id
        WHERE cf.ebitda > cf.revenue
        AND cf.revenue > 0
        AND cf.ebitda > 0
        ORDER BY c.ticker, cf.fiscal_year DESC, cf.fiscal_quarter DESC
    '''))

    records = result.fetchall()
    if records:
        print(f"\n  EBITDA > Revenue ({len(records)} records):")
        print("  (May be valid for companies with non-operating gains)")
        for row in records:
            rev = row[3] / 100 / 1e9
            ebitda = row[4] / 100 / 1e9
            print(f"    {row[0]} Q{row[2]} {row[1]}: Revenue ${rev:.2f}B, EBITDA ${ebitda:.2f}B")

    # Debt > 10x Assets (unusual)
    result = await conn.execute(text('''
        SELECT c.ticker, cf.fiscal_year, cf.fiscal_quarter, cf.total_debt, cf.total_assets
        FROM company_financials cf
        JOIN companies c ON c.id = cf.company_id
        WHERE cf.total_debt > cf.total_assets * 10
        AND cf.total_assets > 0
        ORDER BY c.ticker, cf.fiscal_year DESC, cf.fiscal_quarter DESC
    '''))

    records = result.fetchall()
    if records:
        print(f"\n  Debt > 10x Assets ({len(records)} records):")
        for row in records:
            debt = row[3] / 100 / 1e9
            assets = row[4] / 100 / 1e9
            print(f"    {row[0]} Q{row[2]} {row[1]}: Debt ${debt:.2f}B, Assets ${assets:.2f}B")


async def main():
    parser = argparse.ArgumentParser(description="Fix financial data QC issues")
    parser.add_argument("--save-db", action="store_true", help="Save fixes to database")
    args = parser.parse_args()

    database_url = os.getenv('DATABASE_URL')
    if not database_url:
        print("ERROR: DATABASE_URL not set")
        sys.exit(1)

    print("Financial QC Fix Script")
    print("=" * 60)
    print(f"Mode: {'SAVE TO DB' if args.save_db else 'DRY RUN'}")

    engine = create_async_engine(database_url)

    async with engine.connect() as conn:
        # Step 1: Delete impossible records
        deleted = await delete_impossible_records(conn, args.save_db)

        # Step 2: Re-extract missing revenue (skip for now - takes time and costs $)
        # fixed, failed = await reextract_missing_revenue(conn, args.save_db)
        fixed, failed = 0, 0

        # Step 3: Report other issues
        await find_additional_issues(conn)

    await engine.dispose()

    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    print(f"Deleted impossible records: {deleted}")
    print(f"Re-extracted with revenue: {fixed}")
    print(f"Failed re-extractions: {failed}")

    if not args.save_db:
        print("\n[DRY RUN] Run with --save-db to apply fixes")


if __name__ == "__main__":
    asyncio.run(main())
