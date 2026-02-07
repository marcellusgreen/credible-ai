#!/usr/bin/env python3
"""
Analyze bond identifier coverage for pricing expansion.

Phase 1: ISINs we already have (ready to price)
Phase 2: CUSIPs without ISINs (can derive ISIN)
Phase 3: No identifiers (need SEC discovery)
"""

from sqlalchemy import text

from script_utils import get_db_session, print_header, run_async


async def analyze_coverage():
    async with get_db_session() as session:
        # Phase 1: ISINs we have
        result = await session.execute(
            text("SELECT COUNT(*) FROM debt_instruments WHERE is_active = true AND isin IS NOT NULL AND isin <> ''")
        )
        with_isin = result.scalar()

        # Phase 2: CUSIPs without ISINs (can derive ISIN)
        result = await session.execute(
            text("""
                SELECT COUNT(*) FROM debt_instruments
                WHERE is_active = true
                AND cusip IS NOT NULL AND cusip <> '' AND LENGTH(cusip) = 9
                AND (isin IS NULL OR isin = '')
            """)
        )
        cusip_no_isin = result.scalar()

        # No identifiers at all
        result = await session.execute(
            text("""
                SELECT COUNT(*) FROM debt_instruments
                WHERE is_active = true
                AND (cusip IS NULL OR cusip = '')
                AND (isin IS NULL OR isin = '')
            """)
        )
        no_identifiers = result.scalar()

        # Sample CUSIPs we could convert
        result = await session.execute(
            text("""
                SELECT c.ticker, di.cusip, di.name, di.interest_rate, di.maturity_date
                FROM debt_instruments di
                JOIN entities e ON di.issuer_id = e.id
                JOIN companies c ON e.company_id = c.id
                WHERE di.is_active = true
                AND di.cusip IS NOT NULL AND LENGTH(di.cusip) = 9
                AND (di.isin IS NULL OR di.isin = '')
                LIMIT 15
            """)
        )
        sample_cusips = result.fetchall()

        # Top companies needing ISIN enrichment (via CUSIP conversion)
        result = await session.execute(
            text("""
                SELECT c.ticker,
                       COUNT(*) FILTER (WHERE di.isin IS NOT NULL AND di.isin <> '') as has_isin,
                       COUNT(*) FILTER (WHERE (di.isin IS NULL OR di.isin = '')
                                        AND di.cusip IS NOT NULL AND LENGTH(di.cusip) = 9) as has_cusip_only,
                       COUNT(*) FILTER (WHERE (di.isin IS NULL OR di.isin = '')
                                        AND (di.cusip IS NULL OR di.cusip = '' OR LENGTH(di.cusip) <> 9)) as no_id
                FROM debt_instruments di
                JOIN entities e ON di.issuer_id = e.id
                JOIN companies c ON e.company_id = c.id
                WHERE di.is_active = true
                GROUP BY c.ticker
                ORDER BY COUNT(*) FILTER (WHERE (di.isin IS NULL OR di.isin = '')
                                          AND di.cusip IS NOT NULL AND LENGTH(di.cusip) = 9) DESC
                LIMIT 25
            """)
        )
        companies_by_cusip = result.fetchall()

        # Companies with NO identifiers (need SEC discovery)
        result = await session.execute(
            text("""
                SELECT c.ticker, COUNT(*) as no_id_count
                FROM debt_instruments di
                JOIN entities e ON di.issuer_id = e.id
                JOIN companies c ON e.company_id = c.id
                WHERE di.is_active = true
                AND (di.cusip IS NULL OR di.cusip = '' OR LENGTH(di.cusip) <> 9)
                AND (di.isin IS NULL OR di.isin = '')
                GROUP BY c.ticker
                ORDER BY no_id_count DESC
                LIMIT 20
            """)
        )
        companies_no_id = result.fetchall()

        print_header("BOND IDENTIFIER COVERAGE ANALYSIS")
        print()
        print(f"Phase 1 - With ISIN (ready to price):      {with_isin:,}")
        print(f"Phase 2 - CUSIP only (can derive ISIN):    {cusip_no_isin:,}")
        print(f"Phase 3 - No identifiers (need discovery): {no_identifiers:,}")
        print(f"                                           -------")
        print(f"Total active instruments:                  {with_isin + cusip_no_isin + no_identifiers:,}")
        print()

        print("=" * 70)
        print("PHASE 2: SAMPLE CUSIPs TO CONVERT")
        print("=" * 70)
        for ticker, cusip, name, rate, maturity in sample_cusips:
            name_short = (name[:35] + "...") if name and len(name) > 35 else (name or "N/A")
            rate_str = f"{rate/100:.3f}%" if rate else "N/A"
            mat_str = str(maturity) if maturity else "N/A"
            print(f"  {ticker:<6} {cusip} -> US{cusip}X  {rate_str:<8} {mat_str}")

        print()
        print("=" * 70)
        print("COMPANIES BY CUSIP CONVERSION POTENTIAL")
        print("=" * 70)
        print(f"  {'Ticker':<8} {'Has ISIN':<10} {'CUSIP Only':<12} {'No ID':<8}")
        print(f"  {'-'*8} {'-'*10} {'-'*12} {'-'*8}")
        for ticker, has_isin, has_cusip, no_id in companies_by_cusip[:20]:
            print(f"  {ticker:<8} {has_isin:<10} {has_cusip:<12} {no_id:<8}")

        print()
        print("=" * 70)
        print("PHASE 3: COMPANIES NEEDING SEC DISCOVERY")
        print("=" * 70)
        print(f"  {'Ticker':<8} {'Bonds w/o ID':<15}")
        print(f"  {'-'*8} {'-'*15}")
        for ticker, cnt in companies_no_id:
            print(f"  {ticker:<8} {cnt:<15}")



if __name__ == "__main__":
    run_async(analyze_coverage())
