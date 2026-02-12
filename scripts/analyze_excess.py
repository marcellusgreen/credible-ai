import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv
load_dotenv()

import asyncio
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy import text
from datetime import datetime
from collections import defaultdict

engine = create_async_engine(os.getenv('DATABASE_URL'), echo=False)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)

async def analyze_excess_companies():
    async with AsyncSessionLocal() as session:
        print("=" * 80)
        print("ANALYZING EXCESS DEBT COMPANIES")
        print("=" * 80)

        # First, identify all EXCESS companies
        query = text("""
            WITH company_debt_summary AS (
                SELECT
                    c.id,
                    c.ticker,
                    c.name,
                    cf.total_debt,
                    cf.fiscal_year,
                    cf.fiscal_quarter,
                    COALESCE(SUM(di.outstanding), 0) as total_instruments_outstanding
                FROM companies c
                LEFT JOIN company_financials cf ON c.id = cf.company_id
                LEFT JOIN debt_instruments di ON c.id = di.company_id AND di.is_active = true
                WHERE cf.total_debt IS NOT NULL AND cf.total_debt > 0
                GROUP BY c.id, c.ticker, c.name, cf.total_debt, cf.fiscal_year, cf.fiscal_quarter
            )
            SELECT
                ticker,
                name,
                total_debt / 100.0 as total_debt_dollars,
                total_instruments_outstanding / 100.0 as instruments_outstanding_dollars,
                CASE
                    WHEN total_debt > 0 THEN (total_instruments_outstanding::float / total_debt * 100)
                    ELSE 0
                END as percentage,
                id
            FROM company_debt_summary
            WHERE total_instruments_outstanding > total_debt
            ORDER BY percentage DESC
        """)

        result = await session.execute(query)
        excess_companies = result.fetchall()

        print(f"\nFound {len(excess_companies)} companies with EXCESS debt instruments\n")

        # Separate into SIGNIFICANT (>200%) and regular excess
        significant = [c for c in excess_companies if c.percentage > 200]

        print(f"EXCESS_SIGNIFICANT (>200%): {len(significant)} companies")
        print(f"Regular EXCESS (100-200%): {len(excess_companies) - len(significant)} companies\n")

        # Get company IDs for analysis
        significant_ids = [c.id for c in significant]
        all_excess_ids = [c.id for c in excess_companies]

        # Analysis 1: Source breakdown for SIGNIFICANT companies
        print("=" * 80)
        print("1. SOURCE BREAKDOWN FOR EXCESS_SIGNIFICANT COMPANIES (>200%)")
        print("=" * 80)

        if significant_ids:
            source_query = text("""
                SELECT
                    di.attributes->>'source' as source,
                    COUNT(*) as instrument_count,
                    SUM(di.outstanding) / 100.0 as total_outstanding_dollars
                FROM debt_instruments di
                WHERE di.company_id = ANY(:company_ids)
                AND di.is_active = true
                GROUP BY di.attributes->>'source'
                ORDER BY total_outstanding_dollars DESC
            """)

            result = await session.execute(source_query, {"company_ids": significant_ids})
            sources = result.fetchall()

            for source in sources:
                print(f"  Source: {source.source or 'NULL'}")
                print(f"    Instruments: {source.instrument_count}")
                print(f"    Outstanding: ${source.total_outstanding_dollars:,.2f}")
                print()

        # Analysis 2: Matured bonds still active
        print("=" * 80)
        print("2. INSTRUMENTS WITH PAST MATURITY DATES (Still Active)")
        print("=" * 80)

        matured_query = text("""
            SELECT
                c.ticker,
                c.name,
                di.name as instrument_name,
                di.maturity_date,
                di.outstanding / 100.0 as outstanding_dollars,
                di.attributes->>'source' as source
            FROM debt_instruments di
            JOIN companies c ON di.company_id = c.id
            WHERE di.company_id = ANY(:company_ids)
            AND di.is_active = true
            AND di.maturity_date < CURRENT_DATE
            ORDER BY c.ticker, di.maturity_date
        """)

        result = await session.execute(matured_query, {"company_ids": all_excess_ids})
        matured = result.fetchall()

        print(f"\nFound {len(matured)} matured instruments still marked as active\n")

        if matured:
            print("Sample of matured instruments:")
            for m in matured[:10]:
                print(f"  {m.ticker} - {m.instrument_name}")
                print(f"    Maturity: {m.maturity_date}, Outstanding: ${m.outstanding_dollars:,.2f}")
                print(f"    Source: {m.source}")
                print()

        # Analysis 3: Potential duplicates (same rate + maturity year)
        print("=" * 80)
        print("3. POTENTIAL DUPLICATE INSTRUMENTS (Same Rate + Maturity Year)")
        print("=" * 80)

        duplicate_query = text("""
            WITH instrument_groups AS (
                SELECT
                    company_id,
                    interest_rate,
                    EXTRACT(YEAR FROM maturity_date) as maturity_year,
                    COUNT(*) as instrument_count,
                    SUM(outstanding) / 100.0 as total_outstanding_dollars,
                    ARRAY_AGG(name) as instrument_names,
                    ARRAY_AGG(attributes->>'source') as sources
                FROM debt_instruments
                WHERE company_id = ANY(:company_ids)
                AND is_active = true
                AND interest_rate IS NOT NULL
                AND maturity_date IS NOT NULL
                GROUP BY company_id, interest_rate, EXTRACT(YEAR FROM maturity_date)
                HAVING COUNT(*) > 1
            )
            SELECT
                c.ticker,
                c.name,
                ig.interest_rate,
                ig.maturity_year,
                ig.instrument_count,
                ig.total_outstanding_dollars,
                ig.instrument_names,
                ig.sources
            FROM instrument_groups ig
            JOIN companies c ON ig.company_id = c.id
            ORDER BY ig.instrument_count DESC, c.ticker
        """)

        result = await session.execute(duplicate_query, {"company_ids": all_excess_ids})
        duplicates = result.fetchall()

        total_duplicate_instruments = sum(d.instrument_count for d in duplicates)
        print(f"\nFound {len(duplicates)} groups with potential duplicates")
        print(f"Total instruments in duplicate groups: {total_duplicate_instruments}\n")

        if duplicates:
            print("Top duplicate groups:")
            for d in duplicates[:15]:
                print(f"  {d.ticker} - {d.interest_rate}% maturing {int(d.maturity_year)}")
                print(f"    {d.instrument_count} instruments, Total: ${d.total_outstanding_dollars:,.2f}")
                print(f"    Sources: {set(d.sources)}")
                print()

        # Analysis 4: Detailed view of worst offenders
        print("=" * 80)
        print("4. WORST OFFENDERS - DETAILED INSTRUMENT BREAKDOWN")
        print("=" * 80)

        worst_offenders = ['MA', 'NFLX', 'MSFT', 'ETN']

        for ticker in worst_offenders:
            print(f"\n{'=' * 80}")
            print(f"TICKER: {ticker}")
            print('=' * 80)

            detail_query = text("""
                WITH company_info AS (
                    SELECT
                        c.id,
                        c.ticker,
                        c.name,
                        cf.total_debt / 100.0 as total_debt_dollars,
                        SUM(di.outstanding) / 100.0 as instruments_total_dollars
                    FROM companies c
                    LEFT JOIN company_financials cf ON c.id = cf.company_id
                    LEFT JOIN debt_instruments di ON c.id = di.company_id AND di.is_active = true
                    WHERE c.ticker = :ticker
                    GROUP BY c.id, c.ticker, c.name, cf.total_debt
                )
                SELECT * FROM company_info
            """)

            result = await session.execute(detail_query, {"ticker": ticker})
            company_info = result.fetchone()

            if company_info:
                percentage = (company_info.instruments_total_dollars / company_info.total_debt_dollars * 100) if company_info.total_debt_dollars else 0
                print(f"Company: {company_info.name}")
                print(f"Reported Total Debt: ${company_info.total_debt_dollars:,.2f}")
                print(f"Sum of Instruments: ${company_info.instruments_total_dollars:,.2f}")
                print(f"Percentage: {percentage:.1f}%\n")

                instruments_query = text("""
                    SELECT
                        di.name,
                        di.interest_rate,
                        di.maturity_date,
                        di.outstanding / 100.0 as outstanding_dollars,
                        di.principal / 100.0 as principal_dollars,
                        di.attributes->>'source' as source,
                        di.attributes->>'cusip' as cusip
                    FROM debt_instruments di
                    JOIN companies c ON di.company_id = c.id
                    WHERE c.ticker = :ticker
                    AND di.is_active = true
                    ORDER BY di.maturity_date, di.interest_rate
                """)

                result = await session.execute(instruments_query, {"ticker": ticker})
                instruments = result.fetchall()

                print(f"Active Instruments ({len(instruments)}):")
                for i, inst in enumerate(instruments, 1):
                    print(f"\n  {i}. {inst.name}")
                    print(f"     Rate: {inst.interest_rate}%, Maturity: {inst.maturity_date}")
                    principal_str = f"${inst.principal_dollars:,.2f}" if inst.principal_dollars is not None else "N/A"
                    outstanding_str = f"${inst.outstanding_dollars:,.2f}" if inst.outstanding_dollars is not None else "N/A"
                    print(f"     Outstanding: {outstanding_str}, Principal: {principal_str}")
                    print(f"     Source: {inst.source}, CUSIP: {inst.cusip or 'N/A'}")

        # Summary statistics
        print("\n" + "=" * 80)
        print("SUMMARY STATISTICS")
        print("=" * 80)

        summary_query = text("""
            WITH excess_stats AS (
                SELECT
                    COUNT(DISTINCT di.company_id) as companies_with_instruments,
                    COUNT(*) as total_instruments,
                    AVG(di.outstanding) / 100.0 as avg_outstanding,
                    SUM(di.outstanding) / 100.0 as total_outstanding
                FROM debt_instruments di
                WHERE di.company_id = ANY(:company_ids)
                AND di.is_active = true
            )
            SELECT * FROM excess_stats
        """)

        result = await session.execute(summary_query, {"company_ids": all_excess_ids})
        stats = result.fetchone()

        print(f"\nTotal EXCESS companies analyzed: {len(excess_companies)}")
        print(f"Companies with instruments: {stats.companies_with_instruments}")
        print(f"Total active instruments: {stats.total_instruments}")
        print(f"Average instrument outstanding: ${stats.avg_outstanding:,.2f}")
        print(f"Total outstanding across all EXCESS companies: ${stats.total_outstanding:,.2f}")

async def main():
    try:
        await analyze_excess_companies()
    finally:
        await engine.dispose()

if __name__ == "__main__":
    asyncio.run(main())
