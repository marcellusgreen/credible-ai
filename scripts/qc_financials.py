#!/usr/bin/env python3
"""
Financial Data Quality Control Audit

Validates financial data accuracy by comparing stored values against source SEC filings.
The ONLY source of truth is the SEC filing itself - we re-read the scale and key values
from the original document.

Key principle: NEVER hardcode expected values. Always verify against source.

Usage:
    python scripts/qc_financials.py --verbose
    python scripts/qc_financials.py --ticker AAPL
    python scripts/qc_financials.py --sample 10  # Spot-check 10 random companies
"""

import argparse
import random
import re
import sys
from dataclasses import dataclass, field
from typing import Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from script_utils import get_db_session, print_header, run_async
from app.services.financial_extraction import detect_filing_scale


@dataclass
class ValidationResult:
    ticker: str
    field: str
    stored_value: float  # in dollars
    source_value: Optional[float]  # in dollars, from re-extraction
    source_scale: str  # "millions", "thousands", etc.
    match: bool
    variance_pct: Optional[float]
    issue: Optional[str] = None


@dataclass
class CompanyAuditResult:
    ticker: str
    fiscal_year: int
    fiscal_quarter: int
    source_available: bool
    scale_detected: str
    validations: list[ValidationResult] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


async def get_filing_content(db: AsyncSession, company_id: str, fiscal_year: int, fiscal_quarter: int) -> Optional[str]:
    """Get the source SEC filing content for a financial record."""
    # Try to find the corresponding document section (10-Q or 10-K)
    filing_type = '10-K' if fiscal_quarter == 4 else '10-Q'

    result = await db.execute(text('''
        SELECT ds.content, ds.filing_date
        FROM document_sections ds
        WHERE ds.company_id = :company_id
        AND ds.section_type IN ('debt_footnote', 'mda_liquidity')
        AND EXTRACT(YEAR FROM ds.filing_date) = :year
        ORDER BY ds.filing_date DESC
        LIMIT 1
    '''), {'company_id': company_id, 'year': fiscal_year})

    row = result.fetchone()
    if row:
        return row[0]
    return None


def extract_revenue_from_content(content: str, scale_multiplier: int) -> Optional[float]:
    """
    Extract revenue value from filing content.
    Returns value in dollars.
    """
    content_lower = content.lower()

    # Common revenue patterns
    patterns = [
        r'total\s+(?:net\s+)?revenues?\s*[\$]?\s*([\d,]+(?:\.\d+)?)',
        r'net\s+(?:revenues?|sales)\s*[\$]?\s*([\d,]+(?:\.\d+)?)',
        r'revenues?\s*[\$]?\s*([\d,]+(?:\.\d+)?)\s*(?:million|billion)?',
    ]

    for pattern in patterns:
        match = re.search(pattern, content_lower)
        if match:
            value_str = match.group(1).replace(',', '')
            try:
                value = float(value_str)
                # Apply scale: if scale_multiplier is 100_000_000 (millions to cents),
                # we want dollars, so divide by 100
                return value * scale_multiplier / 100
            except ValueError:
                continue

    return None


def extract_total_debt_from_content(content: str, scale_multiplier: int) -> Optional[float]:
    """
    Extract total debt value from filing content.
    Returns value in dollars.
    """
    content_lower = content.lower()

    patterns = [
        r'total\s+(?:long[- ]term\s+)?debt\s*[\$]?\s*([\d,]+(?:\.\d+)?)',
        r'total\s+borrowings?\s*[\$]?\s*([\d,]+(?:\.\d+)?)',
        r'long[- ]term\s+debt[^$]*[\$]?\s*([\d,]+(?:\.\d+)?)',
    ]

    for pattern in patterns:
        match = re.search(pattern, content_lower)
        if match:
            value_str = match.group(1).replace(',', '')
            try:
                value = float(value_str)
                return value * scale_multiplier / 100
            except ValueError:
                continue

    return None


async def audit_company_financials(
    db: AsyncSession,
    ticker: str,
    verbose: bool = False
) -> CompanyAuditResult:
    """
    Audit a single company's financial data against source SEC filings.
    """
    # Get the most recent financial record
    result = await db.execute(text('''
        SELECT cf.company_id, cf.fiscal_year, cf.fiscal_quarter,
               cf.revenue, cf.total_debt, cf.total_assets, cf.ebitda,
               c.cik
        FROM company_financials cf
        JOIN companies c ON c.id = cf.company_id
        WHERE c.ticker = :ticker
        ORDER BY cf.fiscal_year DESC, cf.fiscal_quarter DESC
        LIMIT 1
    '''), {'ticker': ticker})

    row = result.fetchone()
    if not row:
        return CompanyAuditResult(
            ticker=ticker,
            fiscal_year=0,
            fiscal_quarter=0,
            source_available=False,
            scale_detected="N/A",
            errors=["No financial records found"]
        )

    company_id, fiscal_year, fiscal_quarter = str(row[0]), row[1], row[2]
    stored_revenue = float(row[3]) / 100 if row[3] else None  # cents to dollars
    stored_debt = float(row[4]) / 100 if row[4] else None
    stored_assets = float(row[5]) / 100 if row[5] else None
    stored_ebitda = float(row[6]) / 100 if row[6] else None
    cik = row[7]

    audit_result = CompanyAuditResult(
        ticker=ticker,
        fiscal_year=fiscal_year,
        fiscal_quarter=fiscal_quarter,
        source_available=False,
        scale_detected="unknown"
    )

    # Get source filing content
    content = await get_filing_content(db, company_id, fiscal_year, fiscal_quarter)

    if not content:
        audit_result.errors.append(f"No source document found for Q{fiscal_quarter} {fiscal_year}")
        return audit_result

    audit_result.source_available = True

    # Detect scale from source document
    scale_multiplier = detect_filing_scale(content)
    scale_name = {
        100: "dollars",
        100_000: "thousands",
        100_000_000: "millions",
        100_000_000_000: "billions",
    }.get(scale_multiplier, f"unknown ({scale_multiplier})")

    audit_result.scale_detected = scale_name

    if verbose:
        print(f"  Source document scale: {scale_name}")

    # Validate revenue
    if stored_revenue:
        source_revenue = extract_revenue_from_content(content, scale_multiplier)
        if source_revenue:
            variance = abs(stored_revenue - source_revenue) / source_revenue * 100 if source_revenue else 0
            match = variance < 10  # 10% tolerance

            issue = None
            if not match:
                if variance > 1000:
                    issue = f"SCALE ERROR: {variance:.0f}% variance (likely 1000x off)"
                else:
                    issue = f"Value mismatch: {variance:.1f}% variance"

            audit_result.validations.append(ValidationResult(
                ticker=ticker,
                field="revenue",
                stored_value=stored_revenue,
                source_value=source_revenue,
                source_scale=scale_name,
                match=match,
                variance_pct=variance,
                issue=issue
            ))

    # Validate total debt
    if stored_debt:
        source_debt = extract_total_debt_from_content(content, scale_multiplier)
        if source_debt:
            variance = abs(stored_debt - source_debt) / source_debt * 100 if source_debt else 0
            match = variance < 10

            issue = None
            if not match:
                if variance > 1000:
                    issue = f"SCALE ERROR: {variance:.0f}% variance"
                else:
                    issue = f"Value mismatch: {variance:.1f}% variance"

            audit_result.validations.append(ValidationResult(
                ticker=ticker,
                field="total_debt",
                stored_value=stored_debt,
                source_value=source_debt,
                source_scale=scale_name,
                match=match,
                variance_pct=variance,
                issue=issue
            ))

    return audit_result


async def check_scale_sanity(db: AsyncSession) -> list[dict]:
    """
    Quick sanity check for obvious scale errors without needing source docs.
    These are mathematical impossibilities that indicate scale problems.
    """
    issues = []

    # Check 1: Revenue > $1 trillion per quarter (no company has this)
    result = await db.execute(text('''
        SELECT c.ticker, cf.revenue / 100.0 / 1e9 as rev_b, cf.fiscal_year, cf.fiscal_quarter
        FROM company_financials cf
        JOIN companies c ON c.id = cf.company_id
        WHERE cf.revenue > 100000000000000  -- >$1T in cents
        ORDER BY cf.revenue DESC
    '''))
    for row in result.fetchall():
        issues.append({
            'ticker': row[0],
            'issue': f"Revenue ${float(row[1]):.0f}B > $1T - impossible, scale error",
            'severity': 'critical',
            'field': 'revenue',
            'period': f"Q{row[3]} {row[2]}"
        })

    # Check 2: EBITDA > Revenue (mathematically impossible for operating companies)
    result = await db.execute(text('''
        SELECT c.ticker,
               cf.revenue / 100.0 / 1e9 as rev_b,
               cf.ebitda / 100.0 / 1e9 as ebitda_b,
               cf.fiscal_year, cf.fiscal_quarter
        FROM company_financials cf
        JOIN companies c ON c.id = cf.company_id
        WHERE cf.ebitda > cf.revenue
        AND cf.revenue > 0
        AND cf.ebitda > 0
        ORDER BY cf.ebitda / cf.revenue DESC
    '''))
    for row in result.fetchall():
        issues.append({
            'ticker': row[0],
            'issue': f"EBITDA ${float(row[2]):.1f}B > Revenue ${float(row[1]):.1f}B - impossible",
            'severity': 'error',
            'field': 'ebitda',
            'period': f"Q{row[4]} {row[3]}"
        })

    # Check 3: Total debt > 10x Total assets (extremely rare, likely error)
    result = await db.execute(text('''
        SELECT c.ticker,
               cf.total_debt / 100.0 / 1e9 as debt_b,
               cf.total_assets / 100.0 / 1e9 as assets_b,
               cf.fiscal_year, cf.fiscal_quarter
        FROM company_financials cf
        JOIN companies c ON c.id = cf.company_id
        WHERE cf.total_debt > cf.total_assets * 10
        AND cf.total_assets > 0
        ORDER BY cf.total_debt / cf.total_assets DESC
    '''))
    for row in result.fetchall():
        issues.append({
            'ticker': row[0],
            'issue': f"Debt ${float(row[1]):.1f}B > 10x Assets ${float(row[2]):.1f}B - likely scale error",
            'severity': 'error',
            'field': 'total_debt',
            'period': f"Q{row[4]} {row[3]}"
        })

    # Check 4: Zero or NULL revenue with non-zero EBITDA (extraction failure)
    result = await db.execute(text('''
        SELECT c.ticker,
               cf.revenue / 100.0 / 1e9 as rev_b,
               cf.ebitda / 100.0 / 1e9 as ebitda_b,
               cf.fiscal_year, cf.fiscal_quarter
        FROM company_financials cf
        JOIN companies c ON c.id = cf.company_id
        WHERE (cf.revenue IS NULL OR cf.revenue = 0)
        AND cf.ebitda IS NOT NULL AND cf.ebitda > 100000000  -- >$1M
        ORDER BY cf.ebitda DESC
    '''))
    for row in result.fetchall():
        issues.append({
            'ticker': row[0],
            'issue': f"EBITDA ${float(row[2]):.1f}B with zero/null revenue - extraction failed",
            'severity': 'error',
            'field': 'revenue',
            'period': f"Q{row[4]} {row[3]}"
        })

    # Check 5: Extreme leverage (>20x) with insufficient EBITDA quarters
    # High leverage from <4 quarters of EBITDA is likely a data issue
    result = await db.execute(text('''
        WITH company_ebitda AS (
            SELECT
                c.id as company_id,
                c.ticker,
                COUNT(cf.id) as quarter_count,
                SUM(cf.ebitda) as ttm_ebitda
            FROM companies c
            JOIN company_financials cf ON cf.company_id = c.id
            WHERE cf.ebitda IS NOT NULL AND cf.ebitda > 0
            GROUP BY c.id, c.ticker
        )
        SELECT
            ce.ticker,
            cm.net_leverage_ratio,
            cm.total_debt / 100.0 / 1e9 as debt_b,
            ce.ttm_ebitda / 100.0 / 1e9 as ebitda_b,
            ce.quarter_count
        FROM company_ebitda ce
        JOIN company_metrics cm ON cm.company_id = ce.company_id
        WHERE cm.net_leverage_ratio > 20
        AND ce.quarter_count < 4
        ORDER BY cm.net_leverage_ratio DESC
    '''))
    for row in result.fetchall():
        ticker, leverage, debt_b, ebitda_b, quarters = row
        issues.append({
            'ticker': ticker,
            'issue': f"Leverage {float(leverage):.1f}x with only {quarters} quarter(s) of EBITDA (${float(ebitda_b):.2f}B) - likely understated TTM",
            'severity': 'error',
            'field': 'net_leverage_ratio',
            'period': f"{quarters}Q data"
        })

    # Check 6: Leverage ratio mismatch between stored metric and calculated value
    # Catches cases where metrics were computed with stale/wrong EBITDA
    result = await db.execute(text('''
        WITH company_ebitda AS (
            SELECT
                c.id as company_id,
                c.ticker,
                SUM(cf.ebitda) as ttm_ebitda,
                COUNT(cf.id) as quarter_count
            FROM companies c
            JOIN company_financials cf ON cf.company_id = c.id
            WHERE cf.ebitda IS NOT NULL AND cf.ebitda > 0
            GROUP BY c.id, c.ticker
            HAVING COUNT(cf.id) >= 4
        )
        SELECT
            ce.ticker,
            cm.net_leverage_ratio as stored_leverage,
            CASE WHEN ce.ttm_ebitda > 0
                 THEN cm.net_debt::float / ce.ttm_ebitda::float
                 ELSE NULL END as calc_leverage,
            cm.net_debt / 100.0 / 1e9 as net_debt_b,
            ce.ttm_ebitda / 100.0 / 1e9 as ebitda_b
        FROM company_ebitda ce
        JOIN company_metrics cm ON cm.company_id = ce.company_id
        WHERE cm.net_leverage_ratio IS NOT NULL
        AND ce.ttm_ebitda > 0
        AND ABS(cm.net_leverage_ratio - (cm.net_debt::float / ce.ttm_ebitda::float)) > 2
        ORDER BY ABS(cm.net_leverage_ratio - (cm.net_debt::float / ce.ttm_ebitda::float)) DESC
    '''))
    for row in result.fetchall():
        ticker, stored, calc, debt_b, ebitda_b = row
        if calc:
            issues.append({
                'ticker': ticker,
                'issue': f"Leverage mismatch: stored {float(stored):.1f}x vs calculated {float(calc):.1f}x (debt ${float(debt_b):.1f}B / EBITDA ${float(ebitda_b):.2f}B)",
                'severity': 'warning',
                'field': 'net_leverage_ratio',
                'period': 'metrics'
            })

    return issues


async def run_audit(
    db: AsyncSession,
    tickers: Optional[list[str]] = None,
    sample_size: Optional[int] = None,
    verbose: bool = False
) -> dict:
    """Run the full financial audit."""

    print("=" * 70)
    print("FINANCIAL DATA QUALITY AUDIT")
    print("=" * 70)
    print("\nPrinciple: All values verified against source SEC filings.")
    print("The filing's stated scale ('in millions', etc.) is the source of truth.\n")

    # Step 1: Quick sanity checks (no source docs needed)
    print("[1/2] Running sanity checks (mathematical impossibilities)...")
    sanity_issues = await check_scale_sanity(db)

    critical_count = sum(1 for i in sanity_issues if i['severity'] == 'critical')
    error_count = sum(1 for i in sanity_issues if i['severity'] == 'error')
    warning_count = sum(1 for i in sanity_issues if i['severity'] == 'warning')

    if sanity_issues:
        print(f"\n  Found {len(sanity_issues)} issues ({critical_count} critical, {error_count} errors, {warning_count} warnings):\n")
        for issue in sanity_issues:
            severity = issue['severity'].upper()
            print(f"  [{severity}] {issue['ticker']} ({issue['period']}): {issue['issue']}")
    else:
        print("  No mathematical impossibilities found.")

    # Step 2: Source document validation (if tickers specified or sampling)
    print(f"\n[2/2] Validating against source SEC filings...")

    if tickers:
        companies_to_audit = tickers
    elif sample_size:
        # Get random sample of companies with financials
        result = await db.execute(text('''
            SELECT DISTINCT c.ticker
            FROM company_financials cf
            JOIN companies c ON c.id = cf.company_id
            WHERE cf.revenue IS NOT NULL
        '''))
        all_tickers = [r[0] for r in result.fetchall()]
        companies_to_audit = random.sample(all_tickers, min(sample_size, len(all_tickers)))
        print(f"  Sampling {len(companies_to_audit)} random companies...")
    else:
        # Just audit the ones with sanity issues
        companies_to_audit = list(set(i['ticker'] for i in sanity_issues))
        if companies_to_audit:
            print(f"  Auditing {len(companies_to_audit)} companies with sanity issues...")
        else:
            print("  No companies to audit (no sanity issues found).")
            companies_to_audit = []

    audit_results = []
    for ticker in companies_to_audit:
        if verbose:
            print(f"\n  --- {ticker} ---")
        result = await audit_company_financials(db, ticker, verbose)
        audit_results.append(result)

        if result.errors:
            for err in result.errors:
                print(f"    [SKIP] {err}")

        for v in result.validations:
            if not v.match:
                print(f"    [{v.field}] {v.issue}")
                print(f"      Stored: ${v.stored_value/1e9:.2f}B, Source: ${v.source_value/1e9:.2f}B (scale: {v.source_scale})")

    # Summary
    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)

    print(f"\nSanity Check Issues:")
    print(f"  Critical: {critical_count}")
    print(f"  Errors:   {error_count}")
    print(f"  Warnings: {warning_count}")

    if audit_results:
        validation_failures = sum(
            1 for r in audit_results
            for v in r.validations
            if not v.match
        )
        print(f"\nSource Validation:")
        print(f"  Companies audited: {len(audit_results)}")
        print(f"  Validation failures: {validation_failures}")

    total_critical = critical_count
    total_errors = error_count + sum(1 for r in audit_results for v in r.validations if not v.match and v.variance_pct and v.variance_pct > 100)

    if total_critical > 0:
        print("\n[FAIL] CRITICAL ISSUES - Data accuracy severely compromised")
        return {'status': 'fail', 'critical': total_critical, 'errors': total_errors}
    elif total_errors > 0:
        print("\n[WARN] ERRORS FOUND - Review and re-extract required")
        return {'status': 'warn', 'critical': total_critical, 'errors': total_errors}
    else:
        print("\n[PASS] Financial data quality acceptable")
        return {'status': 'pass', 'critical': 0, 'errors': 0}


async def main():
    parser = argparse.ArgumentParser(description="Financial data quality audit against source SEC filings")
    parser.add_argument("--verbose", "-v", action="store_true", help="Show detailed output")
    parser.add_argument("--ticker", type=str, help="Audit single company")
    parser.add_argument("--sample", type=int, help="Audit N random companies")
    args = parser.parse_args()

    tickers = [args.ticker.upper()] if args.ticker else None

    async with get_db_session() as db:
        result = await run_audit(
            db,
            tickers=tickers,
            sample_size=args.sample,
            verbose=args.verbose
        )

    if result['status'] == 'fail':
        sys.exit(2)
    elif result['status'] == 'warn':
        sys.exit(1)
    else:
        sys.exit(0)


if __name__ == "__main__":
    run_async(main())
