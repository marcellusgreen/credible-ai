"""
API routes for DebtStack.ai

Core endpoints:
- GET /v1/companies
- GET /v1/companies/{ticker}
- GET /v1/companies/{ticker}/structure
- GET /v1/companies/{ticker}/debt
- GET /v1/companies/{ticker}/metrics
- GET /v1/companies/{ticker}/entities
- GET /v1/companies/{ticker}/guarantees
- GET /v1/companies/{ticker}/financials
- GET /v1/companies/{ticker}/ratios
- GET /v1/companies/{ticker}/obligor-group
- GET /v1/search/companies
- GET /v1/search/debt
- GET /v1/compare/companies
- GET /v1/status
- GET /v1/health
"""

from datetime import datetime, date
from typing import Any, Optional, List

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from fastapi.responses import ORJSONResponse
from sqlalchemy import select, func, or_, and_, case
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.database import get_db
from app.core.cache import cache_ping
from app.models import Company, CompanyCache, CompanyFinancials, CompanyMetrics, Entity, DebtInstrument, Guarantee, ObligorGroupFinancials, BondPricing, OwnershipLink
from app.services.yield_calculation import get_staleness_indicator

router = APIRouter()


# =============================================================================
# HELPER FUNCTIONS
# =============================================================================

async def get_company_or_404(db: AsyncSession, ticker: str) -> Company:
    """Get company by ticker or raise 404."""
    result = await db.execute(select(Company).where(Company.ticker == ticker.upper()))
    company = result.scalar_one_or_none()
    if not company:
        raise HTTPException(status_code=404, detail=f"Company {ticker.upper()} not found")
    return company


async def get_cache_or_404(db: AsyncSession, ticker: str) -> CompanyCache:
    """Get company cache by ticker or raise 404."""
    result = await db.execute(select(CompanyCache).where(CompanyCache.ticker == ticker.upper()))
    cache = result.scalar_one_or_none()
    if not cache:
        raise HTTPException(status_code=404, detail=f"Company {ticker.upper()} not found")
    return cache


def parse_uuid_or_400(id_str: str, name: str = "ID") -> "UUID":
    """Parse UUID string or raise 400."""
    from uuid import UUID
    try:
        return UUID(id_str)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid {name} format")


def company_header(company: Company) -> dict:
    """Standard company header for responses."""
    return {"ticker": company.ticker, "name": company.name}


def cached_response(cache: CompanyCache, data: Any) -> ORJSONResponse:
    """Return cached response with ETag headers."""
    return ORJSONResponse(
        content={"data": data, "meta": {"cached": True, "etag": cache.etag}},
        headers={"ETag": cache.etag or "", "Cache-Control": "public, max-age=3600"},
    )


# =============================================================================
# HEALTH CHECK
# =============================================================================


@router.get("/ping", tags=["System"])
async def ping():
    """Simple ping endpoint for load balancer health checks.

    Does not check database - just confirms the app is running.
    Use /health for full health status including database.
    """
    return {"status": "ok"}


@router.get("/debug/config", tags=["System"], include_in_schema=False)
async def debug_config():
    """Debug endpoint to check config (remove in production)."""
    from app.core.config import get_settings
    settings = get_settings()
    return {
        "has_redis_url": settings.redis_url is not None,
        "redis_url_prefix": settings.redis_url[:20] + "..." if settings.redis_url else None,
        "environment": settings.environment,
    }


@router.get("/health", tags=["System"])
async def health_check(db: AsyncSession = Depends(get_db)):
    """Full health check endpoint with database and cache verification."""
    checks = {}

    # Database check
    try:
        await db.execute(select(func.now()))
        checks["database"] = "healthy"
    except Exception as e:
        checks["database"] = f"unhealthy: {str(e)}"

    # Redis cache check
    from app.core.config import get_settings
    settings = get_settings()
    if not settings.redis_url:
        checks["cache"] = "not configured"
    else:
        try:
            success, message = await cache_ping()
            if success:
                checks["cache"] = "healthy"
            else:
                checks["cache"] = f"failed: {message}"
        except Exception as e:
            checks["cache"] = f"error: {str(e)}"

    # Only database is required for healthy status
    healthy = checks["database"] == "healthy"

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
async def get_company(ticker: str, db: AsyncSession = Depends(get_db)):
    """Get company overview with basic information and summary metrics."""
    cache = await get_cache_or_404(db, ticker)
    return cached_response(cache, cache.response_company)


# =============================================================================
# COMPANY STRUCTURE
# =============================================================================


@router.get("/companies/{ticker}/structure", tags=["Companies"])
async def get_company_structure(ticker: str, db: AsyncSession = Depends(get_db)):
    """Get corporate entity structure with hierarchy, guarantor status, and debt at each level."""
    cache = await get_cache_or_404(db, ticker)
    if not cache.response_structure:
        raise HTTPException(status_code=404, detail=f"Structure data not available for {ticker.upper()}")
    return cached_response(cache, cache.response_structure)


# =============================================================================
# COMPANY DEBT
# =============================================================================


@router.get("/companies/{ticker}/debt", tags=["Companies"])
async def get_company_debt(ticker: str, db: AsyncSession = Depends(get_db)):
    """Get all debt facilities and securities with terms, guarantors, and maturity."""
    cache = await get_cache_or_404(db, ticker)
    if not cache.response_debt:
        raise HTTPException(status_code=404, detail=f"Debt data not available for {ticker.upper()}")
    return cached_response(cache, cache.response_debt)


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


# =============================================================================
# COMPANY METRICS
# =============================================================================


@router.get("/companies/{ticker}/metrics", tags=["Companies"])
async def get_company_metrics(ticker: str, db: AsyncSession = Depends(get_db)):
    """Get detailed credit metrics: debt totals, structure metrics, maturity profile, risk scores."""
    ticker = ticker.upper()
    result = await db.execute(select(CompanyMetrics).where(CompanyMetrics.ticker == ticker))
    metrics = result.scalar_one_or_none()
    if not metrics:
        raise HTTPException(status_code=404, detail=f"Company {ticker} not found")

    company = await get_company_or_404(db, ticker)
    secured_pct = round(metrics.secured_debt / metrics.total_debt * 100, 1) if metrics.total_debt and metrics.secured_debt else 0

    return {
        "data": {
            "company": company_header(company),
            "debt_metrics": {
                "total_debt": metrics.total_debt, "secured_debt": metrics.secured_debt,
                "unsecured_debt": metrics.unsecured_debt, "net_debt": metrics.net_debt, "secured_percentage": secured_pct,
            },
            "structure_metrics": {
                "entity_count": metrics.entity_count, "guarantor_count": metrics.guarantor_count,
                "has_structural_subordination": metrics.has_structural_sub,
                "subordination_score": float(metrics.subordination_score) if metrics.subordination_score else None,
                "subordination_risk": metrics.subordination_risk, "has_unrestricted_subs": metrics.has_unrestricted_subs,
            },
            "maturity_profile": {
                "nearest_maturity": metrics.nearest_maturity.isoformat() if metrics.nearest_maturity else None,
                "debt_due_1yr": metrics.debt_due_1yr, "debt_due_2yr": metrics.debt_due_2yr, "debt_due_3yr": metrics.debt_due_3yr,
                "weighted_avg_maturity_years": float(metrics.weighted_avg_maturity) if metrics.weighted_avg_maturity else None,
                "has_near_term_maturity": metrics.has_near_term_maturity,
            },
            "risk_flags": {
                "has_holdco_debt": metrics.has_holdco_debt, "has_opco_debt": metrics.has_opco_debt,
                "has_floating_rate": metrics.has_floating_rate, "is_leveraged_loan": metrics.is_leveraged_loan,
                "is_covenant_lite": metrics.is_covenant_lite,
            },
            "ratings": {"sp_rating": metrics.sp_rating, "moodys_rating": metrics.moodys_rating, "rating_bucket": metrics.rating_bucket},
        },
    }


# =============================================================================
# COMPANY ENTITIES
# =============================================================================


@router.get("/companies/{ticker}/entities", tags=["Companies"])
async def get_company_entities(
    ticker: str,
    entity_type: Optional[str] = Query(None, description="Filter by entity type"),
    is_guarantor: Optional[bool] = Query(None, description="Filter by guarantor status"),
    has_debt: Optional[bool] = Query(None, description="Filter entities with/without debt"),
    db: AsyncSession = Depends(get_db),
):
    """List all entities with type, jurisdiction, guarantor status, and debt counts."""
    company = await get_company_or_404(db, ticker)

    query = select(Entity).where(Entity.company_id == company.id)
    if entity_type:
        query = query.where(Entity.entity_type == entity_type)
    if is_guarantor is not None:
        query = query.where(Entity.is_guarantor == is_guarantor)

    entities = (await db.execute(query.order_by(Entity.structure_tier, Entity.name))).scalars().all()

    # Get debt counts per entity
    debt_result = await db.execute(
        select(DebtInstrument.issuer_id, func.count(DebtInstrument.id), func.sum(DebtInstrument.outstanding))
        .where(DebtInstrument.company_id == company.id).group_by(DebtInstrument.issuer_id)
    )
    debt_counts = {row[0]: {"count": row[1], "total": row[2]} for row in debt_result}

    entity_list = []
    for e in entities:
        debt_info = debt_counts.get(e.id, {"count": 0, "total": 0})
        if has_debt is True and debt_info["count"] == 0:
            continue
        if has_debt is False and debt_info["count"] > 0:
            continue
        entity_list.append({
            "entity_id": str(e.id), "name": e.name, "entity_type": e.entity_type, "jurisdiction": e.jurisdiction,
            "parent_id": str(e.parent_id) if e.parent_id else None, "structure_tier": e.structure_tier,
            "is_guarantor": e.is_guarantor, "is_borrower": e.is_borrower,
            "is_restricted": e.is_restricted, "is_unrestricted": e.is_unrestricted,
            "debt_count": debt_info["count"], "total_debt_at_entity": debt_info["total"],
        })

    type_counts = {}
    for e in entity_list:
        type_counts[e["entity_type"]] = type_counts.get(e["entity_type"], 0) + 1

    return {
        "data": {
            "company": company_header(company),
            "entities": entity_list,
            "summary": {"total_entities": len(entity_list), "by_type": type_counts, "guarantors": sum(1 for e in entity_list if e["is_guarantor"])},
        },
    }


# =============================================================================
# COMPANY GUARANTEES
# =============================================================================


@router.get("/companies/{ticker}/guarantees", tags=["Companies"])
async def get_company_guarantees(ticker: str, db: AsyncSession = Depends(get_db)):
    """Get all guarantee relationships: debt instruments with guarantors and types."""
    company = await get_company_or_404(db, ticker)

    # Get all guarantees with related data
    result = await db.execute(
        select(Guarantee, DebtInstrument, Entity)
        .join(DebtInstrument, Guarantee.debt_instrument_id == DebtInstrument.id)
        .join(Entity, Guarantee.guarantor_id == Entity.id)
        .where(DebtInstrument.company_id == company.id)
        .order_by(DebtInstrument.name, Entity.name)
    )

    guarantees_data = []
    unique_guarantors = set()
    guaranteed_amount = 0

    for guarantee, debt, guarantor in result:
        unique_guarantors.add(guarantor.id)
        if debt.outstanding:
            guaranteed_amount += debt.outstanding

        # Get issuer name
        issuer_result = await db.execute(
            select(Entity).where(Entity.id == debt.issuer_id)
        )
        issuer = issuer_result.scalar_one_or_none()

        guarantees_data.append({
            "id": str(guarantee.id),
            "debt_instrument_id": str(debt.id),
            "debt_instrument_name": debt.name,
            "obligor": {
                "entity_id": str(debt.issuer_id),
                "name": issuer.name if issuer else None,
                "type": issuer.entity_type if issuer else None,
            },
            "guarantor": {
                "entity_id": str(guarantor.id),
                "name": guarantor.name,
                "type": guarantor.entity_type,
            },
            "guarantee_type": guarantee.guarantee_type,
            "limitation_amount": guarantee.limitation_amount,
        })

    return {
        "data": {
            "company": company_header(company),
            "guarantees": guarantees_data,
            "summary": {"total_guarantees": len(guarantees_data), "unique_guarantors": len(unique_guarantors), "guaranteed_debt_amount": guaranteed_amount},
        },
    }


# =============================================================================
# SEARCH COMPANIES
# =============================================================================


@router.get("/search/companies", tags=["Search"])
async def search_companies(
    q: Optional[str] = Query(None, description="Text search (name or ticker)"),
    sector: Optional[str] = Query(None, description="Filter by sector"),
    min_debt: Optional[int] = Query(None, description="Minimum total debt (cents)"),
    max_debt: Optional[int] = Query(None, description="Maximum total debt (cents)"),
    has_secured_debt: Optional[bool] = Query(None, description="Has secured debt"),
    has_structural_sub: Optional[bool] = Query(None, description="Has structural subordination"),
    has_near_term_maturity: Optional[bool] = Query(None, description="Has debt maturing within 24 months"),
    sort_by: str = Query("ticker", description="Sort field (ticker, total_debt, entity_count)"),
    sort_order: str = Query("asc", description="Sort order (asc, desc)"),
    limit: int = Query(50, ge=1, le=100, description="Number of results"),
    offset: int = Query(0, ge=0, description="Offset for pagination"),
    db: AsyncSession = Depends(get_db),
):
    """
    Search companies with rich filtering options.

    Supports text search, debt filtering, risk flags, and sorting.
    """
    # Build query
    query = select(CompanyMetrics, Company).join(
        Company, CompanyMetrics.company_id == Company.id
    )
    count_query = select(func.count()).select_from(CompanyMetrics)

    # Apply filters
    filters = []

    if q:
        q_upper = q.upper()
        filters.append(
            or_(
                CompanyMetrics.ticker.ilike(f"%{q}%"),
                Company.name.ilike(f"%{q}%"),
            )
        )

    if sector:
        filters.append(CompanyMetrics.sector == sector)

    if min_debt is not None:
        filters.append(CompanyMetrics.total_debt >= min_debt)

    if max_debt is not None:
        filters.append(CompanyMetrics.total_debt <= max_debt)

    if has_secured_debt is not None:
        if has_secured_debt:
            filters.append(CompanyMetrics.secured_debt > 0)
        else:
            filters.append(or_(CompanyMetrics.secured_debt == 0, CompanyMetrics.secured_debt.is_(None)))

    if has_structural_sub is not None:
        filters.append(CompanyMetrics.has_structural_sub == has_structural_sub)

    if has_near_term_maturity is not None:
        filters.append(CompanyMetrics.has_near_term_maturity == has_near_term_maturity)

    if filters:
        query = query.where(and_(*filters))
        # Update count query with same filters
        count_query = select(func.count()).select_from(CompanyMetrics).join(
            Company, CompanyMetrics.company_id == Company.id
        ).where(and_(*filters))

    # Get total count
    total = await db.scalar(count_query)

    # Apply sorting
    sort_column = {
        "ticker": CompanyMetrics.ticker,
        "total_debt": CompanyMetrics.total_debt,
        "entity_count": CompanyMetrics.entity_count,
        "sector": CompanyMetrics.sector,
    }.get(sort_by, CompanyMetrics.ticker)

    if sort_order == "desc":
        query = query.order_by(sort_column.desc())
    else:
        query = query.order_by(sort_column.asc())

    # Apply pagination
    query = query.offset(offset).limit(limit)

    result = await db.execute(query)
    rows = result.all()

    return {
        "data": {
            "results": [
                {
                    "ticker": m.ticker,
                    "name": c.name,
                    "sector": m.sector,
                    "industry": m.industry,
                    "total_debt": m.total_debt,
                    "secured_debt": m.secured_debt,
                    "entity_count": m.entity_count,
                    "has_structural_sub": m.has_structural_sub,
                    "subordination_risk": m.subordination_risk,
                    "nearest_maturity": m.nearest_maturity.isoformat() if m.nearest_maturity else None,
                }
                for m, c in rows
            ],
            "total": total,
            "filters_applied": {
                k: v for k, v in {
                    "q": q,
                    "sector": sector,
                    "min_debt": min_debt,
                    "max_debt": max_debt,
                    "has_secured_debt": has_secured_debt,
                    "has_structural_sub": has_structural_sub,
                }.items() if v is not None
            },
        },
        "meta": {
            "limit": limit,
            "offset": offset,
        },
    }


# =============================================================================
# SEARCH DEBT
# =============================================================================


@router.get("/search/debt", tags=["Search"])
async def search_debt(
    seniority: Optional[str] = Query(None, description="Filter by seniority (senior_secured, senior_unsecured, subordinated)"),
    security_type: Optional[str] = Query(None, description="Filter by security type (first_lien, second_lien, unsecured)"),
    instrument_type: Optional[str] = Query(None, description="Filter by instrument type (term_loan_b, senior_notes, etc.)"),
    min_rate: Optional[int] = Query(None, description="Minimum interest rate (basis points)"),
    max_rate: Optional[int] = Query(None, description="Maximum interest rate (basis points)"),
    maturity_before: Optional[date] = Query(None, description="Maturity date before (YYYY-MM-DD)"),
    maturity_after: Optional[date] = Query(None, description="Maturity date after (YYYY-MM-DD)"),
    rate_type: Optional[str] = Query(None, description="Rate type (fixed, floating)"),
    min_ytm_bps: Optional[int] = Query(None, description="Minimum yield to maturity (basis points)"),
    max_ytm_bps: Optional[int] = Query(None, description="Maximum yield to maturity (basis points)"),
    min_spread_bps: Optional[int] = Query(None, description="Minimum spread to treasury (basis points)"),
    max_spread_bps: Optional[int] = Query(None, description="Maximum spread to treasury (basis points)"),
    has_pricing: Optional[bool] = Query(None, description="Filter to bonds with/without pricing data"),
    # NEW FILTERS
    issuer_type: Optional[str] = Query(None, description="Filter by issuer entity type (holdco, opco, subsidiary, spv, finco)"),
    has_guarantors: Optional[bool] = Query(None, description="Filter debt with/without guarantors"),
    min_outstanding: Optional[int] = Query(None, description="Minimum outstanding amount (cents)"),
    max_outstanding: Optional[int] = Query(None, description="Maximum outstanding amount (cents)"),
    has_cusip: Optional[bool] = Query(None, description="Filter bonds with/without CUSIP (tradeable)"),
    currency: Optional[str] = Query(None, description="Filter by currency (USD, EUR, etc.)"),
    sector: Optional[str] = Query(None, description="Filter by company sector"),
    sort_by: str = Query("maturity_date", description="Sort field (maturity_date, interest_rate, outstanding, ytm, spread, issuer_type)"),
    sort_order: str = Query("asc", description="Sort order (asc, desc)"),
    limit: int = Query(50, ge=1, le=100, description="Number of results"),
    offset: int = Query(0, ge=0, description="Offset for pagination"),
    db: AsyncSession = Depends(get_db),
):
    """
    Search debt instruments across all companies.

    Supports filtering by seniority, rates, maturity, instrument type, pricing data,
    issuer entity type, guarantor presence, and more.
    """
    # Check which joins are needed
    pricing_filter_active = any([min_ytm_bps, max_ytm_bps, min_spread_bps, max_spread_bps, has_pricing])
    pricing_sort_active = sort_by in ["ytm", "spread"]
    needs_pricing = pricing_filter_active or pricing_sort_active
    needs_issuer = issuer_type is not None or sort_by == "issuer_type"
    needs_guarantor_check = has_guarantors is not None

    # Build base query with required joins
    query = select(DebtInstrument, Company, Entity).join(
        Company, DebtInstrument.company_id == Company.id
    ).join(
        Entity, DebtInstrument.issuer_id == Entity.id
    ).where(DebtInstrument.is_active == True)

    count_query = select(func.count()).select_from(DebtInstrument).join(
        Entity, DebtInstrument.issuer_id == Entity.id
    ).where(DebtInstrument.is_active == True)

    # Add pricing join if needed
    if needs_pricing:
        query = select(DebtInstrument, Company, Entity, BondPricing).join(
            Company, DebtInstrument.company_id == Company.id
        ).join(
            Entity, DebtInstrument.issuer_id == Entity.id
        ).outerjoin(
            BondPricing, DebtInstrument.id == BondPricing.debt_instrument_id
        ).where(DebtInstrument.is_active == True)

        count_query = select(func.count()).select_from(DebtInstrument).join(
            Entity, DebtInstrument.issuer_id == Entity.id
        ).outerjoin(
            BondPricing, DebtInstrument.id == BondPricing.debt_instrument_id
        ).where(DebtInstrument.is_active == True)

    # Apply filters
    filters = []

    if seniority:
        filters.append(DebtInstrument.seniority == seniority)

    if security_type:
        filters.append(DebtInstrument.security_type == security_type)

    if instrument_type:
        filters.append(DebtInstrument.instrument_type == instrument_type)

    if min_rate is not None:
        filters.append(DebtInstrument.interest_rate >= min_rate)

    if max_rate is not None:
        filters.append(DebtInstrument.interest_rate <= max_rate)

    if maturity_before:
        filters.append(DebtInstrument.maturity_date <= maturity_before)

    if maturity_after:
        filters.append(DebtInstrument.maturity_date >= maturity_after)

    if rate_type:
        filters.append(DebtInstrument.rate_type == rate_type)

    # NEW: Issuer type filter
    if issuer_type:
        filters.append(Entity.entity_type == issuer_type)

    # NEW: Outstanding amount filters
    if min_outstanding is not None:
        filters.append(DebtInstrument.outstanding >= min_outstanding)

    if max_outstanding is not None:
        filters.append(DebtInstrument.outstanding <= max_outstanding)

    # NEW: CUSIP filter (tradeable bonds)
    if has_cusip is True:
        filters.append(DebtInstrument.cusip.isnot(None))
    elif has_cusip is False:
        filters.append(DebtInstrument.cusip.is_(None))

    # NEW: Currency filter
    if currency:
        filters.append(DebtInstrument.currency == currency.upper())

    # NEW: Sector filter
    if sector:
        filters.append(Company.sector == sector)

    # Pricing filters
    if min_ytm_bps is not None:
        filters.append(BondPricing.ytm_bps >= min_ytm_bps)

    if max_ytm_bps is not None:
        filters.append(BondPricing.ytm_bps <= max_ytm_bps)

    if min_spread_bps is not None:
        filters.append(BondPricing.spread_to_treasury_bps >= min_spread_bps)

    if max_spread_bps is not None:
        filters.append(BondPricing.spread_to_treasury_bps <= max_spread_bps)

    if has_pricing is True:
        filters.append(BondPricing.last_price.isnot(None))
    elif has_pricing is False:
        filters.append(or_(BondPricing.last_price.is_(None), BondPricing.id.is_(None)))

    # NEW: Has guarantors filter (requires subquery)
    if has_guarantors is not None:
        guarantor_subq = select(Guarantee.debt_instrument_id).distinct()
        if has_guarantors:
            filters.append(DebtInstrument.id.in_(guarantor_subq))
        else:
            filters.append(DebtInstrument.id.notin_(guarantor_subq))

    if filters:
        query = query.where(and_(*filters))
        count_query = count_query.where(and_(*filters))

    # Get total count
    total = await db.scalar(count_query)

    # Apply sorting
    sort_column_map = {
        "maturity_date": DebtInstrument.maturity_date,
        "interest_rate": DebtInstrument.interest_rate,
        "outstanding": DebtInstrument.outstanding,
        "name": DebtInstrument.name,
        "issuer_type": Entity.entity_type,
    }

    # Add pricing sort columns if pricing is joined
    if needs_pricing:
        sort_column_map["ytm"] = BondPricing.ytm_bps
        sort_column_map["spread"] = BondPricing.spread_to_treasury_bps

    sort_column = sort_column_map.get(sort_by, DebtInstrument.maturity_date)

    if sort_order == "desc":
        query = query.order_by(sort_column.desc().nulls_last())
    else:
        query = query.order_by(sort_column.asc().nulls_last())

    # Apply pagination
    query = query.offset(offset).limit(limit)

    result = await db.execute(query)
    rows = result.all()

    # Build results - tuple structure: (DebtInstrument, Company, Entity, [BondPricing])
    results = []
    for row in rows:
        if needs_pricing:
            d, c, issuer, pricing = row
        else:
            d, c, issuer = row
            pricing = None

        item = {
            "id": str(d.id),
            "name": d.name,
            "cusip": d.cusip,
            "company_ticker": c.ticker,
            "company_name": c.name,
            "company_sector": c.sector,
            "issuer": {
                "entity_id": str(issuer.id),
                "name": issuer.name,
                "type": issuer.entity_type,
            },
            "instrument_type": d.instrument_type,
            "seniority": d.seniority,
            "security_type": d.security_type,
            "outstanding": d.outstanding,
            "currency": d.currency,
            "rate_type": d.rate_type,
            "interest_rate": d.interest_rate,
            "spread_bps": d.spread_bps,
            "benchmark": d.benchmark,
            "maturity_date": d.maturity_date.isoformat() if d.maturity_date else None,
        }

        # Include pricing data if available
        if needs_pricing:
            item["pricing"] = {
                "last_price": float(pricing.last_price) if pricing and pricing.last_price else None,
                "ytm_pct": pricing.ytm_bps / 100 if pricing and pricing.ytm_bps else None,
                "ytm_bps": pricing.ytm_bps if pricing else None,
                "spread_to_treasury_bps": pricing.spread_to_treasury_bps if pricing else None,
                "treasury_benchmark": pricing.treasury_benchmark if pricing else None,
                "price_source": pricing.price_source if pricing else None,
                "staleness_days": pricing.staleness_days if pricing else None,
            } if pricing else None

        results.append(item)

    # Build filters_applied dict
    filters_applied = {
        k: v for k, v in {
            "seniority": seniority,
            "security_type": security_type,
            "instrument_type": instrument_type,
            "min_rate": min_rate,
            "max_rate": max_rate,
            "maturity_before": maturity_before.isoformat() if maturity_before else None,
            "maturity_after": maturity_after.isoformat() if maturity_after else None,
            "rate_type": rate_type,
            "min_ytm_bps": min_ytm_bps,
            "max_ytm_bps": max_ytm_bps,
            "min_spread_bps": min_spread_bps,
            "max_spread_bps": max_spread_bps,
            "has_pricing": has_pricing,
            "issuer_type": issuer_type,
            "has_guarantors": has_guarantors,
            "min_outstanding": min_outstanding,
            "max_outstanding": max_outstanding,
            "has_cusip": has_cusip,
            "currency": currency,
            "sector": sector,
        }.items() if v is not None
    }

    return {
        "data": {
            "results": results,
            "total": total,
            "filters_applied": filters_applied,
        },
        "meta": {
            "limit": limit,
            "offset": offset,
        },
    }


# =============================================================================
# SEARCH ENTITIES (Cross-company)
# =============================================================================


@router.get("/search/entities", tags=["Search"])
async def search_entities(
    entity_type: Optional[str] = Query(None, description="Filter by type (holdco, opco, subsidiary, spv, jv, finco, vie)"),
    jurisdiction: Optional[str] = Query(None, description="Filter by jurisdiction (e.g., Delaware, Cayman Islands)"),
    is_guarantor: Optional[bool] = Query(None, description="Filter by guarantor status"),
    is_vie: Optional[bool] = Query(None, description="Filter by VIE status"),
    is_unrestricted: Optional[bool] = Query(None, description="Filter by unrestricted subsidiary status"),
    is_borrower: Optional[bool] = Query(None, description="Filter by borrower status"),
    has_debt: Optional[bool] = Query(None, description="Filter entities with/without debt issued"),
    q: Optional[str] = Query(None, description="Text search on entity name"),
    sort_by: str = Query("name", description="Sort field (name, entity_type, jurisdiction)"),
    sort_order: str = Query("asc", description="Sort order (asc, desc)"),
    limit: int = Query(50, ge=1, le=100, description="Number of results"),
    offset: int = Query(0, ge=0, description="Offset for pagination"),
    db: AsyncSession = Depends(get_db),
):
    """
    Search entities across ALL companies.

    Enables cross-company analysis like "find all VIEs" or "all Delaware SPVs".
    """
    # Build query with company join
    query = select(Entity, Company).join(Company, Entity.company_id == Company.id)
    count_query = select(func.count()).select_from(Entity)

    filters = []

    if entity_type:
        filters.append(Entity.entity_type == entity_type)

    if jurisdiction:
        filters.append(Entity.jurisdiction.ilike(f"%{jurisdiction}%"))

    if is_guarantor is not None:
        filters.append(Entity.is_guarantor == is_guarantor)

    if is_vie is not None:
        filters.append(Entity.is_vie == is_vie)

    if is_unrestricted is not None:
        filters.append(Entity.is_unrestricted == is_unrestricted)

    if is_borrower is not None:
        filters.append(Entity.is_borrower == is_borrower)

    if q:
        filters.append(Entity.name.ilike(f"%{q}%"))

    if filters:
        query = query.where(and_(*filters))
        count_query = count_query.where(and_(*filters))

    # Handle has_debt filter separately (requires subquery)
    if has_debt is not None:
        debt_subq = select(DebtInstrument.issuer_id).distinct()
        if has_debt:
            query = query.where(Entity.id.in_(debt_subq))
            count_query = count_query.where(Entity.id.in_(debt_subq))
        else:
            query = query.where(Entity.id.notin_(debt_subq))
            count_query = count_query.where(Entity.id.notin_(debt_subq))

    # Get total count
    total = await db.scalar(count_query)

    # Apply sorting
    sort_column = {
        "name": Entity.name,
        "entity_type": Entity.entity_type,
        "jurisdiction": Entity.jurisdiction,
    }.get(sort_by, Entity.name)

    if sort_order == "desc":
        query = query.order_by(sort_column.desc().nulls_last())
    else:
        query = query.order_by(sort_column.asc().nulls_last())

    query = query.offset(offset).limit(limit)

    result = await db.execute(query)
    rows = result.all()

    # Get debt counts for returned entities
    entity_ids = [e.id for e, c in rows]
    if entity_ids:
        debt_counts_result = await db.execute(
            select(DebtInstrument.issuer_id, func.count(DebtInstrument.id), func.sum(DebtInstrument.outstanding))
            .where(DebtInstrument.issuer_id.in_(entity_ids))
            .group_by(DebtInstrument.issuer_id)
        )
        debt_counts = {row[0]: {"count": row[1], "total": row[2]} for row in debt_counts_result}
    else:
        debt_counts = {}

    results = []
    for e, c in rows:
        debt_info = debt_counts.get(e.id, {"count": 0, "total": 0})
        results.append({
            "entity_id": str(e.id),
            "name": e.name,
            "entity_type": e.entity_type,
            "jurisdiction": e.jurisdiction,
            "formation_type": e.formation_type,
            "company": {"ticker": c.ticker, "name": c.name},
            "is_guarantor": e.is_guarantor,
            "is_borrower": e.is_borrower,
            "is_vie": e.is_vie,
            "is_unrestricted": e.is_unrestricted,
            "is_restricted": e.is_restricted,
            "structure_tier": e.structure_tier,
            "debt_issued": {"count": debt_info["count"], "total_outstanding": debt_info["total"]},
        })

    # Summary statistics
    type_counts = {}
    jurisdiction_counts = {}
    for r in results:
        type_counts[r["entity_type"]] = type_counts.get(r["entity_type"], 0) + 1
        if r["jurisdiction"]:
            jurisdiction_counts[r["jurisdiction"]] = jurisdiction_counts.get(r["jurisdiction"], 0) + 1

    return {
        "data": {
            "results": results,
            "total": total,
            "summary": {
                "by_type": type_counts,
                "by_jurisdiction": dict(sorted(jurisdiction_counts.items(), key=lambda x: -x[1])[:10]),
                "guarantors": sum(1 for r in results if r["is_guarantor"]),
                "vies": sum(1 for r in results if r["is_vie"]),
                "with_debt": sum(1 for r in results if r["debt_issued"]["count"] > 0),
            },
            "filters_applied": {k: v for k, v in {
                "entity_type": entity_type, "jurisdiction": jurisdiction, "is_guarantor": is_guarantor,
                "is_vie": is_vie, "is_unrestricted": is_unrestricted, "is_borrower": is_borrower,
                "has_debt": has_debt, "q": q,
            }.items() if v is not None},
        },
        "meta": {"limit": limit, "offset": offset},
    }


# =============================================================================
# COMPANY OWNERSHIP (OwnershipLink exposure)
# =============================================================================


@router.get("/companies/{ticker}/ownership", tags=["Companies"])
async def get_company_ownership(ticker: str, db: AsyncSession = Depends(get_db)):
    """
    Get complex ownership relationships: JVs, multiple parents, ownership types.

    Exposes the OwnershipLink table which tracks:
    - Joint ventures and JV partners
    - Partial ownership relationships
    - Economic vs voting ownership
    - Consolidation methods
    """
    company = await get_company_or_404(db, ticker)

    # Get all entities for this company
    entities_result = await db.execute(
        select(Entity).where(Entity.company_id == company.id)
    )
    entities = {e.id: e for e in entities_result.scalars().all()}
    entity_ids = list(entities.keys())

    if not entity_ids:
        return {
            "data": {
                "company": company_header(company),
                "ownership_links": [],
                "joint_ventures": [],
                "summary": {"total_links": 0, "joint_ventures": 0},
            }
        }

    # Get ownership links where either parent or child is in this company
    links_result = await db.execute(
        select(OwnershipLink).where(
            or_(
                OwnershipLink.parent_entity_id.in_(entity_ids),
                OwnershipLink.child_entity_id.in_(entity_ids),
            )
        ).order_by(OwnershipLink.parent_entity_id)
    )
    links = links_result.scalars().all()

    ownership_links = []
    joint_ventures = []

    for link in links:
        parent = entities.get(link.parent_entity_id)
        child = entities.get(link.child_entity_id)

        link_data = {
            "id": str(link.id),
            "parent": {
                "entity_id": str(link.parent_entity_id),
                "name": parent.name if parent else "External Entity",
                "type": parent.entity_type if parent else None,
            },
            "child": {
                "entity_id": str(link.child_entity_id),
                "name": child.name if child else "External Entity",
                "type": child.entity_type if child else None,
            },
            "ownership_pct": float(link.ownership_pct) if link.ownership_pct else None,
            "ownership_type": link.ownership_type,
            "consolidation_method": link.consolidation_method,
            "effective_from": link.effective_from.isoformat() if link.effective_from else None,
            "effective_to": link.effective_to.isoformat() if link.effective_to else None,
            "is_current": link.effective_to is None,
        }

        ownership_links.append(link_data)

        if link.is_joint_venture:
            jv_data = {
                **link_data,
                "jv_partner_name": link.jv_partner_name,
            }
            joint_ventures.append(jv_data)

    # Also include simple parent-child relationships from Entity.parent_id
    simple_ownership = []
    for entity in entities.values():
        if entity.parent_id and entity.parent_id in entities:
            parent = entities[entity.parent_id]
            simple_ownership.append({
                "parent": {"entity_id": str(parent.id), "name": parent.name, "type": parent.entity_type},
                "child": {"entity_id": str(entity.id), "name": entity.name, "type": entity.entity_type},
                "ownership_pct": float(entity.ownership_pct) if entity.ownership_pct else 100.0,
                "ownership_type": "direct",
                "source": "entity_hierarchy",
            })

    return {
        "data": {
            "company": company_header(company),
            "ownership_links": ownership_links,
            "simple_hierarchy": simple_ownership,
            "joint_ventures": joint_ventures,
            "summary": {
                "total_complex_links": len(ownership_links),
                "total_simple_links": len(simple_ownership),
                "joint_ventures": len(joint_ventures),
                "partial_ownership": sum(1 for l in ownership_links if l["ownership_pct"] and l["ownership_pct"] < 100),
            },
        },
    }


# =============================================================================
# COMPANY HIERARCHY (Tree View)
# =============================================================================


@router.get("/companies/{ticker}/hierarchy", tags=["Companies"])
async def get_company_hierarchy(ticker: str, db: AsyncSession = Depends(get_db)):
    """
    Get corporate structure as a nested tree.

    More intuitive than flat entity list - shows parent→child relationships
    with debt amounts at each level.
    """
    company = await get_company_or_404(db, ticker)

    # Get all entities
    entities_result = await db.execute(
        select(Entity).where(Entity.company_id == company.id)
    )
    entities = list(entities_result.scalars().all())

    # Get debt totals by issuer
    debt_result = await db.execute(
        select(DebtInstrument.issuer_id, func.sum(DebtInstrument.outstanding), func.count(DebtInstrument.id))
        .where(DebtInstrument.company_id == company.id)
        .group_by(DebtInstrument.issuer_id)
    )
    debt_by_entity = {row[0]: {"total": row[1], "count": row[2]} for row in debt_result}

    # Build entity lookup
    entity_map = {e.id: e for e in entities}

    # Find roots (entities without parents or parent outside company)
    roots = [e for e in entities if not e.parent_id or e.parent_id not in entity_map]

    def build_node(entity):
        debt_info = debt_by_entity.get(entity.id, {"total": 0, "count": 0})
        children = [e for e in entities if e.parent_id == entity.id]

        node = {
            "entity_id": str(entity.id),
            "name": entity.name,
            "entity_type": entity.entity_type,
            "jurisdiction": entity.jurisdiction,
            "is_guarantor": entity.is_guarantor,
            "is_borrower": entity.is_borrower,
            "is_vie": entity.is_vie,
            "debt_at_entity": {
                "total_outstanding": debt_info["total"],
                "instrument_count": debt_info["count"],
            },
            "children": [build_node(c) for c in sorted(children, key=lambda x: x.name)],
        }
        return node

    # Build tree from roots
    tree = [build_node(r) for r in sorted(roots, key=lambda x: (x.entity_type != "holdco", x.name))]

    # Calculate totals
    def sum_debt(node):
        total = node["debt_at_entity"]["total_outstanding"] or 0
        for child in node["children"]:
            total += sum_debt(child)
        return total

    total_debt = sum(sum_debt(n) for n in tree)

    return {
        "data": {
            "company": company_header(company),
            "hierarchy": tree,
            "summary": {
                "total_entities": len(entities),
                "root_entities": len(roots),
                "max_depth": max((lambda n, d=0: max(d, max((lambda c: sum_debt(c))(c) for c in n["children"]) if n["children"] else d))(n) for n in tree) if tree else 0,
                "total_debt": total_debt,
                "entities_with_debt": len([e for e in entities if e.id in debt_by_entity]),
            },
        },
    }


# =============================================================================
# ANALYTICS - SECTOR SUMMARY
# =============================================================================


@router.get("/analytics/sectors", tags=["Analytics"])
async def get_sector_analytics(
    sector: Optional[str] = Query(None, description="Filter to specific sector"),
    db: AsyncSession = Depends(get_db),
):
    """
    Get sector-level analytics: average leverage, total debt, entity counts.

    Without sector parameter: returns summary for all sectors.
    With sector parameter: returns detailed breakdown for that sector.
    """
    if sector:
        # Detailed view for specific sector
        result = await db.execute(
            select(CompanyMetrics, Company)
            .join(Company, CompanyMetrics.company_id == Company.id)
            .where(CompanyMetrics.sector == sector)
            .order_by(CompanyMetrics.total_debt.desc().nulls_last())
        )
        rows = result.all()

        if not rows:
            raise HTTPException(status_code=404, detail=f"Sector '{sector}' not found")

        companies = []
        total_debt = 0
        total_secured = 0
        leverage_values = []
        coverage_values = []

        for m, c in rows:
            companies.append({
                "ticker": m.ticker,
                "name": c.name,
                "total_debt": m.total_debt,
                "secured_debt": m.secured_debt,
                "leverage_ratio": float(m.leverage_ratio) if m.leverage_ratio else None,
                "interest_coverage": float(m.interest_coverage) if m.interest_coverage else None,
                "subordination_risk": m.subordination_risk,
                "entity_count": m.entity_count,
            })
            if m.total_debt:
                total_debt += m.total_debt
            if m.secured_debt:
                total_secured += m.secured_debt
            if m.leverage_ratio:
                leverage_values.append(float(m.leverage_ratio))
            if m.interest_coverage:
                coverage_values.append(float(m.interest_coverage))

        return {
            "data": {
                "sector": sector,
                "companies": companies,
                "aggregates": {
                    "company_count": len(companies),
                    "total_debt": total_debt,
                    "total_secured_debt": total_secured,
                    "avg_leverage_ratio": round(sum(leverage_values) / len(leverage_values), 2) if leverage_values else None,
                    "median_leverage_ratio": round(sorted(leverage_values)[len(leverage_values) // 2], 2) if leverage_values else None,
                    "avg_interest_coverage": round(sum(coverage_values) / len(coverage_values), 2) if coverage_values else None,
                    "high_risk_count": sum(1 for c in companies if c["subordination_risk"] == "high"),
                },
            },
        }
    else:
        # Summary view for all sectors
        result = await db.execute(
            select(
                CompanyMetrics.sector,
                func.count(CompanyMetrics.ticker).label("company_count"),
                func.sum(CompanyMetrics.total_debt).label("total_debt"),
                func.sum(CompanyMetrics.secured_debt).label("secured_debt"),
                func.avg(CompanyMetrics.leverage_ratio).label("avg_leverage"),
                func.sum(CompanyMetrics.entity_count).label("total_entities"),
            )
            .where(CompanyMetrics.sector.isnot(None))
            .group_by(CompanyMetrics.sector)
            .order_by(func.sum(CompanyMetrics.total_debt).desc().nulls_last())
        )
        rows = result.all()

        sectors = []
        for row in rows:
            sectors.append({
                "sector": row[0],
                "company_count": row[1],
                "total_debt": row[2],
                "secured_debt": row[3],
                "avg_leverage_ratio": round(float(row[4]), 2) if row[4] else None,
                "total_entities": row[5],
            })

        return {
            "data": {
                "sectors": sectors,
                "totals": {
                    "sector_count": len(sectors),
                    "total_companies": sum(s["company_count"] for s in sectors),
                    "total_debt": sum(s["total_debt"] or 0 for s in sectors),
                },
            },
        }


# =============================================================================
# COMPARE COMPANIES
# =============================================================================


@router.get("/compare/companies", tags=["Analytics"])
async def compare_companies(
    tickers: str = Query(..., description="Comma-separated tickers (e.g., 'RIG,VAL,DO')"),
    db: AsyncSession = Depends(get_db),
):
    """
    Compare multiple companies side-by-side.

    Returns key metrics for each company and aggregate statistics.
    """
    ticker_list = [t.strip().upper() for t in tickers.split(",")]

    if len(ticker_list) < 2:
        raise HTTPException(status_code=400, detail="At least 2 tickers required for comparison")

    if len(ticker_list) > 10:
        raise HTTPException(status_code=400, detail="Maximum 10 tickers allowed")

    # Get metrics for all tickers
    result = await db.execute(
        select(CompanyMetrics, Company)
        .join(Company, CompanyMetrics.company_id == Company.id)
        .where(CompanyMetrics.ticker.in_(ticker_list))
    )
    rows = result.all()

    if not rows:
        raise HTTPException(status_code=404, detail="No companies found")

    comparison = []
    total_debt_sum = 0
    total_entities = 0
    secured_pcts = []

    for m, c in rows:
        secured_pct = round((m.secured_debt / m.total_debt * 100), 1) if m.total_debt and m.secured_debt else 0

        comparison.append({
            "ticker": m.ticker,
            "name": c.name,
            "sector": m.sector,
            "total_debt": m.total_debt,
            "secured_debt": m.secured_debt,
            "secured_percentage": secured_pct,
            "entity_count": m.entity_count,
            "guarantor_count": m.guarantor_count,
            "has_structural_sub": m.has_structural_sub,
            "subordination_risk": m.subordination_risk,
            "nearest_maturity": m.nearest_maturity.isoformat() if m.nearest_maturity else None,
            "has_floating_rate": m.has_floating_rate,
        })

        if m.total_debt:
            total_debt_sum += m.total_debt
        if m.entity_count:
            total_entities += m.entity_count
        secured_pcts.append(secured_pct)

    # Calculate aggregates
    count = len(comparison)

    return {
        "data": {
            "comparison": comparison,
            "aggregates": {
                "company_count": count,
                "avg_total_debt": total_debt_sum // count if count else 0,
                "avg_entity_count": total_entities // count if count else 0,
                "avg_secured_percentage": round(sum(secured_pcts) / count, 1) if count else 0,
                "companies_with_structural_sub": sum(1 for c in comparison if c["has_structural_sub"]),
            },
            "requested_tickers": ticker_list,
            "found_tickers": [c["ticker"] for c in comparison],
            "missing_tickers": [t for t in ticker_list if t not in [c["ticker"] for c in comparison]],
        },
    }


# =============================================================================
# API STATUS
# =============================================================================


@router.get("/status", tags=["System"])
async def get_status(db: AsyncSession = Depends(get_db)):
    """
    Get API status and data coverage statistics.

    Returns counts of companies, entities, debt instruments, and data freshness info.
    """
    # Get counts
    company_count = await db.scalar(select(func.count()).select_from(Company))
    entity_count = await db.scalar(select(func.count()).select_from(Entity))
    debt_count = await db.scalar(select(func.count()).select_from(DebtInstrument))
    guarantee_count = await db.scalar(select(func.count()).select_from(Guarantee))

    # Get sector breakdown
    sector_result = await db.execute(
        select(
            CompanyMetrics.sector,
            func.count(CompanyMetrics.ticker).label("count"),
        )
        .where(CompanyMetrics.sector.isnot(None))
        .group_by(CompanyMetrics.sector)
        .order_by(func.count(CompanyMetrics.ticker).desc())
    )
    sectors = {row[0]: row[1] for row in sector_result}

    # Get latest update
    latest_result = await db.execute(
        select(func.max(CompanyCache.computed_at))
    )
    latest_update = latest_result.scalar()

    # Get avg QA score if we had it (placeholder for now)
    avg_qa = None

    return {
        "data": {
            "status": "operational",
            "version": "1.0.0",
            "data_coverage": {
                "total_companies": company_count,
                "total_entities": entity_count,
                "total_debt_instruments": debt_count,
                "total_guarantees": guarantee_count,
            },
            "by_sector": sectors,
            "data_freshness": {
                "last_update": latest_update.isoformat() + "Z" if latest_update else None,
            },
        },
    }


# =============================================================================
# ENTITY DETAIL
# =============================================================================


@router.get("/companies/{ticker}/entities/{entity_id}", tags=["Companies"])
async def get_entity_detail(ticker: str, entity_id: str, db: AsyncSession = Depends(get_db)):
    """Get entity details including parent, children, debt issued, and guarantees."""
    company = await get_company_or_404(db, ticker)
    entity_uuid = parse_uuid_or_400(entity_id, "entity ID")

    entity_result = await db.execute(select(Entity).where(Entity.id == entity_uuid, Entity.company_id == company.id))
    entity = entity_result.scalar_one_or_none()
    if not entity:
        raise HTTPException(status_code=404, detail=f"Entity {entity_id} not found")

    # Get parent
    parent_data = None
    if entity.parent_id:
        parent = (await db.execute(select(Entity).where(Entity.id == entity.parent_id))).scalar_one_or_none()
        if parent:
            parent_data = {"entity_id": str(parent.id), "name": parent.name, "ownership_percentage": float(entity.ownership_pct) if entity.ownership_pct else 100.0}

    # Get children
    children = [{"entity_id": str(c.id), "name": c.name, "type": c.entity_type} for c in (await db.execute(select(Entity).where(Entity.parent_id == entity.id))).scalars()]

    # Get debt at entity
    debts = (await db.execute(select(DebtInstrument).where(DebtInstrument.issuer_id == entity.id))).scalars().all()
    debt_total = sum(d.outstanding or 0 for d in debts)
    secured_total = sum(d.outstanding or 0 for d in debts if d.seniority == "senior_secured")

    # Get guarantees provided
    guarantees_provided = [{"debt_instrument_id": str(d.id), "debt_instrument_name": d.name, "guarantee_type": g.guarantee_type}
                          for g, d in await db.execute(select(Guarantee, DebtInstrument).join(DebtInstrument, Guarantee.debt_instrument_id == DebtInstrument.id).where(Guarantee.guarantor_id == entity.id))]

    return {
        "data": {
            "company": company_header(company),
            "entity": {
                "entity_id": str(entity.id), "name": entity.name, "legal_name": entity.legal_name,
                "entity_type": entity.entity_type, "jurisdiction": entity.jurisdiction, "formation_type": entity.formation_type,
                "structure_tier": entity.structure_tier, "is_guarantor": entity.is_guarantor, "is_borrower": entity.is_borrower,
                "is_restricted": entity.is_restricted, "is_unrestricted": entity.is_unrestricted, "is_vie": entity.is_vie,
                "consolidation_method": entity.consolidation_method, "parent": parent_data,
                "debt_at_entity": {"count": len(debts), "total": debt_total, "secured": secured_total, "unsecured": debt_total - secured_total},
                "guarantees_provided": guarantees_provided, "children": children,
            },
        },
    }


# =============================================================================
# DEBT INSTRUMENT DETAIL
# =============================================================================


@router.get("/companies/{ticker}/debt/{debt_id}", tags=["Companies"])
async def get_debt_detail(ticker: str, debt_id: str, db: AsyncSession = Depends(get_db)):
    """Get detailed debt instrument info with full terms, issuer, and guarantors."""
    company = await get_company_or_404(db, ticker)
    debt_uuid = parse_uuid_or_400(debt_id, "debt ID")

    debt = (await db.execute(select(DebtInstrument).where(DebtInstrument.id == debt_uuid, DebtInstrument.company_id == company.id))).scalar_one_or_none()
    if not debt:
        raise HTTPException(status_code=404, detail=f"Debt instrument {debt_id} not found")

    issuer = (await db.execute(select(Entity).where(Entity.id == debt.issuer_id))).scalar_one_or_none()
    guarantors = [{"entity_id": str(e.id), "name": e.name, "guarantee_type": g.guarantee_type, "limitation_amount": g.limitation_amount}
                  for g, e in await db.execute(select(Guarantee, Entity).join(Entity, Guarantee.guarantor_id == Entity.id).where(Guarantee.debt_instrument_id == debt.id))]

    return {
        "data": {
            "company": company_header(company),
            "debt_instrument": {
                "id": str(debt.id), "name": debt.name,
                "issuer": {"entity_id": str(issuer.id) if issuer else None, "name": issuer.name if issuer else None, "type": issuer.entity_type if issuer else None},
                "instrument_type": debt.instrument_type, "seniority": debt.seniority, "security_type": debt.security_type,
                "commitment": debt.commitment, "principal": debt.principal, "outstanding": debt.outstanding, "currency": debt.currency,
                "rate_type": debt.rate_type, "interest_rate": debt.interest_rate, "spread_bps": debt.spread_bps,
                "benchmark": debt.benchmark, "floor_bps": debt.floor_bps,
                "issue_date": debt.issue_date.isoformat() if debt.issue_date else None,
                "maturity_date": debt.maturity_date.isoformat() if debt.maturity_date else None,
                "is_drawn": debt.is_drawn, "is_active": debt.is_active, "guarantors": guarantors, "attributes": debt.attributes,
            },
        },
    }


# =============================================================================
# ENTITY DEBT
# =============================================================================


@router.get("/companies/{ticker}/entities/{entity_id}/debt", tags=["Companies"])
async def get_entity_debt(ticker: str, entity_id: str, db: AsyncSession = Depends(get_db)):
    """Get debt instruments issued at a specific entity."""
    company = await get_company_or_404(db, ticker)
    entity_uuid = parse_uuid_or_400(entity_id, "entity ID")

    entity = (await db.execute(select(Entity).where(Entity.id == entity_uuid, Entity.company_id == company.id))).scalar_one_or_none()
    if not entity:
        raise HTTPException(status_code=404, detail=f"Entity {entity_id} not found")

    debts = (await db.execute(select(DebtInstrument).where(DebtInstrument.issuer_id == entity.id))).scalars().all()
    total = sum(d.outstanding or 0 for d in debts)

    return {
        "data": {
            "company": company_header(company),
            "entity": {"entity_id": str(entity.id), "name": entity.name, "type": entity.entity_type},
            "debt_instruments": [{"id": str(d.id), "name": d.name, "instrument_type": d.instrument_type, "seniority": d.seniority, "outstanding": d.outstanding, "maturity_date": d.maturity_date.isoformat() if d.maturity_date else None} for d in debts],
            "summary": {"total_debt": total, "instrument_count": len(debts),
            },
        },
    }


# =============================================================================
# COMPANY FINANCIALS
# =============================================================================


@router.get("/companies/{ticker}/financials", tags=["Companies"])
async def get_company_financials(ticker: str, quarters: int = Query(4, ge=1, le=20), db: AsyncSession = Depends(get_db)):
    """Get quarterly financial statement data (income statement, balance sheet, cash flow)."""
    company = await get_company_or_404(db, ticker)

    financials = (await db.execute(
        select(CompanyFinancials).where(CompanyFinancials.company_id == company.id)
        .order_by(CompanyFinancials.fiscal_year.desc(), CompanyFinancials.fiscal_quarter.desc()).limit(quarters)
    )).scalars().all()

    if not financials:
        raise HTTPException(status_code=404, detail=f"No financial data available for {ticker.upper()}")

    def fmt(f):
        return {
            "period": {"fiscal_year": f.fiscal_year, "fiscal_quarter": f.fiscal_quarter, "period_end_date": f.period_end_date.isoformat() if f.period_end_date else None, "filing_type": f.filing_type},
            "income_statement": {"revenue": f.revenue, "cost_of_revenue": f.cost_of_revenue, "gross_profit": f.gross_profit, "operating_income": f.operating_income, "ebitda": f.ebitda, "interest_expense": f.interest_expense, "net_income": f.net_income, "depreciation_amortization": f.depreciation_amortization},
            "balance_sheet": {"cash_and_equivalents": f.cash_and_equivalents, "total_current_assets": f.total_current_assets, "total_assets": f.total_assets, "total_current_liabilities": f.total_current_liabilities, "total_debt": f.total_debt, "total_liabilities": f.total_liabilities, "stockholders_equity": f.stockholders_equity},
            "cash_flow": {"operating_cash_flow": f.operating_cash_flow, "investing_cash_flow": f.investing_cash_flow, "financing_cash_flow": f.financing_cash_flow, "capex": f.capex},
            "metadata": {"source_filing": f.source_filing, "extracted_at": f.extracted_at.isoformat() + "Z" if f.extracted_at else None},
        }

    return {
        "data": {
            "company": company_header(company),
            "financials": [fmt(f) for f in financials],
            "summary": {"quarters_available": len(financials), "latest_period": f"Q{financials[0].fiscal_quarter} {financials[0].fiscal_year}" if financials else None},
        },
    }


@router.get("/companies/{ticker}/ratios", tags=["Companies"])
async def get_company_ratios(ticker: str, db: AsyncSession = Depends(get_db)):
    """Get computed credit ratios: leverage, interest coverage, margins."""
    company = await get_company_or_404(db, ticker)

    latest = (await db.execute(
        select(CompanyFinancials).where(CompanyFinancials.company_id == company.id)
        .order_by(CompanyFinancials.fiscal_year.desc(), CompanyFinancials.fiscal_quarter.desc()).limit(1)
    )).scalar_one_or_none()

    if not latest:
        raise HTTPException(status_code=404, detail=f"No financial data available for {ticker.upper()}")

    # Helper for safe division
    def ratio(num, denom, mult=1): return round(num / denom * mult, 2) if num and denom and denom != 0 else None

    cash = latest.cash_and_equivalents or 0
    net_debt = (latest.total_debt - cash) if latest.total_debt else None

    return {
        "data": {
            "company": company_header(company),
            "period": {"fiscal_year": latest.fiscal_year, "fiscal_quarter": latest.fiscal_quarter, "period_end_date": latest.period_end_date.isoformat() if latest.period_end_date else None},
            "credit_ratios": {
                "leverage_ratio": ratio(latest.total_debt, latest.ebitda),
                "net_leverage_ratio": ratio(net_debt, latest.ebitda),
                "interest_coverage_ratio": ratio(latest.ebitda, latest.interest_expense),
                "debt_to_equity_ratio": ratio(latest.total_debt, latest.stockholders_equity),
            },
            "liquidity_ratios": {"current_ratio": ratio(latest.total_current_assets, latest.total_current_liabilities), "net_debt": net_debt},
            "profitability_ratios": {
                "gross_margin_pct": ratio(latest.gross_profit, latest.revenue, 100),
                "operating_margin_pct": ratio(latest.operating_income, latest.revenue, 100),
                "net_margin_pct": ratio(latest.net_income, latest.revenue, 100),
            },
            "underlying_data": {"ebitda": latest.ebitda, "total_debt": latest.total_debt, "cash_and_equivalents": latest.cash_and_equivalents, "interest_expense": latest.interest_expense, "stockholders_equity": latest.stockholders_equity},
        },
    }


# =============================================================================
# OBLIGOR GROUP FINANCIALS (SEC Rule 13-01)
# =============================================================================


@router.get("/companies/{ticker}/obligor-group", tags=["Companies"])
async def get_obligor_group_financials(ticker: str, quarters: int = Query(4, ge=1, le=20), db: AsyncSession = Depends(get_db)):
    """Get SEC Rule 13-01 Obligor Group data with asset leakage metrics."""
    company = await get_company_or_404(db, ticker)

    og_data = (await db.execute(
        select(ObligorGroupFinancials).where(ObligorGroupFinancials.company_id == company.id)
        .order_by(ObligorGroupFinancials.fiscal_year.desc(), ObligorGroupFinancials.fiscal_quarter.desc()).limit(quarters)
    )).scalars().all()

    if not og_data:
        raise HTTPException(status_code=404, detail=f"No Rule 13-01 Obligor Group data for {ticker.upper()}. Company may not have guaranteed debt.")

    def fmt(og):
        return {
            "period": {"fiscal_year": og.fiscal_year, "fiscal_quarter": og.fiscal_quarter, "period_end_date": og.period_end_date.isoformat() if og.period_end_date else None, "filing_type": og.filing_type},
            "disclosure_info": {"note_number": og.disclosure_note_number, "debt_description": og.debt_description, "related_debt_ids": og.related_debt_ids},
            "obligor_group": {"total_assets": og.og_total_assets, "total_liabilities": og.og_total_liabilities, "stockholders_equity": og.og_stockholders_equity, "intercompany_receivables": og.og_intercompany_receivables, "revenue": og.og_revenue, "operating_income": og.og_operating_income, "ebitda": og.og_ebitda, "net_income": og.og_net_income},
            "consolidated": {"total_assets": og.consolidated_total_assets, "revenue": og.consolidated_revenue, "ebitda": og.consolidated_ebitda},
            "non_guarantor_subsidiaries": {"assets": og.non_guarantor_assets, "revenue": og.non_guarantor_revenue},
            "leakage_metrics": {"asset_leakage_pct": float(og.asset_leakage_pct) if og.asset_leakage_pct else None, "revenue_leakage_pct": float(og.revenue_leakage_pct) if og.revenue_leakage_pct else None, "ebitda_leakage_pct": float(og.ebitda_leakage_pct) if og.ebitda_leakage_pct else None},
            "metadata": {"source_filing": og.source_filing, "extracted_at": og.extracted_at.isoformat() + "Z" if og.extracted_at else None},
        }

    # Risk assessment based on asset leakage
    risk_assessment = None
    if og_data and og_data[0].asset_leakage_pct:
        leakage = float(og_data[0].asset_leakage_pct)
        level = "high" if leakage >= 50 else "moderate" if leakage >= 25 else "low" if leakage >= 10 else "minimal"
        desc_map = {"high": "significant credit risk", "moderate": "monitor carefully", "low": "acceptable", "minimal": "strong coverage"}
        risk_assessment = {"level": level, "description": f"{leakage:.1f}% of assets outside obligor group - {desc_map[level]}", "asset_leakage_pct": leakage}

    return {
        "data": {
            "company": company_header(company),
            "obligor_group_financials": [fmt(og) for og in og_data],
            "risk_assessment": risk_assessment,
            "summary": {"quarters_available": len(og_data), "latest_period": f"Q{og_data[0].fiscal_quarter} {og_data[0].fiscal_year}" if og_data else None},
        },
    }


# =============================================================================
# BOND PRICING
# =============================================================================


@router.get("/companies/{ticker}/debt/{debt_id}/pricing", tags=["Pricing"])
async def get_debt_pricing(ticker: str, debt_id: str, db: AsyncSession = Depends(get_db)):
    """Get pricing data for a specific debt instrument (price, YTM, spread)."""
    company = await get_company_or_404(db, ticker)
    debt_uuid = parse_uuid_or_400(debt_id, "debt ID")

    debt = (await db.execute(select(DebtInstrument).where(DebtInstrument.id == debt_uuid, DebtInstrument.company_id == company.id))).scalar_one_or_none()
    if not debt:
        raise HTTPException(status_code=404, detail=f"Debt instrument {debt_id} not found")

    pricing = (await db.execute(select(BondPricing).where(BondPricing.debt_instrument_id == debt.id))).scalar_one_or_none()
    debt_info = {"id": str(debt.id), "name": debt.name, "cusip": debt.cusip, "isin": debt.isin}

    if not pricing:
        return {"data": {"company": company_header(company), "debt_instrument": debt_info, "pricing": None, "message": "No pricing data available."}}

    debt_info.update({"coupon_rate": debt.interest_rate / 100 if debt.interest_rate else None, "maturity_date": debt.maturity_date.isoformat() if debt.maturity_date else None})
    return {
        "data": {
            "company": company_header(company),
            "debt_instrument": debt_info,
            "pricing": {
                "last_price": float(pricing.last_price) if pricing.last_price else None,
                "last_trade_date": pricing.last_trade_date.isoformat() if pricing.last_trade_date else None,
                "last_trade_volume": pricing.last_trade_volume,
                "ytm": pricing.ytm_bps / 100 if pricing.ytm_bps else None, "ytm_bps": pricing.ytm_bps,
                "spread_to_treasury": pricing.spread_to_treasury_bps, "treasury_benchmark": pricing.treasury_benchmark,
                "price_source": pricing.price_source, "staleness_days": pricing.staleness_days,
                "staleness_indicator": get_staleness_indicator(pricing.staleness_days),
                "fetched_at": pricing.fetched_at.isoformat() if pricing.fetched_at else None,
                "calculated_at": pricing.calculated_at.isoformat() if pricing.calculated_at else None,
            },
        },
    }


@router.get("/companies/{ticker}/pricing", tags=["Pricing"])
async def get_company_bond_pricing(ticker: str, db: AsyncSession = Depends(get_db)):
    """Get pricing data for all bonds of a company."""
    company = await get_company_or_404(db, ticker)

    rows = (await db.execute(
        select(DebtInstrument, BondPricing).outerjoin(BondPricing, DebtInstrument.id == BondPricing.debt_instrument_id)
        .where(DebtInstrument.company_id == company.id, DebtInstrument.is_active == True).order_by(DebtInstrument.maturity_date)
    )).all()

    bonds, no_cusip, no_pricing, total_mv = [], 0, 0, 0
    for debt, pricing in rows:
        if not debt.cusip:
            no_cusip += 1
            continue
        base = {"id": str(debt.id), "name": debt.name, "cusip": debt.cusip, "isin": debt.isin, "seniority": debt.seniority, "outstanding": debt.outstanding,
                "coupon_rate": debt.interest_rate / 100 if debt.interest_rate else None, "maturity_date": debt.maturity_date.isoformat() if debt.maturity_date else None}
        if not pricing:
            no_pricing += 1
            bonds.append({**base, "pricing": None})
        else:
            mv = int(debt.outstanding * float(pricing.last_price) / 100) if pricing.last_price and debt.outstanding else None
            if mv: total_mv += mv
            bonds.append({**base, "pricing": {
                "last_price": float(pricing.last_price) if pricing.last_price else None,
                "last_trade_date": pricing.last_trade_date.isoformat() if pricing.last_trade_date else None,
                "ytm": pricing.ytm_bps / 100 if pricing.ytm_bps else None, "spread_to_treasury": pricing.spread_to_treasury_bps,
                "treasury_benchmark": pricing.treasury_benchmark, "staleness_days": pricing.staleness_days,
                "staleness_indicator": get_staleness_indicator(pricing.staleness_days), "market_value": mv,
            }})

    # Weighted average YTM
    wtd = sum(b["pricing"]["ytm"] * b["outstanding"] for b in bonds if b.get("pricing") and b["pricing"].get("ytm") and b.get("outstanding"))
    face = sum(b["outstanding"] for b in bonds if b.get("pricing") and b["pricing"].get("ytm") and b.get("outstanding"))
    weighted_ytm = round(wtd / face, 2) if face > 0 else None

    return {
        "data": {
            "company": company_header(company), "bonds": bonds,
            "summary": {"total_bonds": len(rows), "bonds_with_cusip": len(bonds), "bonds_without_cusip": no_cusip,
                        "bonds_with_pricing": len([b for b in bonds if b.get("pricing")]), "bonds_without_pricing": no_pricing,
                        "total_market_value": total_mv if total_mv > 0 else None, "weighted_average_ytm": weighted_ytm},
        },
    }


@router.get("/companies/{ticker}/maturity-waterfall", tags=["Analytics"])
async def get_maturity_waterfall(ticker: str, db: AsyncSession = Depends(get_db)):
    """Get debt maturity waterfall grouped by year for refinancing analysis."""
    from collections import defaultdict
    from datetime import datetime

    company = await get_company_or_404(db, ticker)
    instruments = (await db.execute(
        select(DebtInstrument).where(DebtInstrument.company_id == company.id, DebtInstrument.is_active == True, DebtInstrument.maturity_date.isnot(None))
        .order_by(DebtInstrument.maturity_date)
    )).scalars().all()

    current_year = datetime.now().year
    by_year = defaultdict(lambda: {"amount_cents": 0, "instruments": []})
    for inst in instruments:
        by_year[inst.maturity_date.year]["amount_cents"] += inst.outstanding or 0
        by_year[inst.maturity_date.year]["instruments"].append({"id": str(inst.id), "name": inst.name, "seniority": inst.seniority, "outstanding_cents": inst.outstanding, "maturity_date": inst.maturity_date.isoformat(), "coupon_rate": inst.interest_rate / 100 if inst.interest_rate else None})

    end_year, start_year = current_year + 10, min(current_year, min(by_year.keys())) if by_year else current_year
    max_year = max(by_year.keys()) if by_year else current_year

    waterfall = [{"year": y, "amount_cents": by_year.get(y, {"amount_cents": 0, "instruments": []})["amount_cents"], "instrument_count": len(by_year.get(y, {"instruments": []})["instruments"]), "instruments": by_year.get(y, {"instruments": []})["instruments"]} for y in range(start_year, end_year + 1)]

    beyond = {"amount_cents": sum(by_year.get(y, {"amount_cents": 0})["amount_cents"] for y in range(end_year + 1, max_year + 1)), "instruments": [i for y in range(end_year + 1, max_year + 1) for i in by_year.get(y, {"instruments": []})["instruments"]]}
    if beyond["amount_cents"] > 0:
        waterfall.append({"year": f"{end_year + 1}+", "amount_cents": beyond["amount_cents"], "instrument_count": len(beyond["instruments"]), "instruments": beyond["instruments"]})

    total_debt = sum(w["amount_cents"] for w in waterfall)
    near_term = sum(w["amount_cents"] for w in waterfall if isinstance(w["year"], int) and w["year"] <= current_year + 2)

    return {"data": {"company": company_header(company), "waterfall": waterfall, "summary": {"total_debt_cents": total_debt, "total_instruments": len(instruments), "near_term_debt_cents": near_term, "near_term_years": 2, "earliest_maturity": min(by_year.keys()) if by_year else None, "latest_maturity": max_year if by_year else None}}}
