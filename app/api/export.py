"""
Bulk Export API - Business Tier Only

GET /v1/export - Bulk data export for offline analysis
"""

import csv
import io
from datetime import datetime
from typing import Optional, List, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.auth import require_auth, check_tier_access
from app.core.database import get_db
from app.models import (
    User, Company, DebtInstrument, Entity, CompanyMetrics,
    CompanyFinancials, BondPricing, Covenant,
)

router = APIRouter(tags=["export"])


# =============================================================================
# Export Endpoints
# =============================================================================


@router.get("/export")
async def bulk_export(
    data_type: Literal["companies", "bonds", "financials", "covenants"] = Query(
        ..., description="Type of data to export"
    ),
    format: Literal["csv", "json"] = Query("csv", description="Export format"),
    ticker: Optional[str] = Query(None, description="Filter by company ticker(s), comma-separated"),
    sector: Optional[str] = Query(None, description="Filter by sector"),
    limit: int = Query(10000, description="Maximum records to export", le=50000),
    user: User = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """
    Bulk export data for offline analysis.

    **Business tier only** - Returns 403 for Pay-as-You-Go and Pro users.

    Export types:
    - `companies`: Company metadata, metrics, and debt totals
    - `bonds`: All debt instruments with pricing
    - `financials`: Quarterly financial statements
    - `covenants`: Extracted covenant data

    Max 50,000 records per export. Use filters to narrow results.
    """
    # Check tier access
    allowed, error_msg = check_tier_access(user, "/v1/export")
    if not allowed:
        raise HTTPException(status_code=403, detail=error_msg)

    # Parse ticker filter
    tickers = None
    if ticker:
        tickers = [t.strip().upper() for t in ticker.split(",")]

    if data_type == "companies":
        data = await _export_companies(db, tickers, sector, limit)
    elif data_type == "bonds":
        data = await _export_bonds(db, tickers, sector, limit)
    elif data_type == "financials":
        data = await _export_financials(db, tickers, limit)
    elif data_type == "covenants":
        data = await _export_covenants(db, tickers, limit)
    else:
        raise HTTPException(status_code=400, detail=f"Invalid data_type: {data_type}")

    if format == "json":
        return {
            "data_type": data_type,
            "record_count": len(data),
            "exported_at": datetime.utcnow().isoformat(),
            "data": data,
        }

    # CSV format
    return _to_csv_response(data, f"debtstack_export_{data_type}_{datetime.utcnow().strftime('%Y%m%d')}.csv")


# =============================================================================
# Export Helpers
# =============================================================================


async def _export_companies(
    db: AsyncSession,
    tickers: Optional[List[str]],
    sector: Optional[str],
    limit: int,
) -> List[dict]:
    """Export company data with metrics."""
    query = (
        select(Company, CompanyMetrics)
        .outerjoin(CompanyMetrics, Company.id == CompanyMetrics.company_id)
        .limit(limit)
    )

    if tickers:
        query = query.where(Company.ticker.in_(tickers))
    if sector:
        query = query.where(Company.sector == sector)

    result = await db.execute(query)
    rows = result.all()

    data = []
    for company, metrics in rows:
        row = {
            "ticker": company.ticker,
            "name": company.name,
            "sector": company.sector,
            "industry": company.industry,
            "cik": company.cik,
        }
        if metrics:
            row.update({
                "total_debt": metrics.total_debt,
                "secured_debt": metrics.secured_debt,
                "unsecured_debt": metrics.unsecured_debt,
                "net_debt": metrics.net_debt,
                "leverage_ratio": float(metrics.leverage_ratio) if metrics.leverage_ratio else None,
                "net_leverage_ratio": float(metrics.net_leverage_ratio) if metrics.net_leverage_ratio else None,
                "interest_coverage": float(metrics.interest_coverage) if metrics.interest_coverage else None,
                "entity_count": metrics.entity_count,
                "guarantor_count": metrics.guarantor_count,
                "subordination_risk": metrics.subordination_risk,
                "nearest_maturity": str(metrics.nearest_maturity) if metrics.nearest_maturity else None,
                "sp_rating": metrics.sp_rating,
                "moodys_rating": metrics.moodys_rating,
            })
        data.append(row)

    return data


async def _export_bonds(
    db: AsyncSession,
    tickers: Optional[List[str]],
    sector: Optional[str],
    limit: int,
) -> List[dict]:
    """Export bond data with pricing."""
    query = (
        select(DebtInstrument, BondPricing, Company)
        .join(Company, DebtInstrument.company_id == Company.id)
        .outerjoin(BondPricing, DebtInstrument.id == BondPricing.debt_instrument_id)
        .where(DebtInstrument.is_active == True)
        .limit(limit)
    )

    if tickers:
        query = query.where(Company.ticker.in_(tickers))
    if sector:
        query = query.where(Company.sector == sector)

    result = await db.execute(query)
    rows = result.all()

    data = []
    for bond, pricing, company in rows:
        row = {
            "ticker": company.ticker,
            "company_name": company.name,
            "sector": company.sector,
            "bond_name": bond.name,
            "cusip": bond.cusip,
            "isin": bond.isin,
            "instrument_type": bond.instrument_type,
            "seniority": bond.seniority,
            "security_type": bond.security_type,
            "principal": bond.principal,
            "outstanding": bond.outstanding,
            "coupon_rate": float(bond.interest_rate) / 100 if bond.interest_rate else None,
            "spread_bps": bond.spread_bps,
            "benchmark": bond.benchmark,
            "issue_date": str(bond.issue_date) if bond.issue_date else None,
            "maturity_date": str(bond.maturity_date) if bond.maturity_date else None,
        }
        if pricing:
            row.update({
                "last_price": float(pricing.last_price) if pricing.last_price else None,
                "ytm_pct": float(pricing.ytm_bps) / 100 if pricing.ytm_bps else None,
                "spread_to_treasury_bps": pricing.spread_to_treasury_bps,
                "last_trade_date": str(pricing.last_trade_date) if pricing.last_trade_date else None,
            })
        data.append(row)

    return data


async def _export_financials(
    db: AsyncSession,
    tickers: Optional[List[str]],
    limit: int,
) -> List[dict]:
    """Export financial statement data."""
    query = (
        select(CompanyFinancials, Company)
        .join(Company, CompanyFinancials.company_id == Company.id)
        .order_by(Company.ticker, CompanyFinancials.period_end_date.desc())
        .limit(limit)
    )

    if tickers:
        query = query.where(Company.ticker.in_(tickers))

    result = await db.execute(query)
    rows = result.all()

    data = []
    for financials, company in rows:
        data.append({
            "ticker": company.ticker,
            "company_name": company.name,
            "fiscal_year": financials.fiscal_year,
            "fiscal_quarter": financials.fiscal_quarter,
            "period_end_date": str(financials.period_end_date),
            "filing_type": financials.filing_type,
            "revenue": financials.revenue,
            "gross_profit": financials.gross_profit,
            "operating_income": financials.operating_income,
            "ebitda": financials.ebitda,
            "interest_expense": financials.interest_expense,
            "net_income": financials.net_income,
            "cash_and_equivalents": financials.cash_and_equivalents,
            "total_assets": financials.total_assets,
            "total_debt": financials.total_debt,
            "total_liabilities": financials.total_liabilities,
            "stockholders_equity": financials.stockholders_equity,
            "operating_cash_flow": financials.operating_cash_flow,
            "capex": financials.capex,
        })

    return data


async def _export_covenants(
    db: AsyncSession,
    tickers: Optional[List[str]],
    limit: int,
) -> List[dict]:
    """Export covenant data."""
    query = (
        select(Covenant, Company, DebtInstrument)
        .join(Company, Covenant.company_id == Company.id)
        .outerjoin(DebtInstrument, Covenant.debt_instrument_id == DebtInstrument.id)
        .order_by(Company.ticker, Covenant.covenant_type)
        .limit(limit)
    )

    if tickers:
        query = query.where(Company.ticker.in_(tickers))

    result = await db.execute(query)
    rows = result.all()

    data = []
    for covenant, company, instrument in rows:
        data.append({
            "ticker": company.ticker,
            "company_name": company.name,
            "instrument_name": instrument.name if instrument else None,
            "cusip": instrument.cusip if instrument else None,
            "covenant_type": covenant.covenant_type,
            "covenant_name": covenant.covenant_name,
            "test_metric": covenant.test_metric,
            "threshold_value": float(covenant.threshold_value) if covenant.threshold_value else None,
            "threshold_type": covenant.threshold_type,
            "test_frequency": covenant.test_frequency,
            "description": covenant.description,
            "has_step_down": covenant.has_step_down,
            "extraction_confidence": float(covenant.extraction_confidence) if covenant.extraction_confidence else None,
        })

    return data


def _to_csv_response(data: List[dict], filename: str) -> StreamingResponse:
    """Convert list of dicts to CSV streaming response."""
    if not data:
        output = io.StringIO()
        output.write("")
        output.seek(0)
        return StreamingResponse(
            iter([output.getvalue()]),
            media_type="text/csv",
            headers={"Content-Disposition": f"attachment; filename={filename}"}
        )

    # Get all unique keys across all rows
    all_keys = set()
    for row in data:
        all_keys.update(row.keys())
    fieldnames = sorted(all_keys)

    # Write CSV
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=fieldnames, extrasaction='ignore')
    writer.writeheader()
    writer.writerows(data)

    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )
