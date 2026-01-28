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

    # Get TTM financials (4 quarters) for more accurate ratios
    ttm_financials = await get_ttm_financials(db, company_id)

    # Calculate TTM EBITDA from available quarters
    ttm_ebitda = 0
    ttm_interest = 0
    quarters_with_ebitda = 0
    ttm_quarters = []  # Track which quarters were used
    ttm_filings = []   # Track source filing URLs

    for fin in ttm_financials:
        # Get EBITDA for this quarter (direct or computed)
        q_ebitda = fin.ebitda
        if not q_ebitda:
            if fin.operating_income and fin.depreciation_amortization:
                q_ebitda = fin.operating_income + fin.depreciation_amortization
            elif fin.operating_income:
                q_ebitda = fin.operating_income

        if q_ebitda and q_ebitda > 0:
            ttm_ebitda += q_ebitda
            quarters_with_ebitda += 1
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
    if ttm_ebitda and ttm_ebitda > 0 and quarters_with_ebitda >= 1:
        # Leverage = Total Debt / TTM EBITDA
        if total_debt > 0:
            lev = total_debt / ttm_ebitda
            # Sanity check: leverage > 20x is unusual (skip unless confirmed)
            if lev <= 20:
                leverage_ratio = Decimal(str(round(lev, 2)))

        # Net Debt = Total Debt - Cash
        net_debt = total_debt - cash

        # Net Leverage = Net Debt / TTM EBITDA
        if net_debt:
            net_lev = net_debt / ttm_ebitda
            if abs(net_lev) <= 20:
                net_leverage_ratio = Decimal(str(round(net_lev, 2)))

        # Secured Leverage = Secured Debt / TTM EBITDA
        if secured_debt > 0:
            sec_lev = secured_debt / ttm_ebitda
            if sec_lev <= 20:
                secured_leverage = Decimal(str(round(sec_lev, 2)))

        # Interest Coverage = TTM EBITDA / TTM Interest Expense
        if ttm_interest and ttm_interest > 0:
            cov = ttm_ebitda / ttm_interest
            # Coverage > 100x is unusual but possible (cap at 999.99 for DB)
            if cov <= 999.99:
                interest_coverage = Decimal(str(round(cov, 2)))

    # Build source filings provenance
    source_filings = {
        "debt_source": debt_source,
        "ttm_quarters": ttm_quarters,
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
