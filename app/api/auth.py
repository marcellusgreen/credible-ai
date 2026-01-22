"""
Authentication endpoints for DebtStack API.

Endpoints:
- POST /v1/auth/signup - Create account and get API key
- GET /v1/auth/me - Get current user info and credits
"""

from datetime import date
from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
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
from app.core.database import get_db
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
    All new accounts start on the free tier with 1,000 credits/month.
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
