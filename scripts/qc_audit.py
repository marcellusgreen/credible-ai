#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
QC Audit Script - Data Quality Checks for DebtStack

Runs comprehensive data quality checks and outputs a report.

Usage:
    python scripts/qc_audit.py [--fix] [--verbose]

Checks performed:
1. Debt instrument vs. financial mismatch (sum of instruments vs total_debt)
2. Entity count sanity (companies with 0 entities)
3. Debt without issuer (debt instruments with NULL issuer_id)
4. Orphan guarantees (guarantees referencing non-existent entities)
5. Maturity date sanity (bonds matured but still active)
6. Duplicate debt instruments (same name + issuer + maturity)
7. Missing debt amounts (instruments with NULL principal/outstanding)
8. Companies without financials
9. Invalid leverage ratios (negative or extremely high)
10. ISIN/CUSIP format validation
"""

import argparse
import sys
from datetime import date, datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from script_utils import get_db_session, run_async


class QCAudit:
    """QC Audit runner."""

    def __init__(self, db: AsyncSession, verbose: bool = False, fix: bool = False):
        self.db = db
        self.verbose = verbose
        self.fix = fix
        self.issues = []
        self.warnings = []
        self.fixes_applied = []

    def log_issue(self, check: str, severity: str, message: str, details: list = None):
        """Log an issue found during audit."""
        issue = {
            "check": check,
            "severity": severity,
            "message": message,
            "details": details or [],
        }
        if severity == "CRITICAL" or severity == "ERROR":
            self.issues.append(issue)
        else:
            self.warnings.append(issue)

        # Print immediately if verbose
        if self.verbose:
            symbol = "[X]" if severity in ("CRITICAL", "ERROR") else "[!]"
            print(f"  {symbol} [{severity}] {message}")
            if details:
                for d in details[:5]:
                    print(f"      - {d}")
                if len(details) > 5:
                    print(f"      ... and {len(details) - 5} more")

    async def check_entity_count(self):
        """Check for companies with 0 entities."""
        print("\n[1/10] Checking entity counts...")

        result = await self.db.execute(text('''
            SELECT c.ticker, c.name
            FROM companies c
            LEFT JOIN entities e ON e.company_id = c.id
            GROUP BY c.id, c.ticker, c.name
            HAVING COUNT(e.id) = 0
        '''))
        rows = result.fetchall()

        if rows:
            details = [f"{r[0]}: {r[1]}" for r in rows]
            self.log_issue(
                "entity_count",
                "ERROR",
                f"{len(rows)} companies have 0 entities",
                details
            )
        else:
            print("  [OK] All companies have at least one entity")

    async def check_debt_without_issuer(self):
        """Check for debt instruments without issuer_id."""
        print("\n[2/10] Checking debt instruments without issuer...")

        result = await self.db.execute(text('''
            SELECT c.ticker, d.name
            FROM debt_instruments d
            JOIN companies c ON c.id = d.company_id
            WHERE d.issuer_id IS NULL AND d.is_active = true
        '''))
        rows = result.fetchall()

        if rows:
            details = [f"{r[0]}: {r[1]}" for r in rows]
            self.log_issue(
                "debt_without_issuer",
                "ERROR",
                f"{len(rows)} active debt instruments have NULL issuer_id",
                details
            )
        else:
            print("  [OK] All active debt instruments have issuer_id")

    async def check_orphan_guarantees(self):
        """Check for guarantees referencing non-existent entities."""
        print("\n[3/10] Checking for orphan guarantees...")

        result = await self.db.execute(text('''
            SELECT g.id, g.guarantor_id, g.debt_instrument_id
            FROM guarantees g
            LEFT JOIN entities e ON e.id = g.guarantor_id
            WHERE e.id IS NULL
        '''))
        orphan_guarantor = result.fetchall()

        result = await self.db.execute(text('''
            SELECT g.id, g.guarantor_id, g.debt_instrument_id
            FROM guarantees g
            LEFT JOIN debt_instruments d ON d.id = g.debt_instrument_id
            WHERE d.id IS NULL
        '''))
        orphan_debt = result.fetchall()

        if orphan_guarantor:
            self.log_issue(
                "orphan_guarantees",
                "ERROR",
                f"{len(orphan_guarantor)} guarantees reference non-existent guarantor entities",
                [str(r[0]) for r in orphan_guarantor]
            )
        if orphan_debt:
            self.log_issue(
                "orphan_guarantees",
                "ERROR",
                f"{len(orphan_debt)} guarantees reference non-existent debt instruments",
                [str(r[0]) for r in orphan_debt]
            )
        if not orphan_guarantor and not orphan_debt:
            print("  [OK] No orphan guarantees found")

    async def check_matured_bonds(self):
        """Check for bonds that have matured but are still active."""
        print("\n[4/10] Checking for matured but active bonds...")

        today = date.today()
        result = await self.db.execute(text('''
            SELECT c.ticker, d.name, d.maturity_date, d.id
            FROM debt_instruments d
            JOIN companies c ON c.id = d.company_id
            WHERE d.is_active = true
            AND d.maturity_date IS NOT NULL
            AND d.maturity_date < :today
            ORDER BY d.maturity_date
        '''), {"today": today})
        rows = result.fetchall()

        if rows:
            details = [f"{r[0]}: {r[1]} (matured {r[2]})" for r in rows]
            self.log_issue(
                "matured_bonds",
                "WARNING",
                f"{len(rows)} bonds have matured but are still marked active",
                details
            )

            if self.fix:
                # Mark them as inactive
                ids = [r[3] for r in rows]
                await self.db.execute(text('''
                    UPDATE debt_instruments SET is_active = false WHERE id = ANY(:ids)
                '''), {"ids": ids})
                await self.db.commit()
                self.fixes_applied.append(f"Marked {len(ids)} matured bonds as inactive")
                print(f"  [FIX] Marked {len(ids)} matured bonds as inactive")
        else:
            print("  [OK] No matured bonds marked as active")

    async def check_duplicate_instruments(self):
        """Check for duplicate debt instruments."""
        print("\n[5/10] Checking for duplicate debt instruments...")

        result = await self.db.execute(text('''
            SELECT c.ticker, d.name, d.maturity_date, COUNT(*) as cnt
            FROM debt_instruments d
            JOIN companies c ON c.id = d.company_id
            WHERE d.is_active = true
            GROUP BY c.ticker, d.name, d.maturity_date, d.company_id
            HAVING COUNT(*) > 1
            ORDER BY COUNT(*) DESC
        '''))
        rows = result.fetchall()

        if rows:
            details = [f"{r[0]}: {r[1]} (maturity: {r[2]}) - {r[3]} duplicates" for r in rows]
            self.log_issue(
                "duplicate_instruments",
                "WARNING",
                f"{len(rows)} sets of duplicate debt instruments found",
                details
            )
        else:
            print("  [OK] No duplicate debt instruments found")

    async def check_debt_financial_mismatch(self):
        """Check for large mismatches between debt instruments and financials."""
        print("\n[6/10] Checking debt instrument vs. financial total_debt mismatch...")

        result = await self.db.execute(text('''
            WITH instrument_totals AS (
                SELECT company_id, SUM(COALESCE(outstanding, 0)) as instrument_total
                FROM debt_instruments
                WHERE is_active = true
                GROUP BY company_id
            ),
            financial_totals AS (
                SELECT DISTINCT ON (company_id) company_id, total_debt
                FROM company_financials
                ORDER BY company_id, fiscal_year DESC, fiscal_quarter DESC
            )
            SELECT c.ticker,
                   COALESCE(it.instrument_total, 0) / 100 as instruments_dollars,
                   COALESCE(ft.total_debt, 0) / 100 as financials_dollars
            FROM companies c
            LEFT JOIN instrument_totals it ON it.company_id = c.id
            LEFT JOIN financial_totals ft ON ft.company_id = c.id
            WHERE ft.total_debt > 0
            AND it.instrument_total > 0
            AND (
                it.instrument_total > ft.total_debt * 2
                OR it.instrument_total < ft.total_debt * 0.1
            )
            ORDER BY ABS(it.instrument_total - ft.total_debt) DESC
        '''))
        rows = result.fetchall()

        if rows:
            details = []
            for r in rows:
                ticker, inst, fin = r
                inst_f = float(inst) if inst else 0
                fin_f = float(fin) if fin else 0
                ratio = inst_f / fin_f if fin_f > 0 else 0
                details.append(f"{ticker}: instruments=${inst_f/1e9:.1f}B vs financials=${fin_f/1e9:.1f}B ({ratio:.1f}x)")
            self.log_issue(
                "debt_mismatch",
                "WARNING",
                f"{len(rows)} companies have >2x or <0.1x mismatch between instruments and financials",
                details
            )
        else:
            print("  [OK] No severe debt mismatches found")

    async def check_missing_amounts(self):
        """Check for debt instruments missing amounts."""
        print("\n[7/10] Checking for missing debt amounts...")

        result = await self.db.execute(text('''
            SELECT c.ticker, COUNT(*) as missing_count, COUNT(*) FILTER (WHERE d.outstanding IS NOT NULL) as has_amount
            FROM debt_instruments d
            JOIN companies c ON c.id = d.company_id
            WHERE d.is_active = true AND d.outstanding IS NULL
            GROUP BY c.ticker
            HAVING COUNT(*) > 5
            ORDER BY COUNT(*) DESC
        '''))
        rows = result.fetchall()

        if rows:
            details = [f"{r[0]}: {r[1]} instruments missing amounts" for r in rows]
            self.log_issue(
                "missing_amounts",
                "INFO",
                f"{len(rows)} companies have >5 instruments missing outstanding amounts",
                details
            )
        else:
            print("  [OK] No companies with many missing amounts")

        # Overall stats
        result = await self.db.execute(text('''
            SELECT
                COUNT(*) as total,
                COUNT(*) FILTER (WHERE outstanding IS NOT NULL) as with_amount
            FROM debt_instruments WHERE is_active = true
        '''))
        row = result.fetchone()
        pct = row[1] * 100 / row[0] if row[0] > 0 else 0
        print(f"  [INFO] Overall: {row[1]}/{row[0]} ({pct:.1f}%) instruments have outstanding amounts")

    async def check_companies_without_financials(self):
        """Check for companies without financial data."""
        print("\n[8/10] Checking for companies without financials...")

        result = await self.db.execute(text('''
            SELECT c.ticker, c.name
            FROM companies c
            LEFT JOIN company_financials cf ON cf.company_id = c.id
            WHERE cf.id IS NULL
        '''))
        rows = result.fetchall()

        if rows:
            details = [f"{r[0]}: {r[1]}" for r in rows]
            self.log_issue(
                "missing_financials",
                "WARNING",
                f"{len(rows)} companies have no financial data",
                details
            )
        else:
            print("  [OK] All companies have financial data")

    async def check_invalid_leverage(self):
        """Check for invalid leverage ratios."""
        print("\n[9/10] Checking for invalid leverage ratios...")

        result = await self.db.execute(text('''
            SELECT c.ticker, cm.leverage_ratio, cm.net_leverage_ratio, cm.interest_coverage
            FROM company_metrics cm
            JOIN companies c ON c.id = cm.company_id
            WHERE cm.leverage_ratio < 0
               OR cm.leverage_ratio > 50
               OR cm.net_leverage_ratio < -10
               OR cm.interest_coverage < 0
        '''))
        rows = result.fetchall()

        if rows:
            details = [f"{r[0]}: leverage={r[1]}x, net_leverage={r[2]}x, int_cov={r[3]}x" for r in rows]
            self.log_issue(
                "invalid_leverage",
                "WARNING",
                f"{len(rows)} companies have potentially invalid leverage metrics",
                details
            )
        else:
            print("  [OK] All leverage ratios within reasonable bounds")

    async def check_isin_cusip_format(self):
        """Check for malformed ISINs and CUSIPs."""
        print("\n[10/10] Checking ISIN/CUSIP format validity...")

        # ISIN should be 12 chars: 2 letter country + 9 alphanum + 1 check digit
        result = await self.db.execute(text('''
            SELECT c.ticker, d.isin, d.cusip, d.name
            FROM debt_instruments d
            JOIN companies c ON c.id = d.company_id
            WHERE d.is_active = true
            AND (
                (d.isin IS NOT NULL AND (LENGTH(d.isin) != 12 OR d.isin !~ '^[A-Z]{2}[A-Z0-9]{10}$'))
                OR (d.cusip IS NOT NULL AND (LENGTH(d.cusip) != 9 OR d.cusip !~ '^[A-Z0-9]{9}$'))
            )
        '''))
        rows = result.fetchall()

        if rows:
            details = [f"{r[0]}: ISIN={r[1]}, CUSIP={r[2]} ({r[3][:30]})" for r in rows]
            self.log_issue(
                "invalid_identifiers",
                "WARNING",
                f"{len(rows)} instruments have malformed ISIN/CUSIP",
                details
            )
        else:
            print("  [OK] All ISINs and CUSIPs have valid format")

    async def run_all_checks(self):
        """Run all QC checks."""
        print("=" * 60)
        print("DebtStack QC Audit")
        print("=" * 60)
        print(f"Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        if self.fix:
            print("Mode: FIX (will auto-fix issues where possible)")
        else:
            print("Mode: AUDIT ONLY (use --fix to auto-fix)")

        await self.check_entity_count()
        await self.check_debt_without_issuer()
        await self.check_orphan_guarantees()
        await self.check_matured_bonds()
        await self.check_duplicate_instruments()
        await self.check_debt_financial_mismatch()
        await self.check_missing_amounts()
        await self.check_companies_without_financials()
        await self.check_invalid_leverage()
        await self.check_isin_cusip_format()

        # Summary
        print("\n" + "=" * 60)
        print("SUMMARY")
        print("=" * 60)

        critical_errors = [i for i in self.issues if i["severity"] == "CRITICAL"]
        errors = [i for i in self.issues if i["severity"] == "ERROR"]
        warnings = self.warnings

        print(f"Critical Errors: {len(critical_errors)}")
        print(f"Errors:          {len(errors)}")
        print(f"Warnings:        {len(warnings)}")

        if self.fixes_applied:
            print(f"\nFixes Applied:   {len(self.fixes_applied)}")
            for fix in self.fixes_applied:
                print(f"  [FIX] {fix}")

        if critical_errors or errors:
            print("\n[FAIL] AUDIT FAILED - Critical issues found")
            return False
        elif warnings:
            print("\n[WARN] AUDIT PASSED WITH WARNINGS")
            return True
        else:
            print("\n[PASS] AUDIT PASSED - No issues found")
            return True


async def main():
    parser = argparse.ArgumentParser(description="Run QC audit on DebtStack data")
    parser.add_argument("--fix", action="store_true", help="Auto-fix issues where possible")
    parser.add_argument("--verbose", "-v", action="store_true", help="Show detailed output")
    args = parser.parse_args()

    async with get_db_session() as db:
        audit = QCAudit(db, verbose=args.verbose, fix=args.fix)
        success = await audit.run_all_checks()

    # Exit with appropriate code
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    run_async(main())
