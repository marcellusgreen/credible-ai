"""
Pricing API endpoints for DebtStack.ai

Public and authenticated endpoints for:
- Viewing pricing tiers
- Calculating API costs
- Viewing usage statistics
- Purchasing credits
"""

from datetime import datetime, timedelta
from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import require_auth, get_tier_config, get_endpoint_cost, TIER_CONFIG
from app.core.billing import (
    TIER_CONFIG as BILLING_TIER_CONFIG,
    create_credit_checkout_session,
    create_checkout_session,
)
from app.core.database import get_db
from app.models import User, UserCredits, UsageLog

router = APIRouter(tags=["pricing"])


# =============================================================================
# Response Models
# =============================================================================


class TierInfo(BaseModel):
    """Public tier information."""
    name: str
    monthly_price: int
    rate_limit: int
    team_seats: int
    features: list[str]
    endpoint_costs: Optional[dict] = None


class PricingTiersResponse(BaseModel):
    """Response for /v1/pricing/tiers."""
    tiers: dict[str, TierInfo]


class CostCalculation(BaseModel):
    """Cost calculation for a usage scenario."""
    tier: str
    monthly_queries: int
    estimated_monthly_cost: float
    cost_breakdown: dict


class UsageStats(BaseModel):
    """User's usage statistics."""
    tier: str
    credits_remaining: float
    credits_used_total: float
    credits_purchased_total: float
    queries_this_month: int
    cost_this_month: float
    last_usage: Optional[datetime]


# =============================================================================
# Public Endpoints
# =============================================================================


@router.get("/pricing/tiers", response_model=PricingTiersResponse)
async def get_pricing_tiers():
    """
    Get public information about pricing tiers.

    This is a public endpoint - no authentication required.
    """
    tiers = {}

    # Pay-as-You-Go tier
    tiers["pay_as_you_go"] = TierInfo(
        name="Pay-as-You-Go",
        monthly_price=0,
        rate_limit=60,
        team_seats=1,
        features=[
            "Pay per API call ($0.05-$0.15)",
            "60 requests/minute",
            "200+ companies",
            "All basic endpoints",
            "Bond pricing data",
        ],
        endpoint_costs={
            "simple": 0.05,
            "complex": 0.10,
            "advanced": 0.15,
        }
    )

    # Pro tier
    tiers["pro"] = TierInfo(
        name="Pro",
        monthly_price=199,
        rate_limit=100,
        team_seats=1,
        features=[
            "Unlimited API queries",
            "100 requests/minute",
            "200+ companies",
            "All basic endpoints",
            "Bond pricing data",
        ]
    )

    # Business tier
    tiers["business"] = TierInfo(
        name="Business",
        monthly_price=499,
        rate_limit=500,
        team_seats=5,
        features=[
            "Everything in Pro, plus:",
            "500 requests/minute",
            "5 team seats",
            "Covenant comparison endpoint",
            "Historical bond pricing",
            "Bulk data export",
            "Usage analytics dashboard",
            "Priority support (24hr response)",
            "Custom company coverage requests",
            "99.9% uptime SLA",
        ]
    )

    return PricingTiersResponse(tiers=tiers)


@router.post("/pricing/calculate")
async def calculate_cost(
    tier: str = Query(..., description="Tier to calculate costs for"),
    monthly_queries: int = Query(..., description="Expected monthly queries", ge=0),
    query_mix: Optional[str] = Query(
        "balanced",
        description="Query mix: 'simple', 'complex', 'advanced', or 'balanced'"
    ),
):
    """
    Calculate estimated monthly cost for a usage scenario.

    Query mix affects Pay-as-You-Go costs:
    - simple: Mostly /v1/companies, /v1/bonds, /v1/financials ($0.05/call)
    - complex: Mostly /v1/companies/{ticker}/changes ($0.10/call)
    - advanced: Mostly /v1/entities/traverse, /v1/documents/search ($0.15/call)
    - balanced: Even mix of all types
    """
    if tier not in ("pay_as_you_go", "pro", "business"):
        raise HTTPException(status_code=400, detail="Invalid tier. Must be 'pay_as_you_go', 'pro', or 'business'")

    tier_config = BILLING_TIER_CONFIG.get(tier, BILLING_TIER_CONFIG["pay_as_you_go"])

    if tier in ("pro", "business"):
        # Subscription tiers have fixed monthly cost
        return CostCalculation(
            tier=tier,
            monthly_queries=monthly_queries,
            estimated_monthly_cost=float(tier_config["monthly_price"]),
            cost_breakdown={
                "subscription": tier_config["monthly_price"],
                "per_query_cost": 0,
                "total_query_cost": 0,
            }
        )

    # Pay-as-You-Go: calculate based on query mix
    costs = tier_config.get("endpoint_costs", {"simple": 0.05, "complex": 0.10, "advanced": 0.15})

    if query_mix == "simple":
        avg_cost = costs["simple"]
    elif query_mix == "complex":
        avg_cost = costs["complex"]
    elif query_mix == "advanced":
        avg_cost = costs["advanced"]
    else:  # balanced
        avg_cost = (costs["simple"] + costs["complex"] + costs["advanced"]) / 3

    total_cost = monthly_queries * avg_cost

    return CostCalculation(
        tier=tier,
        monthly_queries=monthly_queries,
        estimated_monthly_cost=round(total_cost, 2),
        cost_breakdown={
            "subscription": 0,
            "per_query_cost": avg_cost,
            "total_query_cost": round(total_cost, 2),
            "simple_cost": costs["simple"],
            "complex_cost": costs["complex"],
            "advanced_cost": costs["advanced"],
        }
    )


# =============================================================================
# Authenticated Endpoints
# =============================================================================


@router.get("/pricing/my-usage", response_model=UsageStats)
async def get_my_usage(
    user: User = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """
    Get authenticated user's usage statistics.

    Returns:
    - Current tier and credit balance
    - Usage for current billing period
    - Total lifetime usage
    """
    # Get user's credits
    credits_result = await db.execute(
        select(UserCredits).where(UserCredits.user_id == user.id)
    )
    credits = credits_result.scalar_one_or_none()

    # Get usage stats for current month
    month_start = datetime.utcnow().replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    usage_result = await db.execute(
        select(
            func.count(UsageLog.id).label("query_count"),
            func.coalesce(func.sum(UsageLog.cost_usd), 0).label("total_cost"),
        ).where(
            UsageLog.user_id == user.id,
            UsageLog.created_at >= month_start,
        )
    )
    usage_row = usage_result.one()

    return UsageStats(
        tier=user.tier,
        credits_remaining=float(credits.credits_remaining) if credits else 0.0,
        credits_used_total=float(credits.credits_used) if credits else 0.0,
        credits_purchased_total=float(credits.credits_purchased) if credits else 0.0,
        queries_this_month=usage_row.query_count,
        cost_this_month=float(usage_row.total_cost),
        last_usage=credits.last_credit_usage if credits else None,
    )


@router.post("/pricing/purchase-credits")
async def purchase_credits(
    amount: int = Query(..., description="Credit package amount: 10, 25, 50, or 100"),
    success_url: Optional[str] = Query(None, description="URL to redirect to on success"),
    cancel_url: Optional[str] = Query(None, description="URL to redirect to on cancel"),
    user: User = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """
    Create a Stripe checkout session to purchase credit package.

    Available packages:
    - $10: ~200 simple queries
    - $25: ~500 simple queries
    - $50: ~1,000 simple queries
    - $100: ~2,000 simple queries

    Returns checkout URL to redirect user to.
    """
    if amount not in (10, 25, 50, 100):
        raise HTTPException(
            status_code=400,
            detail="Invalid amount. Must be 10, 25, 50, or 100"
        )

    try:
        checkout_url = await create_credit_checkout_session(
            user=user,
            amount=amount,
            success_url=success_url or "https://debtstack.ai/dashboard?credits=purchased",
            cancel_url=cancel_url or "https://debtstack.ai/pricing",
            db=db,
        )
        return {"checkout_url": checkout_url}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to create checkout: {str(e)}")


@router.post("/pricing/upgrade")
async def upgrade_subscription(
    tier: str = Query(..., description="Tier to upgrade to: 'pro' or 'business'"),
    success_url: Optional[str] = Query(None, description="URL to redirect to on success"),
    cancel_url: Optional[str] = Query(None, description="URL to redirect to on cancel"),
    user: User = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """
    Create a Stripe checkout session to upgrade to Pro or Business.

    Returns checkout URL to redirect user to.
    """
    if tier not in ("pro", "business"):
        raise HTTPException(
            status_code=400,
            detail="Invalid tier. Must be 'pro' or 'business'"
        )

    # Check if user already has this tier
    if user.tier == tier:
        raise HTTPException(
            status_code=400,
            detail=f"You are already on the {tier.capitalize()} tier"
        )

    # Check if user already has a subscription
    if user.stripe_subscription_id and user.tier in ("pro", "business"):
        raise HTTPException(
            status_code=400,
            detail="You already have an active subscription. Please manage it from your dashboard."
        )

    try:
        checkout_url = await create_checkout_session(
            user=user,
            success_url=success_url or f"https://debtstack.ai/dashboard?upgraded={tier}",
            cancel_url=cancel_url or "https://debtstack.ai/pricing",
            db=db,
            tier=tier,
        )
        return {"checkout_url": checkout_url}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to create checkout: {str(e)}")
