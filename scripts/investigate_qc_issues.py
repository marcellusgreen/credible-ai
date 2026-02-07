#!/usr/bin/env python3
"""
Investigate QC issues in detail to understand root causes.
"""

from sqlalchemy import text

from script_utils import get_db_session, print_header, run_async


async def investigate_all_issues():
    """Run detailed investigation of each QC issue."""
    print_header("QC ISSUE INVESTIGATION")

    async with get_db_session() as session:

        # Issue 1: debt_mismatch (75) - Sum differs >50% from financials
        print("\n" + "=" * 70)
        print("ISSUE 1: DEBT_MISMATCH (75 companies)")
        print("Sum of debt instruments differs >50% from reported financials")
        print("=" * 70)

        result = await session.execute(text("""
            WITH debt_sums AS (
                SELECT
                    c.id AS company_id,
                    c.ticker,
                    c.name,
                    SUM(d.principal) / 100.0 AS instrument_total_millions,
                    f.total_debt / 100.0 / 1000000.0 AS financial_total_millions
                FROM companies c
                LEFT JOIN entities e ON e.company_id = c.id
                LEFT JOIN debt_instruments d ON d.issuer_id = e.id AND d.is_active = true
                LEFT JOIN company_financials f ON f.company_id = c.id
                WHERE f.total_debt IS NOT NULL AND f.total_debt > 0
                GROUP BY c.id, c.ticker, c.name, f.total_debt
            )
            SELECT
                ticker,
                name,
                COALESCE(instrument_total_millions, 0) AS instrument_total,
                financial_total_millions AS reported_total,
                CASE
                    WHEN financial_total_millions > 0 THEN
                        ROUND(ABS(COALESCE(instrument_total_millions, 0) - financial_total_millions) / financial_total_millions * 100, 1)
                    ELSE 0
                END AS diff_pct
            FROM debt_sums
            WHERE CASE
                    WHEN financial_total_millions > 0 THEN
                        ABS(COALESCE(instrument_total_millions, 0) - financial_total_millions) / financial_total_millions > 0.5
                    ELSE true
                END
            ORDER BY diff_pct DESC
            LIMIT 15
        """))
        rows = result.fetchall()

        print(f"\nTop 15 companies with debt mismatch:")
        print(f"{'Ticker':<8} {'Instrument Total ($M)':<22} {'Reported Total ($M)':<22} {'Diff %':<10}")
        print("-" * 70)
        for row in rows:
            print(f"{row.ticker:<8} {row.instrument_total:>20,.0f} {row.reported_total:>20,.0f} {row.diff_pct:>8.1f}%")

        print("\nROOT CAUSE ANALYSIS:")
        print("- Many companies have $0 instrument totals - extraction may have missed debt")
        print("- Some have partial extraction - only some instruments captured")
        print("- Consider: Is our extraction finding all debt instruments from 10-K?")

        # Issue 2: guarantor_flag_missing (16)
        print("\n" + "=" * 70)
        print("ISSUE 2: GUARANTOR_FLAG_MISSING (16 entities)")
        print("Entities that ARE guarantors but is_guarantor flag is false")
        print("=" * 70)

        result = await session.execute(text("""
            SELECT DISTINCT
                c.ticker,
                e.name AS entity_name,
                e.is_guarantor,
                COUNT(g.id) AS guarantee_count
            FROM entities e
            JOIN companies c ON c.id = e.company_id
            JOIN guarantees g ON g.guarantor_id = e.id
            WHERE e.is_guarantor = false OR e.is_guarantor IS NULL
            GROUP BY c.ticker, e.name, e.is_guarantor
            ORDER BY guarantee_count DESC
            LIMIT 20
        """))
        rows = result.fetchall()

        print(f"\nEntities with guarantees but is_guarantor=false:")
        print(f"{'Ticker':<8} {'Entity Name':<45} {'Flag':<8} {'Guarantees':<10}")
        print("-" * 75)
        for row in rows:
            name = row.entity_name[:43] if len(row.entity_name) > 43 else row.entity_name
            flag = str(row.is_guarantor) if row.is_guarantor is not None else "NULL"
            print(f"{row.ticker:<8} {name:<45} {flag:<8} {row.guarantee_count:<10}")

        print("\nFIX: Update is_guarantor flag for these entities")

        # Issue 3: duplicate_instruments (18)
        print("\n" + "=" * 70)
        print("ISSUE 3: DUPLICATE_INSTRUMENTS (18)")
        print("Same issuer + name + maturity = potential duplicates")
        print("=" * 70)

        result = await session.execute(text("""
            SELECT
                c.ticker,
                e.name AS issuer_name,
                d.name AS instrument_name,
                d.maturity_date,
                COUNT(*) AS dup_count,
                array_agg(d.id) AS instrument_ids
            FROM debt_instruments d
            JOIN entities e ON e.id = d.issuer_id
            JOIN companies c ON c.id = e.company_id
            WHERE d.is_active = true
            GROUP BY c.ticker, e.name, d.name, d.maturity_date
            HAVING COUNT(*) > 1
            ORDER BY dup_count DESC
            LIMIT 15
        """))
        rows = result.fetchall()

        print(f"\nPotential duplicate instruments:")
        print(f"{'Ticker':<8} {'Instrument':<40} {'Maturity':<12} {'Count':<6}")
        print("-" * 70)
        for row in rows:
            name = row.instrument_name[:38] if row.instrument_name and len(row.instrument_name) > 38 else (row.instrument_name or "N/A")
            maturity = str(row.maturity_date)[:10] if row.maturity_date else "N/A"
            print(f"{row.ticker:<8} {name:<40} {maturity:<12} {row.dup_count:<6}")

        print("\nROOT CAUSE: May be legitimate (multiple tranches) or extraction duplicates")

        # Issue 4: floating_no_benchmark (197)
        print("\n" + "=" * 70)
        print("ISSUE 4: FLOATING_NO_BENCHMARK (197)")
        print("Floating rate debt without SOFR/LIBOR benchmark specified")
        print("=" * 70)

        result = await session.execute(text("""
            SELECT
                c.ticker,
                d.name AS instrument_name,
                d.rate_type,
                d.interest_rate,
                d.benchmark
            FROM debt_instruments d
            JOIN entities e ON e.id = d.issuer_id
            JOIN companies c ON c.id = e.company_id
            WHERE d.is_active = true
            AND d.rate_type = 'floating'
            AND (d.benchmark IS NULL OR d.benchmark = '')
            ORDER BY c.ticker
            LIMIT 15
        """))
        rows = result.fetchall()

        print(f"\nSample floating rate instruments without benchmark:")
        print(f"{'Ticker':<8} {'Instrument':<50} {'Rate':<10}")
        print("-" * 70)
        for row in rows:
            name = row.instrument_name[:48] if row.instrument_name and len(row.instrument_name) > 48 else (row.instrument_name or "N/A")
            rate = f"{row.interest_rate}%" if row.interest_rate else "N/A"
            print(f"{row.ticker:<8} {name:<50} {rate:<10}")

        print("\nACTION: Low priority - extraction could be enhanced to capture benchmark")

        # Issue 5: fixed_no_rate (103)
        print("\n" + "=" * 70)
        print("ISSUE 5: FIXED_NO_RATE (103)")
        print("Fixed rate debt without interest rate specified")
        print("=" * 70)

        result = await session.execute(text("""
            SELECT
                c.ticker,
                d.name AS instrument_name,
                d.rate_type,
                d.interest_rate
            FROM debt_instruments d
            JOIN entities e ON e.id = d.issuer_id
            JOIN companies c ON c.id = e.company_id
            WHERE d.is_active = true
            AND d.rate_type = 'fixed'
            AND d.interest_rate IS NULL
            ORDER BY c.ticker
            LIMIT 15
        """))
        rows = result.fetchall()

        print(f"\nSample fixed rate instruments without rate:")
        print(f"{'Ticker':<8} {'Instrument':<60}")
        print("-" * 70)
        for row in rows:
            name = row.instrument_name[:58] if row.instrument_name and len(row.instrument_name) > 58 else (row.instrument_name or "N/A")
            print(f"{row.ticker:<8} {name:<60}")

        print("\nROOT CAUSE: Often the rate IS in the name (e.g., '5.25% Notes')")
        print("ACTION: Could parse rate from instrument name if pattern matches")

        # Issue 6: missing_maturity (204)
        print("\n" + "=" * 70)
        print("ISSUE 6: MISSING_MATURITY (204)")
        print("Active debt instruments without maturity date")
        print("=" * 70)

        result = await session.execute(text("""
            SELECT
                c.ticker,
                d.name AS instrument_name,
                d.instrument_type
            FROM debt_instruments d
            JOIN entities e ON e.id = d.issuer_id
            JOIN companies c ON c.id = e.company_id
            WHERE d.is_active = true
            AND d.maturity_date IS NULL
            ORDER BY c.ticker
            LIMIT 15
        """))
        rows = result.fetchall()

        print(f"\nSample instruments without maturity:")
        print(f"{'Ticker':<8} {'Type':<15} {'Instrument':<50}")
        print("-" * 75)
        for row in rows:
            name = row.instrument_name[:48] if row.instrument_name and len(row.instrument_name) > 48 else (row.instrument_name or "N/A")
            dtype = row.instrument_type[:13] if row.instrument_type and len(row.instrument_type) > 13 else (row.instrument_type or "N/A")
            print(f"{row.ticker:<8} {dtype:<15} {name:<50}")

        print("\nROOT CAUSE: Revolvers/credit facilities often don't have fixed maturity")
        print("ACTION: Could parse year from name (e.g., 'Notes due 2027')")

        # Info issues summary
        print("\n" + "=" * 70)
        print("INFO ISSUES (Lower Priority)")
        print("=" * 70)

        # Secured without collateral - check for secured in seniority or security_type
        result = await session.execute(text("""
            SELECT COUNT(*) FROM debt_instruments d
            WHERE d.is_active = true
            AND (d.seniority = 'senior_secured' OR d.security_type IN ('first_lien', 'second_lien'))
            AND NOT EXISTS (SELECT 1 FROM collateral c WHERE c.debt_instrument_id = d.id)
        """))
        secured_no_collateral = result.scalar()

        # Missing amounts
        result = await session.execute(text("""
            SELECT COUNT(*) FROM debt_instruments d
            WHERE d.is_active = true AND d.principal IS NULL
        """))
        missing_amounts = result.scalar()

        # Incomplete hierarchy
        result = await session.execute(text("""
            SELECT COUNT(DISTINCT c.id) FROM companies c
            JOIN entities e ON e.company_id = c.id
            WHERE e.parent_id IS NULL AND e.entity_type != 'parent'
            AND NOT EXISTS (SELECT 1 FROM entities e2 WHERE e2.company_id = c.id AND e2.entity_type = 'parent')
        """))
        incomplete_hier = result.scalar()

        print(f"\n- secured_without_collateral: {secured_no_collateral}")
        print("  (Secured debt without collateral assignment records)")

        print(f"\n- missing_amounts: {missing_amounts}")
        print("  (Debt instruments without principal amount)")

        print(f"\n- incomplete_hierarchy: {incomplete_hier}")
        print("  (Companies where hierarchy relationships may be incomplete)")

        # Summary
        print("\n" + "=" * 70)
        print("SUMMARY & RECOMMENDATIONS")
        print("=" * 70)

        print("""
PRIORITY FIXES (can automate):
1. guarantor_flag_missing: UPDATE entities SET is_guarantor = true
   WHERE id IN (SELECT DISTINCT guarantor_id FROM guarantees)

2. fixed_no_rate: Parse rate from instrument names matching pattern
   'X.XX% Notes' or 'X.XXX% Senior Notes'

3. missing_maturity: Parse year from names matching 'due YYYY' pattern

LOWER PRIORITY (data quality):
4. debt_mismatch: Review extraction logic for missing debt instruments
5. duplicate_instruments: Manual review needed - may be legitimate
6. floating_no_benchmark: Enhancement to extraction

INFO (acceptable gaps):
7. secured_without_collateral: Normal - not all collateral is documented
8. missing_amounts: Common for revolvers/credit facilities
9. incomplete_hierarchy: Some companies don't file detailed Exhibit 21
""")


if __name__ == "__main__":
    run_async(investigate_all_issues())
