"""
Authentication utilities for DebtStack API.

Handles API key generation, validation, and user authentication.
"""

import hashlib
import secrets
from datetime import date
from decimal import Decimal
from typing import Optional
from uuid import UUID

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import APIKeyHeader
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.database import get_db
from app.models import User, UserCredits

settings = get_settings()

# API key header scheme
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


# =============================================================================
# API Key Generation
# =============================================================================


def generate_api_key() -> str:
    """
    Generate a new API key.

    Format: ds_<32 random hex chars> (total 35 chars)
    """
    random_part = secrets.token_hex(16)
    return f"{settings.api_key_prefix}{random_part}"


def hash_api_key(api_key: str) -> str:
    """Hash an API key using SHA-256."""
    return hashlib.sha256(api_key.encode()).hexdigest()


def get_api_key_prefix(api_key: str) -> str:
    """Get display prefix of API key (first 8 chars after ds_)."""
    if api_key.startswith(settings.api_key_prefix):
        return api_key[:len(settings.api_key_prefix) + 8]
    return api_key[:8]


# =============================================================================
# Tier Configuration
# =============================================================================


TIER_CREDITS = {
    "free": 1000,
    "pro": -1,  # Unlimited
    "enterprise": -1,  # Unlimited
    # Legacy tiers (keep for backward compatibility)
    "starter": 3000,
    "growth": 15000,
    "scale": 50000,
}

TIER_RATE_LIMITS = {
    "free": settings.rate_limit_free,
    "pro": settings.rate_limit_pro,
    "enterprise": settings.rate_limit_enterprise,
    # Legacy tiers
    "starter": settings.rate_limit_starter,
    "growth": settings.rate_limit_growth,
    "scale": settings.rate_limit_scale,
}


def get_tier_credits(tier: str) -> int:
    """Get the monthly credit limit for a tier."""
    return TIER_CREDITS.get(tier, TIER_CREDITS["free"])


def get_tier_rate_limit(tier: str) -> int:
    """Get the rate limit (requests per minute) for a tier."""
    return TIER_RATE_LIMITS.get(tier, TIER_RATE_LIMITS["free"])


# =============================================================================
# User Authentication
# =============================================================================


async def get_user_by_api_key(api_key: str, db: AsyncSession) -> Optional[User]:
    """Look up a user by their API key."""
    api_key_hash = hash_api_key(api_key)
    result = await db.execute(
        select(User).where(
            User.api_key_hash == api_key_hash,
            User.is_active == True,
        )
    )
    return result.scalar_one_or_none()


async def require_auth(
    request: Request,
    api_key: Optional[str] = Depends(api_key_header),
    db: AsyncSession = Depends(get_db),
) -> User:
    """Require authentication. Raises 401 if not authenticated."""
    if settings.auth_bypass:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
        )

    if not api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="API key required. Pass via X-API-Key header.",
        )

    if not api_key.startswith(settings.api_key_prefix):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid API key format. Keys start with '{settings.api_key_prefix}'",
        )

    user = await get_user_by_api_key(api_key, db)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API key",
        )

    return user


# =============================================================================
# Credit Management
# =============================================================================


async def create_user_credits(user_id: UUID, tier: str, db: AsyncSession) -> UserCredits:
    """Create a new UserCredits record for a user."""
    credits = UserCredits(
        user_id=user_id,
        credits_remaining=Decimal(str(get_tier_credits(tier))),
        credits_monthly_limit=get_tier_credits(tier),
        billing_cycle_start=date.today().replace(day=1),
    )
    db.add(credits)
    await db.commit()
    await db.refresh(credits)
    return credits
