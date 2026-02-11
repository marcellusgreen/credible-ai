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
import sys
from dataclasses import dataclass, field
from datetime import datetime
from collections import defaultdict

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from script_utils import (
    get_db_session,
    print_header,
    run_async,
)


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
    def __init__(self, db: AsyncSession, verbose: bool = False, fix: bool = False):
        self.db = db
        self.verbose = verbose
        self.fix = fix
        self.issues: list[Issue] = []
        self.fixes_applied: list[str] = []

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

        # 3.5 Duplicate debt instruments (same issuer, name, maturity, AND same CUSIP)
        # Note: Different CUSIPs with same name/maturity are legitimate (144A vs registered tranches)
        # Only flag true duplicates where CUSIP also matches
        result = await self.db.execute(text('''
            SELECT c.ticker, di.name, di.cusip, COUNT(*) as cnt
            FROM debt_instruments di
            JOIN entities e ON e.id = di.issuer_id
            JOIN companies c ON c.id = e.company_id
            WHERE di.is_active = true
            GROUP BY c.ticker, e.company_id, di.issuer_id, di.name, di.maturity_date, di.cusip
            HAVING COUNT(*) > 1
        '''))
        rows = result.fetchall()
        if rows:
            self.add_issue(
                severity='warning',
                category='consistency',
                table='debt_instruments',
                check_name='duplicate_instruments',
                description='Duplicate debt instruments (same issuer + name + maturity + CUSIP)',
                affected_count=sum(r[3] - 1 for r in rows),
                sample_ids=[f"{r[0]}: {r[1]} ({r[3]}x)" for r in rows[:5]]
            )

        # 3.6 Snapshots missing debt data
        result = await self.db.execute(text('''
            SELECT c.ticker
            FROM company_snapshots cs
            JOIN companies c ON c.id = cs.company_id
            WHERE cs.debt_snapshot IS NULL OR jsonb_array_length(cs.debt_snapshot) = 0
        '''))
        rows = result.fetchall()
        if rows:
            self.add_issue(
                severity='warning',
                category='consistency',
                table='company_snapshots',
                check_name='snapshot_missing_debt',
                description='Company snapshots with NULL or empty debt_snapshot',
                affected_count=len(rows),
                sample_ids=[r[0] for r in rows[:5]]
            )

        # 3.7 Snapshots missing entities data
        result = await self.db.execute(text('''
            SELECT c.ticker
            FROM company_snapshots cs
            JOIN companies c ON c.id = cs.company_id
            WHERE cs.entities_snapshot IS NULL OR jsonb_array_length(cs.entities_snapshot) = 0
        '''))
        rows = result.fetchall()
        if rows:
            self.add_issue(
                severity='warning',
                category='consistency',
                table='company_snapshots',
                check_name='snapshot_missing_entities',
                description='Company snapshots with NULL or empty entities_snapshot',
                affected_count=len(rows),
                sample_ids=[r[0] for r in rows[:5]]
            )

        # 3.8 Entity belongs to different company than its parent
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

        # 4.6 EBITDA with zero revenue (extraction failure, not MSTR or financial institutions)
        # Banks/financial institutions use Net Interest Income, not traditional revenue
        result = await self.db.execute(text('''
            SELECT c.ticker, cf.fiscal_year, cf.fiscal_quarter,
                   cf.ebitda / 100.0 / 1e9 as ebitda_b
            FROM company_financials cf
            JOIN companies c ON c.id = cf.company_id
            WHERE (cf.revenue IS NULL OR cf.revenue = 0)
            AND cf.ebitda IS NOT NULL AND cf.ebitda > 100000000  -- >$1M
            AND c.ticker NOT IN ('MSTR')  -- Bitcoin company exception
            AND c.is_financial_institution = false  -- Banks use NII, not revenue
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

        # 5.6 Companies without any snapshots
        result = await self.db.execute(text('''
            SELECT c.ticker
            FROM companies c
            LEFT JOIN company_snapshots cs ON cs.company_id = c.id
            GROUP BY c.id, c.ticker
            HAVING COUNT(cs.id) = 0
        '''))
        rows = result.fetchall()
        if rows:
            self.add_issue(
                severity='info',
                category='completeness',
                table='company_snapshots',
                check_name='company_no_snapshots',
                description='Companies without any snapshots (changes endpoint will not work)',
                affected_count=len(rows),
                sample_ids=[r[0] for r in rows[:5]]
            )

        # 5.7 Bonds with pricing but no pricing history
        result = await self.db.execute(text('''
            SELECT c.ticker, COUNT(*) as cnt
            FROM debt_instruments di
            JOIN entities e ON e.id = di.issuer_id
            JOIN companies c ON c.id = e.company_id
            JOIN bond_pricing bp ON bp.debt_instrument_id = di.id
            LEFT JOIN bond_pricing_history bph ON bph.debt_instrument_id = di.id
            WHERE bph.id IS NULL
            GROUP BY c.ticker
        '''))
        rows = result.fetchall()
        if rows:
            total = sum(r[1] for r in rows)
            self.add_issue(
                severity='info',
                category='completeness',
                table='bond_pricing_history',
                check_name='missing_pricing_history',
                description='Bonds with current pricing but no historical pricing snapshots',
                affected_count=total,
                sample_ids=[f"{r[0]}: {r[1]} bonds" for r in rows[:5]]
            )

        # 5.8 Companies without metrics (needed for export)
        result = await self.db.execute(text('''
            SELECT c.ticker
            FROM companies c
            LEFT JOIN company_metrics cm ON cm.company_id = c.id
            WHERE cm.id IS NULL
        '''))
        rows = result.fetchall()
        if rows:
            self.add_issue(
                severity='warning',
                category='completeness',
                table='company_metrics',
                check_name='missing_metrics_for_export',
                description='Companies without computed metrics (export/screening will lack data)',
                affected_count=len(rows),
                sample_ids=[r[0] for r in rows[:5]]
            )

        # 5.9 Debt instruments missing document links (indenture/credit agreement)
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
    # CATEGORY 6: COVENANT DATA QUALITY
    # =========================================================================

    async def check_covenants(self):
        """Check covenant data quality and consistency."""
        print("\n" + "=" * 70)
        print("CATEGORY 6: COVENANT DATA QUALITY")
        print("=" * 70)

        # 6.1 Orphan covenants (reference non-existent company)
        result = await self.db.execute(text('''
            SELECT cov.id, cov.covenant_name, cov.company_id
            FROM covenants cov
            LEFT JOIN companies c ON c.id = cov.company_id
            WHERE c.id IS NULL
        '''))
        rows = result.fetchall()
        if rows:
            self.add_issue(
                severity='critical',
                category='covenants',
                table='covenants',
                check_name='orphan_covenant_company',
                description='Covenants reference non-existent companies',
                affected_count=len(rows),
                sample_ids=[str(r[0]) for r in rows[:5]]
            )

        # 6.2 Covenants with invalid debt_instrument_id
        result = await self.db.execute(text('''
            SELECT cov.id, cov.covenant_name, cov.debt_instrument_id
            FROM covenants cov
            LEFT JOIN debt_instruments di ON di.id = cov.debt_instrument_id
            WHERE cov.debt_instrument_id IS NOT NULL AND di.id IS NULL
        '''))
        rows = result.fetchall()
        if rows:
            self.add_issue(
                severity='error',
                category='covenants',
                table='covenants',
                check_name='orphan_covenant_instrument',
                description='Covenants reference non-existent debt instruments',
                affected_count=len(rows),
                sample_ids=[str(r[0]) for r in rows[:5]]
            )

        # 6.3 Financial covenants without test_metric
        result = await self.db.execute(text('''
            SELECT c.ticker, cov.covenant_name
            FROM covenants cov
            JOIN companies c ON c.id = cov.company_id
            WHERE cov.covenant_type = 'financial'
            AND (cov.test_metric IS NULL OR cov.test_metric = '')
            AND cov.covenant_name NOT ILIKE '%covenant-lite%'
        '''))
        rows = result.fetchall()
        if rows:
            self.add_issue(
                severity='warning',
                category='covenants',
                table='covenants',
                check_name='financial_no_metric',
                description='Financial covenants without test_metric specified',
                affected_count=len(rows),
                sample_ids=[f"{r[0]}: {r[1]}" for r in rows[:5]]
            )

        # 6.4 Leverage ratio thresholds outside reasonable range (0.5 - 20x)
        result = await self.db.execute(text('''
            SELECT c.ticker, cov.covenant_name, cov.threshold_value
            FROM covenants cov
            JOIN companies c ON c.id = cov.company_id
            WHERE cov.test_metric IN ('leverage_ratio', 'first_lien_leverage', 'secured_leverage', 'net_leverage_ratio')
            AND cov.threshold_value IS NOT NULL
            AND (cov.threshold_value < 0.5 OR cov.threshold_value > 20)
        '''))
        rows = result.fetchall()
        if rows:
            self.add_issue(
                severity='error',
                category='covenants',
                table='covenants',
                check_name='leverage_threshold_outlier',
                description='Leverage ratio thresholds outside reasonable range (0.5-20x)',
                affected_count=len(rows),
                sample_ids=[f"{r[0]}: {r[1]} = {r[2]}x" for r in rows[:5]]
            )

        # 6.5 Coverage ratio thresholds outside reasonable range (0.5 - 10x)
        result = await self.db.execute(text('''
            SELECT c.ticker, cov.covenant_name, cov.threshold_value
            FROM covenants cov
            JOIN companies c ON c.id = cov.company_id
            WHERE cov.test_metric IN ('interest_coverage', 'fixed_charge_coverage')
            AND cov.threshold_value IS NOT NULL
            AND (cov.threshold_value < 0.5 OR cov.threshold_value > 10)
        '''))
        rows = result.fetchall()
        if rows:
            self.add_issue(
                severity='warning',
                category='covenants',
                table='covenants',
                check_name='coverage_threshold_outlier',
                description='Coverage ratio thresholds outside typical range (0.5-10x)',
                affected_count=len(rows),
                sample_ids=[f"{r[0]}: {r[1]} = {r[2]}x" for r in rows[:5]]
            )

        # 6.6 Put price percentages outside reasonable range (95-115%)
        result = await self.db.execute(text('''
            SELECT c.ticker, cov.covenant_name, cov.put_price_pct
            FROM covenants cov
            JOIN companies c ON c.id = cov.company_id
            WHERE cov.put_price_pct IS NOT NULL
            AND (cov.put_price_pct < 95 OR cov.put_price_pct > 115)
        '''))
        rows = result.fetchall()
        if rows:
            self.add_issue(
                severity='warning',
                category='covenants',
                table='covenants',
                check_name='put_price_outlier',
                description='Put price percentages outside typical range (95-115%)',
                affected_count=len(rows),
                sample_ids=[f"{r[0]}: {r[1]} = {r[2]}%" for r in rows[:5]]
            )

        # 6.7 Invalid covenant_type
        result = await self.db.execute(text('''
            SELECT c.ticker, cov.covenant_name, cov.covenant_type
            FROM covenants cov
            JOIN companies c ON c.id = cov.company_id
            WHERE cov.covenant_type NOT IN ('financial', 'negative', 'incurrence', 'protective')
        '''))
        rows = result.fetchall()
        if rows:
            self.add_issue(
                severity='error',
                category='covenants',
                table='covenants',
                check_name='invalid_covenant_type',
                description='Covenants with invalid covenant_type',
                affected_count=len(rows),
                sample_ids=[f"{r[0]}: {r[1]} type='{r[2]}'" for r in rows[:5]]
            )

        # 6.8 Invalid threshold_type
        result = await self.db.execute(text('''
            SELECT c.ticker, cov.covenant_name, cov.threshold_type
            FROM covenants cov
            JOIN companies c ON c.id = cov.company_id
            WHERE cov.threshold_type IS NOT NULL
            AND cov.threshold_type NOT IN ('maximum', 'minimum')
        '''))
        rows = result.fetchall()
        if rows:
            self.add_issue(
                severity='error',
                category='covenants',
                table='covenants',
                check_name='invalid_threshold_type',
                description='Covenants with invalid threshold_type (should be maximum/minimum)',
                affected_count=len(rows),
                sample_ids=[f"{r[0]}: {r[1]} type='{r[2]}'" for r in rows[:5]]
            )

        # 6.9 Leverage covenants with threshold_type='minimum' (should be maximum)
        result = await self.db.execute(text('''
            SELECT c.ticker, cov.covenant_name, cov.threshold_value
            FROM covenants cov
            JOIN companies c ON c.id = cov.company_id
            WHERE cov.test_metric IN ('leverage_ratio', 'first_lien_leverage', 'secured_leverage', 'net_leverage_ratio')
            AND cov.threshold_type = 'minimum'
        '''))
        rows = result.fetchall()
        if rows:
            self.add_issue(
                severity='error',
                category='covenants',
                table='covenants',
                check_name='leverage_wrong_direction',
                description='Leverage covenants with threshold_type=minimum (should be maximum)',
                affected_count=len(rows),
                sample_ids=[f"{r[0]}: {r[1]} min={r[2]}x" for r in rows[:5]]
            )

        # 6.10 Coverage covenants with threshold_type='maximum' (should be minimum)
        result = await self.db.execute(text('''
            SELECT c.ticker, cov.covenant_name, cov.threshold_value
            FROM covenants cov
            JOIN companies c ON c.id = cov.company_id
            WHERE cov.test_metric IN ('interest_coverage', 'fixed_charge_coverage')
            AND cov.threshold_type = 'maximum'
        '''))
        rows = result.fetchall()
        if rows:
            self.add_issue(
                severity='error',
                category='covenants',
                table='covenants',
                check_name='coverage_wrong_direction',
                description='Coverage covenants with threshold_type=maximum (should be minimum)',
                affected_count=len(rows),
                sample_ids=[f"{r[0]}: {r[1]} max={r[2]}x" for r in rows[:5]]
            )

        # 6.11 Duplicate covenants (same company + name + instrument)
        result = await self.db.execute(text('''
            SELECT c.ticker, cov.covenant_name, COUNT(*) as cnt
            FROM covenants cov
            JOIN companies c ON c.id = cov.company_id
            GROUP BY c.ticker, cov.company_id, cov.covenant_name, cov.debt_instrument_id
            HAVING COUNT(*) > 1
        '''))
        rows = result.fetchall()
        if rows:
            self.add_issue(
                severity='warning',
                category='covenants',
                table='covenants',
                check_name='duplicate_covenants',
                description='Duplicate covenants (same company + name + instrument)',
                affected_count=sum(r[2] - 1 for r in rows),
                sample_ids=[f"{r[0]}: {r[1]} ({r[2]}x)" for r in rows[:5]]
            )

        # 6.12 Low confidence covenants (< 0.7)
        result = await self.db.execute(text('''
            SELECT c.ticker, cov.covenant_name, cov.extraction_confidence
            FROM covenants cov
            JOIN companies c ON c.id = cov.company_id
            WHERE cov.extraction_confidence < 0.7
        '''))
        rows = result.fetchall()
        if rows:
            self.add_issue(
                severity='info',
                category='covenants',
                table='covenants',
                check_name='low_confidence_covenants',
                description='Covenants with extraction_confidence < 0.7 (review recommended)',
                affected_count=len(rows),
                sample_ids=[f"{r[0]}: {r[1]} ({r[2]:.0%})" for r in rows[:5]]
            )

        # 6.13 Covenants without source_document_id
        result = await self.db.execute(text('''
            SELECT c.ticker, COUNT(*) as cnt
            FROM covenants cov
            JOIN companies c ON c.id = cov.company_id
            WHERE cov.source_document_id IS NULL
            GROUP BY c.ticker
        '''))
        rows = result.fetchall()
        if rows:
            total = sum(r[1] for r in rows)
            self.add_issue(
                severity='info',
                category='covenants',
                table='covenants',
                check_name='missing_source_document',
                description='Covenants without source_document_id linkage',
                affected_count=total,
                sample_ids=[f"{r[0]}: {r[1]} covenants" for r in rows[:5]]
            )

        # 6.14 Companies with covenants but no financial covenants (unusual)
        result = await self.db.execute(text('''
            SELECT c.ticker, COUNT(*) as total,
                   COUNT(*) FILTER (WHERE cov.covenant_type = 'financial') as financial
            FROM covenants cov
            JOIN companies c ON c.id = cov.company_id
            GROUP BY c.ticker, cov.company_id
            HAVING COUNT(*) >= 3
            AND COUNT(*) FILTER (WHERE cov.covenant_type = 'financial') = 0
        '''))
        rows = result.fetchall()
        if rows:
            self.add_issue(
                severity='info',
                category='covenants',
                table='covenants',
                check_name='no_financial_covenants',
                description='Companies with 3+ covenants but no financial covenants (may be covenant-lite)',
                affected_count=len(rows),
                sample_ids=[f"{r[0]}: {r[1]} covenants, 0 financial" for r in rows[:5]]
            )

        # 6.15 Covenant instrument linkage rate
        result = await self.db.execute(text('''
            SELECT
                COUNT(*) as total,
                COUNT(*) FILTER (WHERE debt_instrument_id IS NOT NULL) as linked
            FROM covenants
        '''))
        row = result.fetchone()
        if row and row.total > 0:
            linkage_pct = row.linked / row.total * 100
            if linkage_pct < 80:
                self.add_issue(
                    severity='warning',
                    category='covenants',
                    table='covenants',
                    check_name='low_instrument_linkage',
                    description=f'Covenant-to-instrument linkage is {linkage_pct:.1f}% (target: >80%)',
                    affected_count=row.total - row.linked,
                    sample_ids=[f"Linked: {row.linked}/{row.total}"]
                )

    # =========================================================================
    # COVENANT FIXES
    # =========================================================================

    async def fix_covenants(self):
        """Apply fixes for covenant data quality issues."""
        if not self.fix:
            return

        print("\n" + "=" * 70)
        print("APPLYING COVENANT FIXES")
        print("=" * 70)

        # Fix 1: Misclassified test_metrics (debt-to-capital ratios labeled as leverage)
        # These are percentage-based covenants, not leverage multiples
        misclassified_fixes = [
            # AEP: Debt-to-Capital Ratio - 400 means 0.40 (40%)
            ('88d12273-5344-4cf2-a482-5620767609a6', 'AEP', 'Debt-to-Capital Ratio',
             'debt_to_capitalization', 0.40, 'Debt-to-capital (40%), not leverage multiple'),
            # BA: Maximum Leverage Ratio - 60 means 60% debt-to-capital
            ('d4ecf34c-da53-47dd-92a4-9912e14d6b0d', 'BA', 'Maximum Leverage Ratio',
             'debt_to_capitalization', 0.60, '60% debt-to-capital, not leverage multiple'),
            # VNO: Three debt-to-capitalization ratios
            ('2bb5231c-7e38-4c54-ad17-cec23324347c', 'VNO', 'Ratio of Total Outstanding Indebtedness to Capitalization Value',
             'debt_to_capitalization', 0.60, '60% debt-to-capitalization'),
            ('49fae537-09e9-49d1-b4aa-5555b0d564b4', 'VNO', 'Ratio of Secured Indebtedness to Capitalization Value',
             'debt_to_capitalization', 0.50, '50% secured debt-to-capitalization'),
            ('de035abf-117f-4031-b42a-ca2484869cbd', 'VNO', 'Ratio of Unsecured Indebtedness to Capitalization Value of Unencumbered Assets',
             'debt_to_capitalization', 0.60, '60% debt-to-capitalization'),
        ]

        for cov_id, ticker, name, new_metric, new_threshold, reason in misclassified_fixes:
            result = await self.db.execute(text(
                "SELECT id FROM covenants WHERE id = :id"
            ), {'id': cov_id})
            if result.fetchone():
                await self.db.execute(text("""
                    UPDATE covenants
                    SET test_metric = :metric, threshold_value = :threshold
                    WHERE id = :id
                """), {'id': cov_id, 'metric': new_metric, 'threshold': new_threshold})
                self.fixes_applied.append(f"[{ticker}] {name}: {reason}")
                if self.verbose:
                    print(f"  Fixed [{ticker}] {name}: test_metric -> {new_metric}, threshold -> {new_threshold}")

        # Fix 2: CAT Minimum Consolidated Net Worth - dollar amount, not a ratio
        # Set test_metric to NULL (not a ratio metric) but keep threshold as dollar amount
        result = await self.db.execute(text(
            "SELECT id FROM covenants WHERE id = 'f374062e-8df4-4afe-a0fc-435dddeaf40b'"
        ))
        if result.fetchone():
            await self.db.execute(text("""
                UPDATE covenants
                SET test_metric = NULL
                WHERE id = 'f374062e-8df4-4afe-a0fc-435dddeaf40b'
            """))
            self.fixes_applied.append("[CAT] Minimum Consolidated Net Worth: test_metric -> NULL (dollar amount, not ratio)")
            if self.verbose:
                print("  Fixed [CAT] Minimum Consolidated Net Worth: test_metric -> NULL")

        # Fix 3: FUN - 0.30 is already correct as decimal, just fix metric type
        result = await self.db.execute(text(
            "SELECT id FROM covenants WHERE id = '52a93d80-9511-4f6b-8c05-e092295e52b0'"
        ))
        if result.fetchone():
            await self.db.execute(text("""
                UPDATE covenants
                SET test_metric = 'debt_to_capitalization'
                WHERE id = '52a93d80-9511-4f6b-8c05-e092295e52b0'
            """))
            self.fixes_applied.append("[FUN] Restricted Subsidiary Indebtedness: test_metric -> debt_to_capitalization")
            if self.verbose:
                print("  Fixed [FUN] Restricted Subsidiary Indebtedness: test_metric -> debt_to_capitalization")

        # Fix 4: Invalid threshold_type values (e.g., "N/A" should be NULL)
        result = await self.db.execute(text("""
            UPDATE covenants
            SET threshold_type = NULL
            WHERE threshold_type IS NOT NULL
            AND threshold_type NOT IN ('maximum', 'minimum')
            RETURNING id
        """))
        fixed_ids = result.fetchall()
        if fixed_ids:
            self.fixes_applied.append(f"Cleared {len(fixed_ids)} invalid threshold_type values (e.g., 'N/A' -> NULL)")
            if self.verbose:
                print(f"  Fixed {len(fixed_ids)} covenants with invalid threshold_type")

        # Fix 5: Leverage covenants with threshold_type='minimum' (should be 'maximum')
        # But skip CAT since we already set its test_metric to NULL
        result = await self.db.execute(text("""
            UPDATE covenants
            SET threshold_type = 'maximum'
            WHERE test_metric IN ('leverage_ratio', 'first_lien_leverage', 'secured_leverage', 'net_leverage_ratio')
            AND threshold_type = 'minimum'
            RETURNING id
        """))
        fixed_ids = result.fetchall()
        if fixed_ids:
            self.fixes_applied.append(f"Fixed {len(fixed_ids)} leverage covenants: threshold_type 'minimum' -> 'maximum'")
            if self.verbose:
                print(f"  Fixed {len(fixed_ids)} leverage covenants with wrong direction")

        # Fix 6: Coverage covenants with threshold_type='maximum' (should be 'minimum')
        result = await self.db.execute(text("""
            UPDATE covenants
            SET threshold_type = 'minimum'
            WHERE test_metric IN ('interest_coverage', 'fixed_charge_coverage')
            AND threshold_type = 'maximum'
            RETURNING id
        """))
        fixed_ids = result.fetchall()
        if fixed_ids:
            self.fixes_applied.append(f"Fixed {len(fixed_ids)} coverage covenants: threshold_type 'maximum' -> 'minimum'")
            if self.verbose:
                print(f"  Fixed {len(fixed_ids)} coverage covenants with wrong direction")

        # Fix 7: Remove duplicate covenants (keep oldest)
        result = await self.db.execute(text("""
            DELETE FROM covenants
            WHERE id IN (
                SELECT id FROM (
                    SELECT id,
                           ROW_NUMBER() OVER (
                               PARTITION BY company_id, covenant_name, COALESCE(debt_instrument_id::text, '')
                               ORDER BY created_at ASC
                           ) as rn
                    FROM covenants
                ) ranked
                WHERE rn > 1
            )
            RETURNING id
        """))
        deleted_ids = result.fetchall()
        if deleted_ids:
            self.fixes_applied.append(f"Deleted {len(deleted_ids)} duplicate covenants (kept oldest)")
            if self.verbose:
                print(f"  Deleted {len(deleted_ids)} duplicate covenants")

        await self.db.commit()

        print(f"\n  Applied {len(self.fixes_applied)} fixes")
        for fix_desc in self.fixes_applied:
            print(f"    - {fix_desc}")

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
            'covenants': self.check_covenants,
        }

        if categories:
            checks = {k: v for k, v in all_categories.items() if k in categories}
        else:
            checks = all_categories

        for name, check_func in checks.items():
            await check_func()

        # Apply fixes if requested
        if self.fix:
            if not categories or 'covenants' in categories:
                await self.fix_covenants()

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
    parser.add_argument("--category", "-c", type=str, help="Run specific category (integrity, impossible, consistency, business, completeness, covenants)")
    parser.add_argument("--fix", action="store_true", help="Auto-fix where safe")
    args = parser.parse_args()

    print_header("MASTER DATA QUALITY CONTROL SUITE")
    print("ACCURACY IS THE NUMBER ONE PRODUCT")
    print(f"\nRun time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    async with get_db_session() as db:
        qc = QCMaster(db, verbose=args.verbose, fix=args.fix)
        categories = [args.category] if args.category else None
        result = await qc.run_all(categories)

    if result['status'] == 'fail':
        sys.exit(2)
    elif result['status'] == 'warn':
        sys.exit(1)
    else:
        sys.exit(0)


if __name__ == "__main__":
    run_async(main())
