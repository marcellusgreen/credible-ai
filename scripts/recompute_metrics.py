#!/usr/bin/env python3
"""
Recompute CompanyMetrics for all companies in the database.

This script recalculates derived metrics (maturity profile, flags, etc.)
from existing data without re-running extraction.

Usage:
    python scripts/recompute_metrics.py                    # All companies
    python scripts/recompute_metrics.py --ticker AAPL      # Single company
    python scripts/recompute_metrics.py --dry-run          # Preview without saving
"""

import argparse
import asyncio
import os
import sys
from datetime import date, timedelta
from decimal import Decimal

from dotenv import load_dotenv

# Add parent dir to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

load_dotenv()

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker

from app.core.config import get_settings
from app.models import Company, CompanyMetrics, CompanyFinancials, DebtInstrument, Entity

settings = get_settings()


async def get_latest_financials(db: AsyncSession, company_id) -> CompanyFinancials | None:
    """Get the most recent financial data for a company."""
    result = await db.execute(
        select(CompanyFinancials)
        .where(CompanyFinancials.company_id == company_id)
        .order_by(
            CompanyFinancials.fiscal_year.desc(),
            CompanyFinancials.fiscal_quarter.desc(),
        )
        .limit(1)
    )
    return result.scalar_one_or_none()


async def recompute_metrics_for_company(
    db: AsyncSession,
    company: Company,
    dry_run: bool = False,
) -> dict:
    """Recompute metrics for a single company."""

    ticker = company.ticker
    company_id = company.id

    # Get all entities for this company
    result = await db.execute(
        select(Entity).where(Entity.company_id == company_id)
    )
    entities = list(result.scalars().all())

    # Build entity lookup by ID
    entity_by_id = {e.id: e for e in entities}

    # Get all debt instruments
    result = await db.execute(
        select(DebtInstrument).where(
            DebtInstrument.company_id == company_id,
            DebtInstrument.is_active == True,
        )
    )
    debt_instruments = list(result.scalars().all())

    # Calculate totals
    total_debt = sum(d.outstanding or 0 for d in debt_instruments)
    secured_debt = sum(
        d.outstanding or 0 for d in debt_instruments
        if d.seniority == "senior_secured"
    )
    unsecured_debt = total_debt - secured_debt

    # Nearest maturity
    nearest_maturity = min(
        (d.maturity_date for d in debt_instruments if d.maturity_date),
        default=None,
    )

    # Maturity profile
    today = date.today()

    debt_due_1yr = sum(
        d.outstanding or 0 for d in debt_instruments
        if d.maturity_date and d.maturity_date <= today + timedelta(days=365)
    )
    debt_due_2yr = sum(
        d.outstanding or 0 for d in debt_instruments
        if d.maturity_date and today + timedelta(days=365) < d.maturity_date <= today + timedelta(days=730)
    )
    debt_due_3yr = sum(
        d.outstanding or 0 for d in debt_instruments
        if d.maturity_date and today + timedelta(days=730) < d.maturity_date <= today + timedelta(days=1095)
    )

    has_near_term_maturity = (debt_due_1yr > 0) or (debt_due_2yr > 0)

    # Weighted average maturity
    if total_debt > 0:
        weighted_avg_maturity = sum(
            (d.outstanding or 0) * max(0, (d.maturity_date - today).days / 365.0)
            for d in debt_instruments
            if d.maturity_date and d.outstanding
        ) / total_debt
        # Cap at 999.9 years (Numeric(4,1) max value)
        if weighted_avg_maturity > 999.9:
            weighted_avg_maturity = 999.9
    else:
        weighted_avg_maturity = None

    # Compute flags
    has_holdco_debt = any(
        entity_by_id.get(d.issuer_id) and entity_by_id[d.issuer_id].structure_tier == 1
        for d in debt_instruments
    )
    has_opco_debt = any(
        entity_by_id.get(d.issuer_id) and entity_by_id[d.issuer_id].structure_tier and entity_by_id[d.issuer_id].structure_tier >= 3
        for d in debt_instruments
    )
    has_structural_sub = has_holdco_debt and has_opco_debt
    has_unrestricted_subs = any(e.is_unrestricted for e in entities)
    has_floating_rate = any(d.rate_type == "floating" for d in debt_instruments)

    # Subordination score
    if has_structural_sub:
        subordination_risk = "moderate"
        subordination_score = Decimal("5.0")
    elif has_holdco_debt:
        subordination_risk = "low"
        subordination_score = Decimal("2.0")
    else:
        subordination_risk = "low"
        subordination_score = Decimal("1.0")

    # Leverage ratios from financials
    leverage_ratio = None
    net_leverage_ratio = None
    interest_coverage = None
    secured_leverage = None
    net_debt = None

    financials = await get_latest_financials(db, company_id)
    if financials:
        # Use quarterly EBITDA annualized (x4) for ratios
        # Priority order for EBITDA:
        # 1. Directly extracted EBITDA
        # 2. Computed: Operating Income + Depreciation & Amortization
        # 3. Fallback: Operating Income only (understates by D&A amount)
        quarterly_ebitda = financials.ebitda
        cash = financials.cash_and_equivalents or 0

        # If no EBITDA, try to compute from components
        if not quarterly_ebitda:
            if financials.operating_income and financials.depreciation_amortization:
                # Best: compute EBITDA = Operating Income + D&A
                quarterly_ebitda = financials.operating_income + financials.depreciation_amortization
            elif financials.operating_income:
                # Fallback: use Operating Income alone (understates by D&A)
                quarterly_ebitda = financials.operating_income

        if quarterly_ebitda and quarterly_ebitda > 0:
            # Annualize quarterly EBITDA
            annual_ebitda = quarterly_ebitda * 4

            # Leverage = Total Debt / EBITDA
            if total_debt > 0:
                lev = total_debt / annual_ebitda
                # Sanity check: leverage > 100x indicates bad data (skip)
                if lev <= 100:
                    leverage_ratio = Decimal(str(round(lev, 2)))

            # Net Debt = Total Debt - Cash
            net_debt = total_debt - cash

            # Net Leverage = Net Debt / EBITDA
            if net_debt:
                net_lev = net_debt / annual_ebitda
                if abs(net_lev) <= 100:
                    net_leverage_ratio = Decimal(str(round(net_lev, 2)))

            # Secured Leverage = Secured Debt / EBITDA
            if secured_debt > 0:
                sec_lev = secured_debt / annual_ebitda
                if sec_lev <= 100:
                    secured_leverage = Decimal(str(round(sec_lev, 2)))

        # Interest Coverage = EBITDA / Interest Expense
        if quarterly_ebitda and financials.interest_expense and financials.interest_expense > 0:
            cov = quarterly_ebitda / financials.interest_expense
            # Coverage > 100x is unusual but possible (cap at 999.99 for DB)
            if cov <= 999.99:
                interest_coverage = Decimal(str(round(cov, 2)))

    # Build metrics dict
    metrics_data = {
        "ticker": ticker,
        "company_id": company_id,
        "sector": company.sector,
        "industry": company.industry,
        "total_debt": total_debt,
        "secured_debt": secured_debt,
        "unsecured_debt": unsecured_debt,
        "net_debt": net_debt,
        "leverage_ratio": leverage_ratio,
        "net_leverage_ratio": net_leverage_ratio,
        "interest_coverage": interest_coverage,
        "secured_leverage": secured_leverage,
        "entity_count": len(entities),
        "guarantor_count": sum(1 for e in entities if e.is_guarantor),
        "nearest_maturity": nearest_maturity,
        "debt_due_1yr": debt_due_1yr,
        "debt_due_2yr": debt_due_2yr,
        "debt_due_3yr": debt_due_3yr,
        "weighted_avg_maturity": weighted_avg_maturity,
        "has_near_term_maturity": has_near_term_maturity,
        "subordination_risk": subordination_risk,
        "subordination_score": subordination_score,
        "has_holdco_debt": has_holdco_debt,
        "has_opco_debt": has_opco_debt,
        "has_structural_sub": has_structural_sub,
        "has_unrestricted_subs": has_unrestricted_subs,
        "has_floating_rate": has_floating_rate,
        "is_leveraged_loan": leverage_ratio is not None and leverage_ratio > 4,
    }

    if not dry_run:
        # Get or create metrics record
        result = await db.execute(
            select(CompanyMetrics).where(CompanyMetrics.ticker == ticker)
        )
        metrics = result.scalar_one_or_none()

        if metrics:
            # Update existing
            for key, value in metrics_data.items():
                if key not in ("ticker", "company_id"):
                    setattr(metrics, key, value)
        else:
            # Create new
            metrics = CompanyMetrics(**metrics_data)
            db.add(metrics)

        await db.flush()

    return metrics_data


async def main():
    parser = argparse.ArgumentParser(description="Recompute company metrics")
    parser.add_argument("--ticker", help="Single ticker to process")
    parser.add_argument("--dry-run", action="store_true", help="Preview without saving")
    args = parser.parse_args()

    # Create async engine
    database_url = settings.database_url
    if database_url.startswith("postgresql://"):
        database_url = database_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    elif not database_url.startswith("postgresql+asyncpg://"):
        # Handle case where it already has asyncpg
        pass

    engine = create_async_engine(database_url, echo=False)
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with async_session() as db:
        # Get companies to process
        if args.ticker:
            result = await db.execute(
                select(Company).where(Company.ticker == args.ticker.upper())
            )
            companies = list(result.scalars().all())
            if not companies:
                print(f"Company {args.ticker} not found")
                return
        else:
            result = await db.execute(select(Company).order_by(Company.ticker))
            companies = list(result.scalars().all())

        print(f"Processing {len(companies)} companies...")
        if args.dry_run:
            print("(DRY RUN - no changes will be saved)")
        print()

        for company in companies:
            try:
                metrics = await recompute_metrics_for_company(db, company, args.dry_run)

                # Format output
                total_debt_b = (metrics["total_debt"] or 0) / 100_000_000_000
                wam = metrics["weighted_avg_maturity"]
                wam_str = f"{wam:.1f}y" if wam else "N/A"
                lev = metrics["leverage_ratio"]
                lev_str = f"{lev:.1f}x" if lev else "N/A"
                cov = metrics["interest_coverage"]
                cov_str = f"{cov:.1f}x" if cov else "N/A"

                flags = []
                if metrics["has_near_term_maturity"]:
                    flags.append("NEAR_MAT")
                if metrics["has_structural_sub"]:
                    flags.append("STRUCT_SUB")
                if metrics["has_floating_rate"]:
                    flags.append("FLOAT")
                if metrics["is_leveraged_loan"]:
                    flags.append("LEV>4x")

                print(f"  {company.ticker:6} | debt: ${total_debt_b:6.1f}B | "
                      f"lev: {lev_str:5} | cov: {cov_str:5} | "
                      f"WAM: {wam_str:5} | {' '.join(flags)}")

            except Exception as e:
                print(f"  {company.ticker:6} | ERROR: {e}")

        if not args.dry_run:
            await db.commit()
            print(f"\nCommitted changes for {len(companies)} companies")
        else:
            print(f"\nDry run complete - no changes saved")


if __name__ == "__main__":
    asyncio.run(main())
