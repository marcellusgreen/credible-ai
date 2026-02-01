"""
Historical Pricing API - Business Tier Only

GET /v1/bonds/{cusip}/pricing/history - Historical bond pricing data
"""

from datetime import date, datetime, timedelta
from typing import Optional, List
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Path
from pydantic import BaseModel
from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import require_auth, check_tier_access
from app.core.database import get_db
from app.models import User, DebtInstrument, BondPricingHistory

router = APIRouter(tags=["historical-pricing"])


# =============================================================================
# Response Models
# =============================================================================


class PricePoint(BaseModel):
    """Single historical price point."""
    date: date
    price: Optional[float]
    ytm_pct: Optional[float]  # Yield as percentage (not bps)
    spread_bps: Optional[int]
    volume: Optional[int]
    source: str


class HistoricalPricingResponse(BaseModel):
    """Response for historical pricing endpoint."""
    cusip: str
    bond_name: str
    company_ticker: str
    from_date: date
    to_date: date
    data_points: int
    prices: List[PricePoint]


# =============================================================================
# Endpoints
# =============================================================================


@router.get("/bonds/{cusip}/pricing/history", response_model=HistoricalPricingResponse)
async def get_historical_pricing(
    cusip: str = Path(..., description="Bond CUSIP identifier"),
    from_date: Optional[date] = Query(None, description="Start date (default: 1 year ago)"),
    to_date: Optional[date] = Query(None, description="End date (default: today)"),
    user: User = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """
    Get historical pricing data for a bond.

    **Business tier only** - Returns 403 for Pay-as-You-Go and Pro users.

    Returns daily price snapshots including:
    - Clean price (as % of par)
    - Yield to maturity
    - Spread over treasury
    - Trading volume

    Default date range is last 365 days.
    """
    # Check tier access
    allowed, error_msg = check_tier_access(user, "/v1/bonds/{cusip}/pricing/history")
    if not allowed:
        raise HTTPException(status_code=403, detail=error_msg)

    # Set default date range
    if not to_date:
        to_date = date.today()
    if not from_date:
        from_date = to_date - timedelta(days=365)

    # Validate date range
    if from_date > to_date:
        raise HTTPException(status_code=400, detail="from_date must be before to_date")

    max_range = timedelta(days=730)  # 2 years max
    if (to_date - from_date) > max_range:
        raise HTTPException(status_code=400, detail="Date range cannot exceed 2 years")

    # Find bond by CUSIP
    bond_result = await db.execute(
        select(DebtInstrument).where(DebtInstrument.cusip == cusip)
    )
    bond = bond_result.scalar_one_or_none()

    if not bond:
        raise HTTPException(status_code=404, detail=f"Bond with CUSIP {cusip} not found")

    # Get company ticker
    company_ticker = bond.company.ticker if bond.company else "UNKNOWN"

    # Query historical pricing
    pricing_result = await db.execute(
        select(BondPricingHistory)
        .where(
            and_(
                BondPricingHistory.debt_instrument_id == bond.id,
                BondPricingHistory.price_date >= from_date,
                BondPricingHistory.price_date <= to_date,
            )
        )
        .order_by(BondPricingHistory.price_date.asc())
    )
    pricing_rows = pricing_result.scalars().all()

    # Convert to response format
    prices = []
    for row in pricing_rows:
        ytm_pct = float(row.ytm_bps) / 100 if row.ytm_bps else None
        prices.append(PricePoint(
            date=row.price_date,
            price=float(row.price) if row.price else None,
            ytm_pct=ytm_pct,
            spread_bps=row.spread_bps,
            volume=row.volume,
            source=row.price_source,
        ))

    return HistoricalPricingResponse(
        cusip=cusip,
        bond_name=bond.name,
        company_ticker=company_ticker,
        from_date=from_date,
        to_date=to_date,
        data_points=len(prices),
        prices=prices,
    )
