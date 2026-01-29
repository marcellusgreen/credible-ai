"""
Metrics Computation Service for DebtStack.ai

Computes derived credit metrics from extracted data:
- Leverage ratios (using balance sheet debt as primary source)
- Interest coverage
- Maturity profile
- Structural subordination flags
- Source filing provenance tracking
"""

from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Optional
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Company, CompanyMetrics, CompanyFinancials, DebtInstrument, Entity


async def get_latest_financials(db: AsyncSession, company_id: UUID) -> Optional[CompanyFinancials]:
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


async def get_ttm_financials(db: AsyncSession, company_id: UUID) -> list[CompanyFinancials]:
    """Get trailing 4 quarters of financial data for TTM calculations."""
    result = await db.execute(
        select(CompanyFinancials)
        .where(CompanyFinancials.company_id == company_id)
        .order_by(
            CompanyFinancials.fiscal_year.desc(),
            CompanyFinancials.fiscal_quarter.desc(),
        )
        .limit(4)
    )
    return list(result.scalars().all())


async def recompute_metrics_for_company(
    db: AsyncSession,
    company: Company,
    dry_run: bool = False,
) -> dict:
    """
    Recompute metrics for a single company.

    Uses balance sheet total_debt as primary source for leverage calculations
    (more accurate than summing extracted instruments). Tracks provenance
    of all source filings used in TTM calculations.

    Args:
        db: Database session
        company: Company object to compute metrics for
        dry_run: If True, compute but don't save to database

    Returns:
        Dict of computed metrics
    """
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

    # Get latest financials for balance sheet debt
    latest_financials = await get_latest_financials(db, company_id)

    # Calculate debt totals
    # Primary: Use balance sheet total_debt (audited, includes all debt)
    # Fallback: Sum of extracted instruments (may miss some debt)
    instrument_debt = sum(d.outstanding or 0 for d in debt_instruments)
    balance_sheet_debt = latest_financials.total_debt if latest_financials and latest_financials.total_debt else None

    # Use balance sheet debt for leverage calculations, instrument sum for breakdowns
    total_debt = balance_sheet_debt if balance_sheet_debt else instrument_debt
    debt_source = "balance_sheet" if balance_sheet_debt else "instruments"

    # Track discrepancy for data quality monitoring
    debt_discrepancy_pct = None
    if balance_sheet_debt and instrument_debt and balance_sheet_debt > 0:
        debt_discrepancy_pct = abs(balance_sheet_debt - instrument_debt) / balance_sheet_debt * 100

    # Seniority breakdown still uses instruments (balance sheet doesn't have this detail)
    secured_debt = sum(
        d.outstanding or 0 for d in debt_instruments
        if d.seniority == "senior_secured"
    )
    unsecured_debt = instrument_debt - secured_debt  # Use instrument total for breakdown

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

    # Get TTM financials for EBITDA calculation
    # Rule: If latest filing is 10-K, use annual figures directly (already TTM)
    #       If latest filing is 10-Q, sum trailing 4 quarters
    ttm_financials = await get_ttm_financials(db, company_id)

    # Calculate TTM EBITDA
    ttm_ebitda = 0
    ttm_interest = 0
    quarters_with_ebitda = 0
    quarters_with_da = 0  # Track quarters where D&A was available
    ttm_quarters = []  # Track which quarters were used
    ttm_filings = []   # Track source filing URLs
    ebitda_source = "quarterly"  # Track whether we used annual or quarterly data

    if ttm_financials:
        latest = ttm_financials[0]

        # Check if latest filing is a 10-K (annual report)
        if latest.filing_type == "10-K":
            # Use annual figures directly - they represent full year TTM
            ebitda_source = "annual_10k"
            q_ebitda = latest.ebitda
            has_da = False

            if not q_ebitda:
                if latest.operating_income and latest.depreciation_amortization:
                    q_ebitda = latest.operating_income + latest.depreciation_amortization
                    has_da = True
                elif latest.operating_income:
                    q_ebitda = latest.operating_income
            else:
                has_da = True

            if q_ebitda and q_ebitda > 0:
                ttm_ebitda = q_ebitda
                quarters_with_ebitda = 4  # Annual = 4 quarters equivalent
                if has_da:
                    quarters_with_da = 4
                ttm_quarters.append(f"{latest.fiscal_year}FY")
                if latest.source_filing:
                    ttm_filings.append(latest.source_filing)

            if latest.interest_expense:
                ttm_interest = latest.interest_expense

        else:
            # Latest is 10-Q - sum trailing 4 quarters
            ebitda_source = "quarterly_sum"
            for fin in ttm_financials:
                # Skip if this is an annual 10-K mixed in with quarters
                # (we only want quarterly 10-Q data for summation)
                if fin.filing_type == "10-K":
                    continue

                # Get EBITDA for this quarter (direct or computed)
                q_ebitda = fin.ebitda
                has_da = False
                if not q_ebitda:
                    if fin.operating_income and fin.depreciation_amortization:
                        q_ebitda = fin.operating_income + fin.depreciation_amortization
                        has_da = True
                    elif fin.operating_income:
                        # Fallback to operating income when D&A not available
                        q_ebitda = fin.operating_income
                else:
                    # Direct EBITDA was available
                    has_da = True

                if q_ebitda and q_ebitda > 0:
                    ttm_ebitda += q_ebitda
                    quarters_with_ebitda += 1
                    if has_da:
                        quarters_with_da += 1
                    ttm_quarters.append(f"{fin.fiscal_year}Q{fin.fiscal_quarter}")
                    if fin.source_filing:
                        ttm_filings.append(fin.source_filing)

                if fin.interest_expense:
                    ttm_interest += fin.interest_expense

            # If we have fewer than 4 quarters, annualize what we have
            if quarters_with_ebitda > 0 and quarters_with_ebitda < 4:
                ttm_ebitda = int(ttm_ebitda * (4 / quarters_with_ebitda))
                ttm_interest = int(ttm_interest * (4 / quarters_with_ebitda))

    # Get cash from latest quarter
    cash = latest_financials.cash_and_equivalents if latest_financials else 0
    cash = cash or 0

    # Require at least 1 quarter of EBITDA data for leverage calculation
    # (single quarter will be annualized - less accurate but better than nothing)
    # Note: We calculate leverage even if extreme - users can filter based on
    # ebitda_quarters in source_filings for data quality assessment
    if ttm_ebitda and ttm_ebitda > 0 and quarters_with_ebitda >= 1:
        # Leverage = Total Debt / TTM EBITDA
        if total_debt > 0:
            lev = total_debt / ttm_ebitda
            # Cap at 999.99 for DB storage, but still save extreme values
            leverage_ratio = Decimal(str(round(min(lev, 999.99), 2)))

        # Net Debt = Total Debt - Cash
        net_debt = total_debt - cash

        # Net Leverage = Net Debt / TTM EBITDA
        if net_debt:
            net_lev = net_debt / ttm_ebitda
            # Cap at 999.99 for DB storage
            net_leverage_ratio = Decimal(str(round(min(abs(net_lev), 999.99), 2)))
            if net_lev < 0:
                net_leverage_ratio = -net_leverage_ratio

        # Secured Leverage = Secured Debt / TTM EBITDA
        if secured_debt > 0:
            sec_lev = secured_debt / ttm_ebitda
            secured_leverage = Decimal(str(round(min(sec_lev, 999.99), 2)))

        # Interest Coverage = TTM EBITDA / TTM Interest Expense
        if ttm_interest and ttm_interest > 0:
            cov = ttm_ebitda / ttm_interest
            # Coverage > 100x is unusual but possible (cap at 999.99 for DB)
            if cov <= 999.99:
                interest_coverage = Decimal(str(round(cov, 2)))

    # Build source filings provenance
    source_filings = {
        "debt_source": debt_source,
        "ebitda_source": ebitda_source,  # "annual_10k" or "quarterly_sum"
        "ttm_quarters": ttm_quarters,
        "ebitda_quarters": quarters_with_ebitda,  # Number of quarters used for EBITDA
        "ebitda_quarters_with_da": quarters_with_da,  # Quarters where D&A was available
        "is_annualized": ebitda_source == "quarterly_sum" and quarters_with_ebitda < 4 and quarters_with_ebitda > 0,
        "ebitda_estimated": quarters_with_da < quarters_with_ebitda,  # True if some quarters used OpInc only
        "computed_at": datetime.utcnow().isoformat() + "Z",
    }
    # Add debt filing URL if using balance sheet
    if debt_source == "balance_sheet" and latest_financials and latest_financials.source_filing:
        source_filings["debt_filing"] = latest_financials.source_filing
    # Add TTM filing URLs if available
    if ttm_filings:
        source_filings["ttm_filings"] = ttm_filings
    # Track debt discrepancy for data quality
    if debt_discrepancy_pct is not None:
        source_filings["debt_discrepancy_pct"] = round(debt_discrepancy_pct, 1)

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
        "source_filings": source_filings,
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


# =============================================================================
# CLI
# =============================================================================

if __name__ == "__main__":
    import argparse
    import asyncio
    import sys

    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
    from sqlalchemy.orm import sessionmaker

    # Fix Windows encoding
    if sys.platform == 'win32':
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')

    # Add parent to path for imports
    sys.path.insert(0, str(__file__).replace('app/services/metrics.py', ''))

    from app.core.config import get_settings

    async def main():
        parser = argparse.ArgumentParser(description="Compute credit metrics for companies")
        parser.add_argument("--ticker", help="Company ticker")
        parser.add_argument("--all", action="store_true", help="Process all companies")
        parser.add_argument("--limit", type=int, help="Limit companies")
        parser.add_argument("--dry-run", action="store_true", help="Compute but don't save")
        args = parser.parse_args()

        if not args.ticker and not args.all:
            print("Usage: python -m app.services.metrics --ticker CHTR")
            print("       python -m app.services.metrics --all [--limit N]")
            return

        settings = get_settings()
        engine = create_async_engine(
            settings.database_url.replace("postgresql://", "postgresql+asyncpg://")
        )
        async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

        async with async_session() as db:
            if args.ticker:
                result = await db.execute(
                    select(Company).where(Company.ticker == args.ticker.upper())
                )
                companies = [result.scalar_one_or_none()]
            else:
                result = await db.execute(
                    select(Company).order_by(Company.ticker)
                )
                companies = list(result.scalars())
                if args.limit:
                    companies = companies[:args.limit]

        print(f"Processing {len(companies)} companies")
        total = 0

        for company in companies:
            if not company:
                continue

            async with async_session() as db:
                print(f"[{company.ticker}] {company.name}")
                metrics = await recompute_metrics_for_company(db, company, dry_run=args.dry_run)

                lev = metrics.get('leverage_ratio')
                cov = metrics.get('interest_coverage')
                debt = metrics.get('total_debt', 0) or 0
                print(f"  Debt: ${debt/100/1e9:.2f}B | Leverage: {lev}x | Coverage: {cov}x")

                if not args.dry_run:
                    await db.commit()
                total += 1

        print(f"\nProcessed {total} companies")
        await engine.dispose()

    asyncio.run(main())
