#!/usr/bin/env python3
"""
Master Data Quality Control Suite

Comprehensive QC checks across all data tables. ACCURACY IS THE NUMBER ONE PRODUCT.

Categories:
1. Referential Integrity - Foreign keys, missing references
2. Mathematical Impossibilities - Values that can't be real
3. Cross-Table Consistency - Data should agree across tables
4. Business Logic Validation - Credit-specific rules
5. Data Completeness - Critical fields that should be populated

Usage:
    python scripts/qc_master.py                    # Run all checks
    python scripts/qc_master.py --category integrity  # Run specific category
    python scripts/qc_master.py --verbose          # Show all details
    python scripts/qc_master.py --fix              # Auto-fix where safe
"""

import argparse
import asyncio
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime, date
from typing import Optional
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker

load_dotenv()


@dataclass
class Issue:
    severity: str  # 'critical', 'error', 'warning', 'info'
    category: str  # 'integrity', 'impossible', 'consistency', 'business', 'completeness'
    table: str
    check_name: str
    description: str
    affected_count: int
    sample_ids: list = field(default_factory=list)
    auto_fixable: bool = False


class QCMaster:
    def __init__(self, db: AsyncSession, verbose: bool = False):
        self.db = db
        self.verbose = verbose
        self.issues: list[Issue] = []

    def add_issue(self, **kwargs):
        issue = Issue(**kwargs)
        self.issues.append(issue)
        if self.verbose or issue.severity in ('critical', 'error'):
            symbol = {'critical': '!!', 'error': 'X', 'warning': '!', 'info': 'i'}[issue.severity]
            print(f"  [{symbol}] {issue.check_name}: {issue.description} ({issue.affected_count})")

    # =========================================================================
    # CATEGORY 1: REFERENTIAL INTEGRITY
    # =========================================================================

    async def check_integrity(self):
        """Check foreign key relationships and orphan records."""
        print("\n" + "=" * 70)
        print("CATEGORY 1: REFERENTIAL INTEGRITY")
        print("=" * 70)

        # 1.1 Debt instruments with invalid issuer_id
        result = await self.db.execute(text('''
            SELECT di.id, di.name, di.issuer_id
            FROM debt_instruments di
            LEFT JOIN entities e ON e.id = di.issuer_id
            WHERE di.issuer_id IS NOT NULL AND e.id IS NULL
        '''))
        rows = result.fetchall()
        if rows:
            self.add_issue(
                severity='critical',
                category='integrity',
                table='debt_instruments',
                check_name='orphan_issuer',
                description='Debt instruments reference non-existent issuer entities',
                affected_count=len(rows),
                sample_ids=[str(r[0]) for r in rows[:5]]
            )

        # 1.2 Guarantees with invalid debt_instrument_id
        result = await self.db.execute(text('''
            SELECT g.id, g.debt_instrument_id, g.guarantor_id
            FROM guarantees g
            LEFT JOIN debt_instruments di ON di.id = g.debt_instrument_id
            WHERE di.id IS NULL
        '''))
        rows = result.fetchall()
        if rows:
            self.add_issue(
                severity='critical',
                category='integrity',
                table='guarantees',
                check_name='orphan_debt_guarantee',
                description='Guarantees reference non-existent debt instruments',
                affected_count=len(rows),
                sample_ids=[str(r[0]) for r in rows[:5]]
            )

        # 1.3 Guarantees with invalid guarantor_id
        result = await self.db.execute(text('''
            SELECT g.id, g.debt_instrument_id, g.guarantor_id
            FROM guarantees g
            LEFT JOIN entities e ON e.id = g.guarantor_id
            WHERE e.id IS NULL
        '''))
        rows = result.fetchall()
        if rows:
            self.add_issue(
                severity='critical',
                category='integrity',
                table='guarantees',
                check_name='orphan_guarantor',
                description='Guarantees reference non-existent guarantor entities',
                affected_count=len(rows),
                sample_ids=[str(r[0]) for r in rows[:5]]
            )

        # 1.4 Entities with invalid parent_id
        result = await self.db.execute(text('''
            SELECT e.id, e.name, e.parent_id
            FROM entities e
            LEFT JOIN entities p ON p.id = e.parent_id
            WHERE e.parent_id IS NOT NULL AND p.id IS NULL
        '''))
        rows = result.fetchall()
        if rows:
            self.add_issue(
                severity='error',
                category='integrity',
                table='entities',
                check_name='orphan_parent',
                description='Entities reference non-existent parent entities',
                affected_count=len(rows),
                sample_ids=[str(r[0]) for r in rows[:5]]
            )

        # 1.5 Collateral with invalid debt_instrument_id
        result = await self.db.execute(text('''
            SELECT c.id, c.debt_instrument_id
            FROM collateral c
            LEFT JOIN debt_instruments di ON di.id = c.debt_instrument_id
            WHERE di.id IS NULL
        '''))
        rows = result.fetchall()
        if rows:
            self.add_issue(
                severity='error',
                category='integrity',
                table='collateral',
                check_name='orphan_collateral',
                description='Collateral records reference non-existent debt instruments',
                affected_count=len(rows),
                sample_ids=[str(r[0]) for r in rows[:5]]
            )

        # 1.6 Bond pricing with invalid debt_instrument_id
        result = await self.db.execute(text('''
            SELECT bp.id, bp.debt_instrument_id
            FROM bond_pricing bp
            LEFT JOIN debt_instruments di ON di.id = bp.debt_instrument_id
            WHERE bp.debt_instrument_id IS NOT NULL AND di.id IS NULL
        '''))
        rows = result.fetchall()
        if rows:
            self.add_issue(
                severity='error',
                category='integrity',
                table='bond_pricing',
                check_name='orphan_pricing',
                description='Bond pricing references non-existent debt instruments',
                affected_count=len(rows),
                sample_ids=[str(r[0]) for r in rows[:5]]
            )

        # 1.7 Companies without any entities
        result = await self.db.execute(text('''
            SELECT c.id, c.ticker, c.name
            FROM companies c
            LEFT JOIN entities e ON e.company_id = c.id
            WHERE e.id IS NULL
        '''))
        rows = result.fetchall()
        if rows:
            self.add_issue(
                severity='warning',
                category='integrity',
                table='companies',
                check_name='company_no_entities',
                description='Companies with no entities',
                affected_count=len(rows),
                sample_ids=[r[1] for r in rows[:5]]
            )

        # 1.8 Companies without root entity
        result = await self.db.execute(text('''
            SELECT c.id, c.ticker, c.name
            FROM companies c
            WHERE EXISTS (SELECT 1 FROM entities e WHERE e.company_id = c.id)
            AND NOT EXISTS (SELECT 1 FROM entities e WHERE e.company_id = c.id AND e.is_root = true)
        '''))
        rows = result.fetchall()
        if rows:
            self.add_issue(
                severity='error',
                category='integrity',
                table='companies',
                check_name='company_no_root',
                description='Companies have entities but no root entity (is_root=true)',
                affected_count=len(rows),
                sample_ids=[r[1] for r in rows[:5]]
            )

    # =========================================================================
    # CATEGORY 2: MATHEMATICAL IMPOSSIBILITIES
    # =========================================================================

    async def check_impossible(self):
        """Check for mathematically impossible values."""
        print("\n" + "=" * 70)
        print("CATEGORY 2: MATHEMATICAL IMPOSSIBILITIES")
        print("=" * 70)

        # 2.1 Revenue > $1T quarterly (impossible)
        result = await self.db.execute(text('''
            SELECT c.ticker, cf.fiscal_year, cf.fiscal_quarter, cf.revenue / 100.0 / 1e9 as rev_b
            FROM company_financials cf
            JOIN companies c ON c.id = cf.company_id
            WHERE cf.revenue > 100000000000000  -- >$1T in cents
        '''))
        rows = result.fetchall()
        if rows:
            self.add_issue(
                severity='critical',
                category='impossible',
                table='company_financials',
                check_name='revenue_over_1t',
                description='Revenue > $1T (scale error)',
                affected_count=len(rows),
                sample_ids=[f"{r[0]} Q{r[2]} {r[1]}: ${r[3]:.0f}B" for r in rows[:5]]
            )

        # 2.2 EBITDA > Revenue (impossible for operating companies)
        result = await self.db.execute(text('''
            SELECT c.ticker, cf.fiscal_year, cf.fiscal_quarter,
                   cf.revenue / 100.0 / 1e9 as rev_b,
                   cf.ebitda / 100.0 / 1e9 as ebitda_b
            FROM company_financials cf
            JOIN companies c ON c.id = cf.company_id
            WHERE cf.ebitda > cf.revenue AND cf.revenue > 0 AND cf.ebitda > 0
        '''))
        rows = result.fetchall()
        if rows:
            # Filter out known exceptions (MSTR = Bitcoin company)
            non_exceptions = [r for r in rows if r[0] not in ('MSTR',)]
            if non_exceptions:
                self.add_issue(
                    severity='error',
                    category='impossible',
                    table='company_financials',
                    check_name='ebitda_exceeds_revenue',
                    description='EBITDA > Revenue (impossible)',
                    affected_count=len(non_exceptions),
                    sample_ids=[f"{r[0]}: EBITDA ${r[4]:.1f}B > Rev ${r[3]:.1f}B" for r in non_exceptions[:5]]
                )

        # 2.3 Debt > 10x Assets (extremely unusual)
        result = await self.db.execute(text('''
            SELECT c.ticker, cf.fiscal_year, cf.fiscal_quarter,
                   cf.total_debt / 100.0 / 1e9 as debt_b,
                   cf.total_assets / 100.0 / 1e9 as assets_b
            FROM company_financials cf
            JOIN companies c ON c.id = cf.company_id
            WHERE cf.total_debt > cf.total_assets * 10
            AND cf.total_assets > 0
        '''))
        rows = result.fetchall()
        if rows:
            self.add_issue(
                severity='error',
                category='impossible',
                table='company_financials',
                check_name='debt_exceeds_10x_assets',
                description='Debt > 10x Assets (likely scale error or missing assets)',
                affected_count=len(rows),
                sample_ids=[f"{r[0]}: Debt ${r[3]:.1f}B, Assets ${r[4]:.1f}B" for r in rows[:5]]
            )

        # 2.4 Negative amounts (should never happen)
        result = await self.db.execute(text('''
            SELECT c.ticker, cf.fiscal_year, cf.fiscal_quarter, 'revenue' as field
            FROM company_financials cf
            JOIN companies c ON c.id = cf.company_id
            WHERE cf.revenue < 0
            UNION ALL
            SELECT c.ticker, cf.fiscal_year, cf.fiscal_quarter, 'total_debt'
            FROM company_financials cf
            JOIN companies c ON c.id = cf.company_id
            WHERE cf.total_debt < 0
            UNION ALL
            SELECT c.ticker, cf.fiscal_year, cf.fiscal_quarter, 'total_assets'
            FROM company_financials cf
            JOIN companies c ON c.id = cf.company_id
            WHERE cf.total_assets < 0
        '''))
        rows = result.fetchall()
        if rows:
            self.add_issue(
                severity='critical',
                category='impossible',
                table='company_financials',
                check_name='negative_amounts',
                description='Negative monetary amounts (data corruption)',
                affected_count=len(rows),
                sample_ids=[f"{r[0]} Q{r[2]} {r[1]}: {r[3]}" for r in rows[:5]]
            )

        # 2.5 Interest rate > 50% (extremely unusual)
        result = await self.db.execute(text('''
            SELECT c.ticker, di.name, di.interest_rate
            FROM debt_instruments di
            JOIN entities e ON e.id = di.issuer_id
            JOIN companies c ON c.id = e.company_id
            WHERE di.interest_rate > 5000  -- > 50% in bps
        '''))
        rows = result.fetchall()
        if rows:
            self.add_issue(
                severity='warning',
                category='impossible',
                table='debt_instruments',
                check_name='extreme_interest_rate',
                description='Interest rate > 50% (verify this is correct)',
                affected_count=len(rows),
                sample_ids=[f"{r[0]}: {r[1]} at {r[2]/100:.1f}%" for r in rows[:5]]
            )

        # 2.6 Maturity before issue date
        result = await self.db.execute(text('''
            SELECT c.ticker, di.name, di.issue_date, di.maturity_date
            FROM debt_instruments di
            JOIN entities e ON e.id = di.issuer_id
            JOIN companies c ON c.id = e.company_id
            WHERE di.maturity_date < di.issue_date
            AND di.issue_date IS NOT NULL
            AND di.maturity_date IS NOT NULL
        '''))
        rows = result.fetchall()
        if rows:
            self.add_issue(
                severity='error',
                category='impossible',
                table='debt_instruments',
                check_name='maturity_before_issue',
                description='Maturity date before issue date',
                affected_count=len(rows),
                sample_ids=[f"{r[0]}: {r[1]}" for r in rows[:5]]
            )

        # 2.7 Future issue dates (exclude estimated dates - those are calculated from maturity)
        result = await self.db.execute(text('''
            SELECT c.ticker, di.name, di.issue_date
            FROM debt_instruments di
            JOIN entities e ON e.id = di.issuer_id
            JOIN companies c ON c.id = e.company_id
            WHERE di.issue_date > CURRENT_DATE
            AND (di.issue_date_estimated = false OR di.issue_date_estimated IS NULL)
        '''))
        rows = result.fetchall()
        if rows:
            self.add_issue(
                severity='error',
                category='impossible',
                table='debt_instruments',
                check_name='future_issue_date',
                description='Issue date is in the future (non-estimated)',
                affected_count=len(rows),
                sample_ids=[f"{r[0]}: {r[1]} issued {r[2]}" for r in rows[:5]]
            )

        # 2.8 Ownership percentage > 100%
        result = await self.db.execute(text('''
            SELECT c.ticker, e.name, ol.ownership_pct
            FROM ownership_links ol
            JOIN entities e ON e.id = ol.child_entity_id
            JOIN companies c ON c.id = e.company_id
            WHERE ol.ownership_pct > 100
        '''))
        rows = result.fetchall()
        if rows:
            self.add_issue(
                severity='error',
                category='impossible',
                table='ownership_links',
                check_name='ownership_over_100',
                description='Ownership percentage > 100%',
                affected_count=len(rows),
                sample_ids=[f"{r[0]}: {r[1]} at {r[2]}%" for r in rows[:5]]
            )

    # =========================================================================
    # CATEGORY 3: CROSS-TABLE CONSISTENCY
    # =========================================================================

    async def check_consistency(self):
        """Check data consistency across related tables."""
        print("\n" + "=" * 70)
        print("CATEGORY 3: CROSS-TABLE CONSISTENCY")
        print("=" * 70)

        # 3.1 Debt instrument sum vs financial total_debt (>50% variance)
        result = await self.db.execute(text('''
            WITH debt_sums AS (
                SELECT e.company_id,
                       SUM(COALESCE(di.outstanding, di.principal, 0)) as instrument_debt
                FROM debt_instruments di
                JOIN entities e ON e.id = di.issuer_id
                WHERE di.is_active = true
                GROUP BY e.company_id
            ),
            latest_financials AS (
                SELECT DISTINCT ON (company_id) company_id, total_debt, fiscal_year, fiscal_quarter
                FROM company_financials
                WHERE total_debt IS NOT NULL AND total_debt > 0
                ORDER BY company_id, fiscal_year DESC, fiscal_quarter DESC
            )
            SELECT c.ticker,
                   ds.instrument_debt / 100.0 / 1e9 as inst_debt_b,
                   lf.total_debt / 100.0 / 1e9 as fin_debt_b,
                   ABS(ds.instrument_debt - lf.total_debt) * 100.0 / NULLIF(lf.total_debt, 0) as variance_pct
            FROM debt_sums ds
            JOIN latest_financials lf ON lf.company_id = ds.company_id
            JOIN companies c ON c.id = ds.company_id
            WHERE ABS(ds.instrument_debt - lf.total_debt) * 100.0 / NULLIF(lf.total_debt, 0) > 50
            AND lf.total_debt > 100000000000  -- >$1B to avoid small-debt noise
            ORDER BY variance_pct DESC
        '''))
        rows = result.fetchall()
        if rows:
            # Filter out banks (they have deposits/wholesale funding not in notes)
            non_banks = [r for r in rows if r[0] not in ('JPM', 'BAC', 'C', 'WFC', 'GS', 'MS', 'SCHW', 'COF')]
            if non_banks:
                self.add_issue(
                    severity='warning',
                    category='consistency',
                    table='debt_instruments',
                    check_name='debt_mismatch',
                    description='Sum of debt instruments differs >50% from financials total_debt',
                    affected_count=len(non_banks),
                    sample_ids=[f"{r[0]}: Inst ${r[1]:.1f}B vs Fin ${r[2]:.1f}B ({r[3]:.0f}%)" for r in non_banks[:5]]
                )

        # 3.2 Guarantor entity without is_guarantor flag
        result = await self.db.execute(text('''
            SELECT DISTINCT c.ticker, e.name
            FROM guarantees g
            JOIN entities e ON e.id = g.guarantor_id
            JOIN companies c ON c.id = e.company_id
            WHERE e.is_guarantor = false OR e.is_guarantor IS NULL
        '''))
        rows = result.fetchall()
        if rows:
            self.add_issue(
                severity='warning',
                category='consistency',
                table='entities',
                check_name='guarantor_flag_missing',
                description='Entities in guarantees table but is_guarantor=false',
                affected_count=len(rows),
                sample_ids=[f"{r[0]}: {r[1]}" for r in rows[:5]],
                auto_fixable=True
            )

        # 3.3 Secured debt without collateral records
        result = await self.db.execute(text('''
            SELECT c.ticker, di.name, di.seniority
            FROM debt_instruments di
            JOIN entities e ON e.id = di.issuer_id
            JOIN companies c ON c.id = e.company_id
            LEFT JOIN collateral col ON col.debt_instrument_id = di.id
            WHERE di.seniority IN ('senior_secured', 'first_lien', 'second_lien')
            AND di.is_active = true
            AND col.id IS NULL
        '''))
        rows = result.fetchall()
        if rows:
            self.add_issue(
                severity='info',
                category='consistency',
                table='collateral',
                check_name='secured_without_collateral',
                description='Secured debt instruments without collateral records',
                affected_count=len(rows),
                sample_ids=[f"{r[0]}: {r[1]}" for r in rows[:5]]
            )

        # 3.4 Matured bonds still marked active
        result = await self.db.execute(text('''
            SELECT c.ticker, di.name, di.maturity_date
            FROM debt_instruments di
            JOIN entities e ON e.id = di.issuer_id
            JOIN companies c ON c.id = e.company_id
            WHERE di.maturity_date < CURRENT_DATE
            AND di.is_active = true
        '''))
        rows = result.fetchall()
        if rows:
            self.add_issue(
                severity='error',
                category='consistency',
                table='debt_instruments',
                check_name='matured_still_active',
                description='Matured bonds still marked is_active=true',
                affected_count=len(rows),
                sample_ids=[f"{r[0]}: {r[1]} matured {r[2]}" for r in rows[:5]],
                auto_fixable=True
            )

        # 3.5 Duplicate debt instruments (same issuer, name, maturity)
        # Note: Different issuers within same company can have same-named instruments (e.g., holdco vs opco)
        result = await self.db.execute(text('''
            SELECT c.ticker, di.name, COUNT(*) as cnt
            FROM debt_instruments di
            JOIN entities e ON e.id = di.issuer_id
            JOIN companies c ON c.id = e.company_id
            GROUP BY c.ticker, e.company_id, di.issuer_id, di.name, di.maturity_date
            HAVING COUNT(*) > 1
        '''))
        rows = result.fetchall()
        if rows:
            self.add_issue(
                severity='warning',
                category='consistency',
                table='debt_instruments',
                check_name='duplicate_instruments',
                description='Duplicate debt instruments (same issuer + name + maturity)',
                affected_count=sum(r[2] - 1 for r in rows),
                sample_ids=[f"{r[0]}: {r[1]} ({r[2]}x)" for r in rows[:5]]
            )

        # 3.6 Entity belongs to different company than its parent
        result = await self.db.execute(text('''
            SELECT c1.ticker as child_company, e.name as entity,
                   c2.ticker as parent_company, p.name as parent
            FROM entities e
            JOIN entities p ON p.id = e.parent_id
            JOIN companies c1 ON c1.id = e.company_id
            JOIN companies c2 ON c2.id = p.company_id
            WHERE e.company_id != p.company_id
        '''))
        rows = result.fetchall()
        if rows:
            self.add_issue(
                severity='critical',
                category='consistency',
                table='entities',
                check_name='cross_company_parent',
                description='Entity parent belongs to different company',
                affected_count=len(rows),
                sample_ids=[f"{r[0]}/{r[1]} -> {r[2]}/{r[3]}" for r in rows[:5]]
            )

    # =========================================================================
    # CATEGORY 4: BUSINESS LOGIC VALIDATION
    # =========================================================================

    async def check_business(self):
        """Check credit-specific business rules."""
        print("\n" + "=" * 70)
        print("CATEGORY 4: BUSINESS LOGIC VALIDATION")
        print("=" * 70)

        # 4.1 Unrestricted subsidiaries as guarantors (violates SEC Rule 3-16)
        result = await self.db.execute(text('''
            SELECT c.ticker, e.name
            FROM guarantees g
            JOIN entities e ON e.id = g.guarantor_id
            JOIN companies c ON c.id = e.company_id
            WHERE e.is_unrestricted = true
        '''))
        rows = result.fetchall()
        if rows:
            self.add_issue(
                severity='error',
                category='business',
                table='guarantees',
                check_name='unrestricted_guarantor',
                description='Unrestricted subsidiaries cannot be guarantors (SEC Rule 3-16)',
                affected_count=len(rows),
                sample_ids=[f"{r[0]}: {r[1]}" for r in rows[:5]]
            )

        # 4.2 is_root=true but has parent_id
        result = await self.db.execute(text('''
            SELECT c.ticker, e.name, p.name as parent
            FROM entities e
            JOIN entities p ON p.id = e.parent_id
            JOIN companies c ON c.id = e.company_id
            WHERE e.is_root = true AND e.parent_id IS NOT NULL
        '''))
        rows = result.fetchall()
        if rows:
            self.add_issue(
                severity='error',
                category='business',
                table='entities',
                check_name='root_with_parent',
                description='Root entities (is_root=true) should not have parent_id',
                affected_count=len(rows),
                sample_ids=[f"{r[0]}: {r[1]} -> {r[2]}" for r in rows[:5]]
            )

        # 4.3 Floating rate debt without benchmark
        result = await self.db.execute(text('''
            SELECT c.ticker, di.name, di.rate_type
            FROM debt_instruments di
            JOIN entities e ON e.id = di.issuer_id
            JOIN companies c ON c.id = e.company_id
            WHERE di.rate_type = 'floating'
            AND (di.benchmark IS NULL OR di.benchmark = '')
            AND di.is_active = true
        '''))
        rows = result.fetchall()
        if rows:
            self.add_issue(
                severity='warning',
                category='business',
                table='debt_instruments',
                check_name='floating_no_benchmark',
                description='Floating rate debt without benchmark (SOFR, etc.)',
                affected_count=len(rows),
                sample_ids=[f"{r[0]}: {r[1]}" for r in rows[:5]]
            )

        # 4.4 Fixed rate debt without interest rate
        result = await self.db.execute(text('''
            SELECT c.ticker, di.name
            FROM debt_instruments di
            JOIN entities e ON e.id = di.issuer_id
            JOIN companies c ON c.id = e.company_id
            WHERE di.rate_type = 'fixed'
            AND (di.interest_rate IS NULL OR di.interest_rate = 0)
            AND di.is_active = true
        '''))
        rows = result.fetchall()
        if rows:
            self.add_issue(
                severity='warning',
                category='business',
                table='debt_instruments',
                check_name='fixed_no_rate',
                description='Fixed rate debt without interest rate',
                affected_count=len(rows),
                sample_ids=[f"{r[0]}: {r[1]}" for r in rows[:5]]
            )

        # 4.5 Limited guarantee without limitation amount
        result = await self.db.execute(text('''
            SELECT c.ticker, di.name, e.name as guarantor
            FROM guarantees g
            JOIN entities e ON e.id = g.guarantor_id
            JOIN debt_instruments di ON di.id = g.debt_instrument_id
            JOIN companies c ON c.id = e.company_id
            WHERE g.guarantee_type = 'limited'
            AND (g.limitation_amount IS NULL OR g.limitation_amount = 0)
        '''))
        rows = result.fetchall()
        if rows:
            self.add_issue(
                severity='warning',
                category='business',
                table='guarantees',
                check_name='limited_no_amount',
                description='Limited guarantees without limitation amount',
                affected_count=len(rows),
                sample_ids=[f"{r[0]}: {r[1]} by {r[2]}" for r in rows[:5]]
            )

        # 4.6 EBITDA with zero revenue (extraction failure, not MSTR)
        result = await self.db.execute(text('''
            SELECT c.ticker, cf.fiscal_year, cf.fiscal_quarter,
                   cf.ebitda / 100.0 / 1e9 as ebitda_b
            FROM company_financials cf
            JOIN companies c ON c.id = cf.company_id
            WHERE (cf.revenue IS NULL OR cf.revenue = 0)
            AND cf.ebitda IS NOT NULL AND cf.ebitda > 100000000  -- >$1M
            AND c.ticker NOT IN ('MSTR')  -- Bitcoin company exception
        '''))
        rows = result.fetchall()
        if rows:
            self.add_issue(
                severity='error',
                category='business',
                table='company_financials',
                check_name='ebitda_no_revenue',
                description='EBITDA present but revenue is zero (extraction failure)',
                affected_count=len(rows),
                sample_ids=[f"{r[0]} Q{r[2]} {r[1]}: EBITDA ${r[3]:.1f}B" for r in rows[:5]]
            )

        # 4.7 Multiple root entities per company (should only be 1, except dual-listed)
        result = await self.db.execute(text('''
            SELECT c.ticker, COUNT(*) as root_count
            FROM entities e
            JOIN companies c ON c.id = e.company_id
            WHERE e.is_root = true
            GROUP BY c.id, c.ticker
            HAVING COUNT(*) > 1
        '''))
        rows = result.fetchall()
        if rows:
            # Filter out known dual-listed companies
            non_dual = [r for r in rows if r[0] not in ('ATUS', 'CCL')]
            if non_dual:
                self.add_issue(
                    severity='warning',
                    category='business',
                    table='entities',
                    check_name='multiple_roots',
                    description='Companies with multiple root entities (verify dual-listed)',
                    affected_count=len(non_dual),
                    sample_ids=[f"{r[0]}: {r[1]} roots" for r in non_dual[:5]]
                )

    # =========================================================================
    # CATEGORY 5: DATA COMPLETENESS
    # =========================================================================

    async def check_completeness(self):
        """Check critical field population rates."""
        print("\n" + "=" * 70)
        print("CATEGORY 5: DATA COMPLETENESS")
        print("=" * 70)

        # 5.1 Debt instruments missing maturity date
        result = await self.db.execute(text('''
            SELECT c.ticker, di.name
            FROM debt_instruments di
            JOIN entities e ON e.id = di.issuer_id
            JOIN companies c ON c.id = e.company_id
            WHERE di.maturity_date IS NULL
            AND di.is_active = true
            AND di.instrument_type NOT IN ('revolver', 'revolving_credit')
        '''))
        rows = result.fetchall()
        if rows:
            self.add_issue(
                severity='warning',
                category='completeness',
                table='debt_instruments',
                check_name='missing_maturity',
                description='Active non-revolver debt without maturity date',
                affected_count=len(rows),
                sample_ids=[f"{r[0]}: {r[1]}" for r in rows[:5]]
            )

        # 5.2 Debt instruments missing amounts
        result = await self.db.execute(text('''
            SELECT c.ticker, COUNT(*) as cnt
            FROM debt_instruments di
            JOIN entities e ON e.id = di.issuer_id
            JOIN companies c ON c.id = e.company_id
            WHERE di.outstanding IS NULL AND di.principal IS NULL
            AND di.is_active = true
            GROUP BY c.ticker
            HAVING COUNT(*) >= 3
        '''))
        rows = result.fetchall()
        if rows:
            self.add_issue(
                severity='info',
                category='completeness',
                table='debt_instruments',
                check_name='missing_amounts',
                description='Companies with 3+ active debt instruments missing amounts',
                affected_count=sum(r[1] for r in rows),
                sample_ids=[f"{r[0]}: {r[1]} instruments" for r in rows[:5]]
            )

        # 5.3 Companies without recent financials
        # Exclude: acquired companies, foreign private issuers (file 20-F instead of 10-Q)
        result = await self.db.execute(text('''
            SELECT c.ticker, c.name, MAX(cf.fiscal_year) as last_year
            FROM companies c
            LEFT JOIN company_financials cf ON cf.company_id = c.id
            WHERE (c.attributes->>'status') IS DISTINCT FROM 'acquired'
            AND (c.attributes->>'no_10q_expected')::boolean IS NOT TRUE
            GROUP BY c.id, c.ticker, c.name
            HAVING MAX(cf.fiscal_year) IS NULL OR MAX(cf.fiscal_year) < 2024
        '''))
        rows = result.fetchall()
        if rows:
            self.add_issue(
                severity='warning',
                category='completeness',
                table='company_financials',
                check_name='stale_financials',
                description='Companies without 2024+ financial data',
                affected_count=len(rows),
                sample_ids=[f"{r[0]}: last {r[2] or 'never'}" for r in rows[:5]]
            )

        # 5.4 Entity hierarchy completeness
        result = await self.db.execute(text('''
            SELECT c.ticker,
                   COUNT(*) as total_entities,
                   COUNT(*) FILTER (WHERE e.parent_id IS NOT NULL OR e.is_root = true) as with_parent
            FROM entities e
            JOIN companies c ON c.id = e.company_id
            GROUP BY c.id, c.ticker
            HAVING COUNT(*) FILTER (WHERE e.parent_id IS NOT NULL OR e.is_root = true) * 100.0 / COUNT(*) < 50
            AND COUNT(*) > 5
        '''))
        rows = result.fetchall()
        if rows:
            self.add_issue(
                severity='info',
                category='completeness',
                table='entities',
                check_name='incomplete_hierarchy',
                description='Companies with <50% entity hierarchy populated (>5 entities)',
                affected_count=len(rows),
                sample_ids=[f"{r[0]}: {r[2]}/{r[1]} have parent" for r in rows[:5]]
            )

        # 5.5 Bond pricing staleness
        result = await self.db.execute(text('''
            SELECT c.ticker, di.name, bp.fetched_at
            FROM bond_pricing bp
            JOIN debt_instruments di ON di.id = bp.debt_instrument_id
            JOIN entities e ON e.id = di.issuer_id
            JOIN companies c ON c.id = e.company_id
            WHERE bp.fetched_at < NOW() - INTERVAL '90 days'
        '''))
        rows = result.fetchall()
        if rows:
            self.add_issue(
                severity='info',
                category='completeness',
                table='bond_pricing',
                check_name='stale_pricing',
                description='Bond pricing >90 days old',
                affected_count=len(rows),
                sample_ids=[f"{r[0]}: {r[1]}" for r in rows[:5]]
            )

        # 5.6 Debt instruments missing document links (indenture/credit agreement)
        # Notes and bonds should have indentures, credit facilities should have credit agreements
        result = await self.db.execute(text('''
            SELECT c.ticker, di.name, di.instrument_type
            FROM debt_instruments di
            JOIN entities e ON e.id = di.issuer_id
            JOIN companies c ON c.id = e.company_id
            LEFT JOIN debt_instrument_documents did ON did.debt_instrument_id = di.id
            WHERE di.is_active = true
            AND did.id IS NULL
            AND di.instrument_type IN (
                'senior_notes', 'senior_secured_notes', 'senior_unsecured_notes',
                'convertible_notes', 'subordinated_notes', 'debentures',
                'revolver', 'term_loan_a', 'term_loan_b', 'term_loan'
            )
        '''))
        rows = result.fetchall()
        if rows:
            self.add_issue(
                severity='warning',
                category='completeness',
                table='debt_instrument_documents',
                check_name='missing_document_link',
                description='Debt instruments without indenture/credit agreement link',
                affected_count=len(rows),
                sample_ids=[f"{r[0]}: {r[1]}" for r in rows[:5]]
            )

        # 5.7 Document coverage by principal (warning if <70% coverage)
        result = await self.db.execute(text('''
            SELECT
                SUM(CASE WHEN did.id IS NOT NULL THEN di.principal ELSE 0 END) as covered,
                SUM(di.principal) as total
            FROM debt_instruments di
            LEFT JOIN debt_instrument_documents did ON did.debt_instrument_id = di.id
            WHERE di.is_active = true
            AND di.principal IS NOT NULL
        '''))
        row = result.fetchone()
        if row and row.total and row.total > 0:
            coverage_pct = (row.covered or 0) / row.total * 100
            if coverage_pct < 70:
                self.add_issue(
                    severity='warning',
                    category='completeness',
                    table='debt_instrument_documents',
                    check_name='low_document_coverage',
                    description=f'Document coverage by principal is {coverage_pct:.1f}% (target: >70%)',
                    affected_count=1,
                    sample_ids=[f"Covered: ${(row.covered or 0)/100/1e9:.1f}B of ${row.total/100/1e9:.1f}B"]
                )

    # =========================================================================
    # RUN ALL CHECKS
    # =========================================================================

    async def run_all(self, categories: list[str] = None):
        """Run all QC checks."""
        all_categories = {
            'integrity': self.check_integrity,
            'impossible': self.check_impossible,
            'consistency': self.check_consistency,
            'business': self.check_business,
            'completeness': self.check_completeness,
        }

        if categories:
            checks = {k: v for k, v in all_categories.items() if k in categories}
        else:
            checks = all_categories

        for name, check_func in checks.items():
            await check_func()

        return self.summarize()

    def summarize(self) -> dict:
        """Print summary and return results."""
        print("\n" + "=" * 70)
        print("QC MASTER SUMMARY")
        print("=" * 70)

        by_severity = defaultdict(list)
        for issue in self.issues:
            by_severity[issue.severity].append(issue)

        print(f"\n  Critical: {len(by_severity['critical'])}")
        print(f"  Errors:   {len(by_severity['error'])}")
        print(f"  Warnings: {len(by_severity['warning'])}")
        print(f"  Info:     {len(by_severity['info'])}")

        if by_severity['critical']:
            print("\n  CRITICAL ISSUES (must fix):")
            for issue in by_severity['critical']:
                print(f"    - {issue.check_name}: {issue.description} ({issue.affected_count})")

        if by_severity['error']:
            print("\n  ERRORS (should fix):")
            for issue in by_severity['error']:
                print(f"    - {issue.check_name}: {issue.description} ({issue.affected_count})")

        total_critical = len(by_severity['critical'])
        total_errors = len(by_severity['error'])

        if total_critical > 0:
            print("\n[FAIL] CRITICAL ISSUES FOUND")
            return {'status': 'fail', 'critical': total_critical, 'errors': total_errors}
        elif total_errors > 0:
            print("\n[WARN] ERRORS FOUND - Review required")
            return {'status': 'warn', 'critical': 0, 'errors': total_errors}
        else:
            print("\n[PASS] All critical checks passed")
            return {'status': 'pass', 'critical': 0, 'errors': 0}


async def main():
    parser = argparse.ArgumentParser(description="Master Data Quality Control Suite")
    parser.add_argument("--verbose", "-v", action="store_true", help="Show all details")
    parser.add_argument("--category", "-c", type=str, help="Run specific category (integrity, impossible, consistency, business, completeness)")
    parser.add_argument("--fix", action="store_true", help="Auto-fix where safe")
    args = parser.parse_args()

    database_url = os.getenv('DATABASE_URL')
    if not database_url:
        print("ERROR: DATABASE_URL not set")
        sys.exit(1)

    # Convert to async URL
    if 'postgresql://' in database_url and '+asyncpg' not in database_url:
        database_url = database_url.replace('postgresql://', 'postgresql+asyncpg://', 1)

    engine = create_async_engine(database_url, echo=False)
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    print("=" * 70)
    print("MASTER DATA QUALITY CONTROL SUITE")
    print("=" * 70)
    print("ACCURACY IS THE NUMBER ONE PRODUCT")
    print(f"\nRun time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    async with async_session() as db:
        qc = QCMaster(db, verbose=args.verbose)
        categories = [args.category] if args.category else None
        result = await qc.run_all(categories)

    await engine.dispose()

    if result['status'] == 'fail':
        sys.exit(2)
    elif result['status'] == 'warn':
        sys.exit(1)
    else:
        sys.exit(0)


if __name__ == "__main__":
    asyncio.run(main())
