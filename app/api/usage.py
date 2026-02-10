"""
Usage Analytics API - Business Tier Only

GET /v1/usage/analytics - Detailed usage analytics dashboard
"""

from datetime import datetime, timedelta
from typing import Optional, List
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select, func, and_, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import require_auth, check_tier_access
from app.core.database import get_db
from app.models import User, UsageLog, UserCredits

router = APIRouter(tags=["usage-analytics"])


# =============================================================================
# Response Models
# =============================================================================


class DailyUsage(BaseModel):
    """Usage for a single day."""
    date: str
    query_count: int
    cost_usd: float
    unique_endpoints: int


class EndpointUsage(BaseModel):
    """Usage breakdown by endpoint."""
    endpoint: str
    query_count: int
    cost_usd: float
    avg_response_time_ms: Optional[float]


class TeamMemberUsage(BaseModel):
    """Usage by team member (Business tier)."""
    email: str
    query_count: int
    cost_usd: float


class UsageAnalyticsResponse(BaseModel):
    """Comprehensive usage analytics."""
    period_start: str
    period_end: str
    total_queries: int
    total_cost_usd: float
    avg_queries_per_day: float
    peak_day: Optional[str]
    peak_day_queries: int

    # Credit info
    credits_remaining: float
    credits_purchased_total: float
    credits_used_total: float

    # Breakdowns
    daily_usage: List[DailyUsage]
    endpoint_breakdown: List[EndpointUsage]
    team_usage: Optional[List[TeamMemberUsage]]  # Business only with team


class UsageTrendResponse(BaseModel):
    """Usage trends over time."""
    period: str  # "7d", "30d", "90d"
    trend_direction: str  # "increasing", "decreasing", "stable"
    change_percent: float
    current_period_queries: int
    previous_period_queries: int
    daily_data: List[DailyUsage]


# =============================================================================
# Endpoints
# =============================================================================


@router.get("/usage/analytics", response_model=UsageAnalyticsResponse)
async def get_usage_analytics(
    days: int = Query(30, description="Number of days to analyze", ge=1, le=365),
    user: User = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """
    Get detailed usage analytics for your account.

    **Business tier only** - Returns 403 for Pay-as-You-Go and Pro users.

    Returns:
    - Total queries and costs for the period
    - Daily usage breakdown
    - Endpoint-level breakdown
    - Team member usage (if applicable)
    """
    # Check tier access
    allowed, error_msg = check_tier_access(user, "/v1/usage/analytics")
    if not allowed:
        raise HTTPException(status_code=403, detail=error_msg)

    period_end = datetime.utcnow()
    period_start = period_end - timedelta(days=days)

    # Get user's credits
    credits_result = await db.execute(
        select(UserCredits).where(UserCredits.user_id == user.id)
    )
    credits = credits_result.scalar_one_or_none()

    # Total usage for period
    total_result = await db.execute(
        select(
            func.count(UsageLog.id).label("query_count"),
            func.coalesce(func.sum(UsageLog.cost_usd), 0).label("total_cost"),
        ).where(
            UsageLog.user_id == user.id,
            UsageLog.created_at >= period_start,
            UsageLog.created_at <= period_end,
        )
    )
    total_row = total_result.one()

    # Daily breakdown
    day_trunc = func.date_trunc('day', UsageLog.created_at).label("day")
    daily_result = await db.execute(
        select(
            day_trunc,
            func.count(UsageLog.id).label("query_count"),
            func.coalesce(func.sum(UsageLog.cost_usd), 0).label("cost"),
            func.count(func.distinct(UsageLog.endpoint)).label("unique_endpoints"),
        ).where(
            UsageLog.user_id == user.id,
            UsageLog.created_at >= period_start,
            UsageLog.created_at <= period_end,
        ).group_by(
            day_trunc
        ).order_by(
            day_trunc
        )
    )
    daily_rows = daily_result.all()

    daily_usage = []
    peak_day = None
    peak_day_queries = 0
    for row in daily_rows:
        day_str = row.day.strftime("%Y-%m-%d") if row.day else "Unknown"
        daily_usage.append(DailyUsage(
            date=day_str,
            query_count=row.query_count,
            cost_usd=float(row.cost) if row.cost else 0.0,
            unique_endpoints=row.unique_endpoints,
        ))
        if row.query_count > peak_day_queries:
            peak_day_queries = row.query_count
            peak_day = day_str

    # Endpoint breakdown
    endpoint_result = await db.execute(
        select(
            UsageLog.endpoint,
            func.count(UsageLog.id).label("query_count"),
            func.coalesce(func.sum(UsageLog.cost_usd), 0).label("cost"),
            func.avg(UsageLog.response_time_ms).label("avg_response_time"),
        ).where(
            UsageLog.user_id == user.id,
            UsageLog.created_at >= period_start,
            UsageLog.created_at <= period_end,
        ).group_by(
            UsageLog.endpoint
        ).order_by(
            func.count(UsageLog.id).desc()
        )
    )
    endpoint_rows = endpoint_result.all()

    endpoint_breakdown = [
        EndpointUsage(
            endpoint=row.endpoint,
            query_count=row.query_count,
            cost_usd=float(row.cost) if row.cost else 0.0,
            avg_response_time_ms=float(row.avg_response_time) if row.avg_response_time else None,
        )
        for row in endpoint_rows
    ]

    # Calculate averages
    avg_queries_per_day = total_row.query_count / days if days > 0 else 0

    return UsageAnalyticsResponse(
        period_start=period_start.strftime("%Y-%m-%d"),
        period_end=period_end.strftime("%Y-%m-%d"),
        total_queries=total_row.query_count,
        total_cost_usd=float(total_row.total_cost) if total_row.total_cost else 0.0,
        avg_queries_per_day=round(avg_queries_per_day, 1),
        peak_day=peak_day,
        peak_day_queries=peak_day_queries,
        credits_remaining=float(credits.credits_remaining) if credits else 0.0,
        credits_purchased_total=float(credits.credits_purchased) if credits else 0.0,
        credits_used_total=float(credits.credits_used) if credits else 0.0,
        daily_usage=daily_usage,
        endpoint_breakdown=endpoint_breakdown,
        team_usage=None,  # TODO: Implement team usage for Business tier with team members
    )


@router.get("/usage/trends", response_model=UsageTrendResponse)
async def get_usage_trends(
    period: str = Query("30d", description="Period to analyze: '7d', '30d', or '90d'"),
    user: User = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """
    Get usage trends comparing current period to previous period.

    **Business tier only** - Returns 403 for Pay-as-You-Go and Pro users.

    Compares usage in the selected period vs. the same length period before it.
    """
    # Check tier access
    allowed, error_msg = check_tier_access(user, "/v1/usage/analytics")
    if not allowed:
        raise HTTPException(status_code=403, detail=error_msg)

    # Parse period
    if period == "7d":
        days = 7
    elif period == "30d":
        days = 30
    elif period == "90d":
        days = 90
    else:
        raise HTTPException(status_code=400, detail="Invalid period. Must be '7d', '30d', or '90d'")

    now = datetime.utcnow()
    current_start = now - timedelta(days=days)
    previous_start = current_start - timedelta(days=days)

    # Current period queries
    current_result = await db.execute(
        select(func.count(UsageLog.id)).where(
            UsageLog.user_id == user.id,
            UsageLog.created_at >= current_start,
            UsageLog.created_at <= now,
        )
    )
    current_queries = current_result.scalar() or 0

    # Previous period queries
    previous_result = await db.execute(
        select(func.count(UsageLog.id)).where(
            UsageLog.user_id == user.id,
            UsageLog.created_at >= previous_start,
            UsageLog.created_at < current_start,
        )
    )
    previous_queries = previous_result.scalar() or 0

    # Calculate trend
    if previous_queries == 0:
        if current_queries > 0:
            change_percent = 100.0
            trend_direction = "increasing"
        else:
            change_percent = 0.0
            trend_direction = "stable"
    else:
        change_percent = ((current_queries - previous_queries) / previous_queries) * 100
        if change_percent > 10:
            trend_direction = "increasing"
        elif change_percent < -10:
            trend_direction = "decreasing"
        else:
            trend_direction = "stable"

    # Daily data for current period
    day_trunc = func.date_trunc('day', UsageLog.created_at).label("day")
    daily_result = await db.execute(
        select(
            day_trunc,
            func.count(UsageLog.id).label("query_count"),
            func.coalesce(func.sum(UsageLog.cost_usd), 0).label("cost"),
            func.count(func.distinct(UsageLog.endpoint)).label("unique_endpoints"),
        ).where(
            UsageLog.user_id == user.id,
            UsageLog.created_at >= current_start,
            UsageLog.created_at <= now,
        ).group_by(
            day_trunc
        ).order_by(
            day_trunc
        )
    )
    daily_rows = daily_result.all()

    daily_data = [
        DailyUsage(
            date=row.day.strftime("%Y-%m-%d") if row.day else "Unknown",
            query_count=row.query_count,
            cost_usd=float(row.cost) if row.cost else 0.0,
            unique_endpoints=row.unique_endpoints,
        )
        for row in daily_rows
    ]

    return UsageTrendResponse(
        period=period,
        trend_direction=trend_direction,
        change_percent=round(change_percent, 1),
        current_period_queries=current_queries,
        previous_period_queries=previous_queries,
        daily_data=daily_data,
    )
