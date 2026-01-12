"""
API routes for Credible.ai

Core endpoints:
- GET /v1/companies
- GET /v1/companies/{ticker}
- GET /v1/companies/{ticker}/structure
- GET /v1/companies/{ticker}/debt
- GET /v1/health
"""

from datetime import datetime
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from fastapi.responses import ORJSONResponse
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models import Company, CompanyCache, CompanyMetrics

router = APIRouter()


# =============================================================================
# HEALTH CHECK
# =============================================================================


@router.get("/health", tags=["System"])
async def health_check(db: AsyncSession = Depends(get_db)):
    """Health check endpoint."""
    checks = {}

    # Database check
    try:
        await db.execute(select(func.now()))
        checks["database"] = "healthy"
    except Exception as e:
        checks["database"] = f"unhealthy: {str(e)}"

    healthy = all(v == "healthy" for v in checks.values())

    return {
        "status": "healthy" if healthy else "degraded",
        "checks": checks,
        "version": "1.0.0",
        "timestamp": datetime.utcnow().isoformat() + "Z",
    }


# =============================================================================
# COMPANIES LIST
# =============================================================================


@router.get("/companies", tags=["Companies"])
async def list_companies(
    sector: Optional[str] = Query(None, description="Filter by sector"),
    limit: int = Query(50, ge=1, le=100, description="Number of results"),
    offset: int = Query(0, ge=0, description="Offset for pagination"),
    db: AsyncSession = Depends(get_db),
):
    """
    List all companies in the database.

    Returns a paginated list of companies with summary metrics.
    """
    # Build query
    query = select(CompanyMetrics)
    count_query = select(func.count()).select_from(CompanyMetrics)

    if sector:
        query = query.where(CompanyMetrics.sector == sector)
        count_query = count_query.where(CompanyMetrics.sector == sector)

    # Get total count
    total = await db.scalar(count_query)

    # Get results
    query = query.order_by(CompanyMetrics.ticker).offset(offset).limit(limit)
    result = await db.execute(query)
    companies = result.scalars().all()

    return {
        "data": [
            {
                "ticker": c.ticker,
                "sector": c.sector,
                "industry": c.industry,
                "total_debt": c.total_debt,
                "entity_count": c.entity_count,
                "guarantor_count": c.guarantor_count,
                "subordination_risk": c.subordination_risk,
                "nearest_maturity": c.nearest_maturity.isoformat() if c.nearest_maturity else None,
                "has_structural_sub": c.has_structural_sub,
                "has_floating_rate": c.has_floating_rate,
            }
            for c in companies
        ],
        "meta": {
            "total": total,
            "limit": limit,
            "offset": offset,
        },
    }


# =============================================================================
# COMPANY DETAIL
# =============================================================================


@router.get("/companies/{ticker}", tags=["Companies"])
async def get_company(
    ticker: str,
    db: AsyncSession = Depends(get_db),
):
    """
    Get company overview.

    Returns basic company information and summary metrics.
    """
    ticker = ticker.upper()

    # Get from cache
    result = await db.execute(
        select(CompanyCache).where(CompanyCache.ticker == ticker)
    )
    cache = result.scalar_one_or_none()

    if not cache:
        raise HTTPException(status_code=404, detail=f"Company {ticker} not found")

    # Return pre-computed response
    return ORJSONResponse(
        content={
            "data": cache.response_company,
            "meta": {
                "cached": True,
                "etag": cache.etag,
                "computed_at": cache.computed_at.isoformat() + "Z" if cache.computed_at else None,
            },
        },
        headers={
            "ETag": cache.etag or "",
            "Cache-Control": "public, max-age=3600",
        },
    )


# =============================================================================
# COMPANY STRUCTURE
# =============================================================================


@router.get("/companies/{ticker}/structure", tags=["Companies"])
async def get_company_structure(
    ticker: str,
    db: AsyncSession = Depends(get_db),
):
    """
    Get corporate entity structure.

    Returns the full entity hierarchy with ownership relationships,
    guarantor status, and debt at each entity level.
    """
    ticker = ticker.upper()

    result = await db.execute(
        select(CompanyCache).where(CompanyCache.ticker == ticker)
    )
    cache = result.scalar_one_or_none()

    if not cache:
        raise HTTPException(status_code=404, detail=f"Company {ticker} not found")

    if not cache.response_structure:
        raise HTTPException(
            status_code=404,
            detail=f"Structure data not available for {ticker}",
        )

    return ORJSONResponse(
        content={
            "data": cache.response_structure,
            "meta": {
                "cached": True,
                "etag": cache.etag,
            },
        },
        headers={
            "ETag": cache.etag or "",
            "Cache-Control": "public, max-age=3600",
        },
    )


# =============================================================================
# COMPANY DEBT
# =============================================================================


@router.get("/companies/{ticker}/debt", tags=["Companies"])
async def get_company_debt(
    ticker: str,
    db: AsyncSession = Depends(get_db),
):
    """
    Get debt instruments.

    Returns all debt facilities and securities with terms,
    guarantors, and maturity information.
    """
    ticker = ticker.upper()

    result = await db.execute(
        select(CompanyCache).where(CompanyCache.ticker == ticker)
    )
    cache = result.scalar_one_or_none()

    if not cache:
        raise HTTPException(status_code=404, detail=f"Company {ticker} not found")

    if not cache.response_debt:
        raise HTTPException(
            status_code=404,
            detail=f"Debt data not available for {ticker}",
        )

    return ORJSONResponse(
        content={
            "data": cache.response_debt,
            "meta": {
                "cached": True,
                "etag": cache.etag,
            },
        },
        headers={
            "ETag": cache.etag or "",
            "Cache-Control": "public, max-age=3600",
        },
    )


# =============================================================================
# SECTORS LIST (Utility endpoint)
# =============================================================================


@router.get("/sectors", tags=["Metadata"])
async def list_sectors(db: AsyncSession = Depends(get_db)):
    """
    List all sectors with company counts.
    """
    result = await db.execute(
        select(
            CompanyMetrics.sector,
            func.count(CompanyMetrics.ticker).label("count"),
        )
        .where(CompanyMetrics.sector.isnot(None))
        .group_by(CompanyMetrics.sector)
        .order_by(func.count(CompanyMetrics.ticker).desc())
    )

    sectors = result.all()

    return {
        "data": [{"sector": s[0], "company_count": s[1]} for s in sectors],
    }
