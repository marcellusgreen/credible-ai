"""
Authentication endpoints for DebtStack API.

Endpoints:
- POST /v1/auth/signup - Create account and get API key
- GET /v1/auth/me - Get current user info and credits
- POST /v1/auth/upgrade - Create Stripe checkout session for Pro upgrade
- POST /v1/auth/portal - Create Stripe customer portal session
- POST /v1/auth/webhook - Handle Stripe webhook events
"""

from datetime import date
from decimal import Decimal
from typing import Optional

import stripe
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import (
    generate_api_key,
    hash_api_key,
    get_api_key_prefix,
    require_auth,
    create_user_credits,
    TIER_CREDITS,
    TIER_RATE_LIMITS,
)
from app.core.billing import (
    create_checkout_session,
    create_portal_session,
    verify_webhook_signature,
    handle_subscription_created,
    handle_subscription_updated,
    handle_subscription_deleted,
    handle_invoice_paid,
    handle_credit_purchase,
)
from app.core.database import get_db
from app.core.posthog import capture_event as posthog_capture, identify_user as posthog_identify
from app.models import User, UserCredits

router = APIRouter(prefix="/auth", tags=["Authentication"])


# =============================================================================
# Request/Response Models
# =============================================================================


class SignupRequest(BaseModel):
    """Request body for user signup."""
    email: EmailStr = Field(..., description="User email address")


class SignupResponse(BaseModel):
    """Response for successful signup."""
    message: str
    user_id: str
    email: str
    api_key: str = Field(..., description="Your API key. Save this - it cannot be retrieved later!")
    api_key_prefix: str
    tier: str
    credits_monthly: int
    rate_limit_per_minute: int


class UserInfoResponse(BaseModel):
    """Response for user info endpoint."""
    user_id: str
    email: str
    tier: str
    api_key_prefix: str
    is_active: bool
    credits_remaining: float
    credits_monthly_limit: int
    billing_cycle_start: date


class UpgradeRequest(BaseModel):
    """Request body for upgrade."""
    success_url: str = Field(default="https://debtstack.ai/dashboard?upgraded=true")
    cancel_url: str = Field(default="https://debtstack.ai/pricing")


class UpgradeResponse(BaseModel):
    """Response for upgrade endpoint."""
    checkout_url: str


class PortalResponse(BaseModel):
    """Response for portal endpoint."""
    portal_url: str


# =============================================================================
# Endpoints
# =============================================================================


@router.post("/signup", response_model=SignupResponse)
async def signup(
    request: SignupRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    Create a new DebtStack account.

    Returns an API key that you must save - it cannot be retrieved later.
    All new accounts start on the free tier with $5 in trial credits.
    """
    # Check if email already exists
    existing = await db.execute(
        select(User).where(User.email == request.email)
    )
    if existing.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="An account with this email already exists",
        )

    # Generate API key
    api_key = generate_api_key()
    api_key_hashed = hash_api_key(api_key)
    api_key_prefix_value = get_api_key_prefix(api_key)

    # Create user
    user = User(
        email=request.email,
        api_key_hash=api_key_hashed,
        api_key_prefix=api_key_prefix_value,
        tier="free",
        is_active=True,
    )
    db.add(user)
    await db.flush()

    # Create credits
    await create_user_credits(user.id, "free", db)

    await db.commit()

    # Track signup in PostHog
    posthog_distinct_id = api_key_hashed
    posthog_capture(
        distinct_id=posthog_distinct_id,
        event="user_signed_up",
        properties={"tier": "free", "email": request.email},
    )
    posthog_identify(
        distinct_id=posthog_distinct_id,
        properties={"email": request.email, "tier": "free"},
    )

    return SignupResponse(
        message="Account created successfully",
        user_id=str(user.id),
        email=user.email,
        api_key=api_key,
        api_key_prefix=api_key_prefix_value,
        tier="free",
        credits_monthly=TIER_CREDITS["free"],
        rate_limit_per_minute=TIER_RATE_LIMITS["free"],
    )


@router.get("/me", response_model=UserInfoResponse)
async def get_me(
    user: User = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """Get current user info and credit balance."""
    credits_result = await db.execute(
        select(UserCredits).where(UserCredits.user_id == user.id)
    )
    credits = credits_result.scalar_one_or_none()

    return UserInfoResponse(
        user_id=str(user.id),
        email=user.email,
        tier=user.tier,
        api_key_prefix=user.api_key_prefix,
        is_active=user.is_active,
        credits_remaining=float(credits.credits_remaining) if credits else 0,
        credits_monthly_limit=credits.credits_monthly_limit if credits else TIER_CREDITS["free"],
        billing_cycle_start=credits.billing_cycle_start if credits else date.today(),
    )


# =============================================================================
# Billing Endpoints
# =============================================================================


@router.post("/upgrade", response_model=UpgradeResponse)
async def upgrade_to_pro(
    request: UpgradeRequest,
    user: User = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """
    Create a Stripe Checkout session to upgrade to Pro.

    Returns a checkout URL - redirect the user there to complete payment.
    """
    if user.tier == "pro":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="You are already on the Pro tier",
        )

    if user.tier in ("business", "enterprise"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Business tier users should contact sales for billing changes",
        )

    try:
        checkout_url = await create_checkout_session(
            user=user,
            success_url=request.success_url,
            cancel_url=request.cancel_url,
            db=db,
        )
        return UpgradeResponse(checkout_url=checkout_url)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to create checkout session: {str(e)}",
        )


@router.post("/portal", response_model=PortalResponse)
async def billing_portal(
    user: User = Depends(require_auth),
):
    """
    Create a Stripe Customer Portal session to manage subscription.

    Returns a portal URL - redirect the user there to manage billing.
    """
    if not user.stripe_customer_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No billing account found. Upgrade to Pro first.",
        )

    try:
        portal_url = await create_portal_session(user)
        return PortalResponse(portal_url=portal_url)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to create portal session: {str(e)}",
        )


@router.post("/webhook")
async def stripe_webhook(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """
    Handle Stripe webhook events.

    This endpoint is called by Stripe when subscription events occur.
    """
    payload = await request.body()
    sig_header = request.headers.get("stripe-signature")

    if not sig_header:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing stripe-signature header",
        )

    try:
        event = verify_webhook_signature(payload, sig_header)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )

    # Handle the event
    event_type = event["type"]
    data = event["data"]["object"]

    try:
        if event_type == "checkout.session.completed":
            await handle_credit_purchase(data, db)
        elif event_type == "customer.subscription.created":
            await handle_subscription_created(data, db)
        elif event_type == "customer.subscription.updated":
            await handle_subscription_updated(data, db)
        elif event_type == "customer.subscription.deleted":
            await handle_subscription_deleted(data, db)
        elif event_type == "invoice.paid":
            await handle_invoice_paid(data, db)
        else:
            print(f"Unhandled event type: {event_type}")
    except Exception as e:
        # Log the error but return 200 to prevent Stripe from retrying
        # (Stripe will retry on non-2xx responses)
        print(f"Error handling webhook event {event_type}: {e}")
        # Re-raise to return 500 and see the error
        raise

    return {"status": "ok"}
