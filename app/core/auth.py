"""
Authentication utilities for DebtStack API.

Handles API key generation, validation, user authentication, and tier management.

Three-Tier Pricing:
- Pay-as-You-Go: $0/month, pay per API call ($0.05-$0.15), 60 rpm
- Pro: $199/month, unlimited queries, 100 rpm
- Business: $499/month, full access + historical pricing + team seats, 500 rpm
"""

import hashlib
import secrets
from datetime import date, datetime
from decimal import Decimal
from typing import Optional, Tuple
from uuid import UUID

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import APIKeyHeader
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.database import get_db
from app.core.posthog import capture_event as posthog_capture
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
# Three-Tier Pricing Configuration
# =============================================================================


TIER_CONFIG = {
    'pay_as_you_go': {
        'rate_limit_per_minute': 60,
        'monthly_price': 0,
        'team_seats': 1,
        # Endpoints excluded from this tier (require Pro or Business)
        'excluded_endpoints': [
            '/v1/covenants/compare',
            '/v1/bonds/{cusip}/pricing/history',
            '/v1/export',
            '/v1/usage/analytics',
        ],
        # Cost per endpoint (in USD)
        'endpoint_costs': {
            # Simple: $0.05
            '/v1/companies': Decimal('0.05'),
            '/v1/bonds': Decimal('0.05'),
            '/v1/bonds/resolve': Decimal('0.05'),
            '/v1/financials': Decimal('0.05'),
            '/v1/collateral': Decimal('0.05'),
            '/v1/covenants': Decimal('0.05'),
            # Complex: $0.10
            '/v1/companies/{ticker}/changes': Decimal('0.10'),
            # Advanced: $0.15
            '/v1/entities/traverse': Decimal('0.15'),
            '/v1/documents/search': Decimal('0.15'),
            '/v1/batch': Decimal('0.15'),  # Base cost, actual depends on operations
        }
    },
    'pro': {
        'rate_limit_per_minute': 100,
        'monthly_price': 199,
        'team_seats': 1,
        # Pro still has some excluded endpoints (Business-only)
        'excluded_endpoints': [
            '/v1/covenants/compare',
            '/v1/bonds/{cusip}/pricing/history',
            '/v1/export',
            '/v1/usage/analytics',
        ],
        'endpoint_costs': {}  # Unlimited - no per-call charges
    },
    'business': {
        'rate_limit_per_minute': 500,
        'monthly_price': 499,
        'team_seats': 5,
        'excluded_endpoints': [],  # Full access to all endpoints
        'endpoint_costs': {}  # Unlimited - no per-call charges
    }
}

# Legacy tier mapping for backwards compatibility
LEGACY_TIER_MAPPING = {
    'free': 'pay_as_you_go',
    'enterprise': 'business',
    'starter': 'pay_as_you_go',
    'growth': 'pro',
    'scale': 'business',
}


def normalize_tier(tier: str) -> str:
    """Normalize tier name to new three-tier system."""
    return LEGACY_TIER_MAPPING.get(tier, tier)


# Legacy compatibility - map old TIER_CREDITS to new system
TIER_CREDITS = {
    "pay_as_you_go": 0,  # Pay per call, not credit-based
    "pro": -1,  # Unlimited
    "business": -1,  # Unlimited
    # Legacy aliases
    "free": 5,  # $5 free trial credits on signup
    "enterprise": -1,
    "starter": 0,
    "growth": -1,
    "scale": -1,
}

TIER_RATE_LIMITS = {
    "pay_as_you_go": settings.rate_limit_pay_as_you_go,
    "pro": settings.rate_limit_pro,
    "business": settings.rate_limit_business,
    # Legacy aliases
    "free": settings.rate_limit_free,
    "enterprise": settings.rate_limit_enterprise,
    "starter": settings.rate_limit_starter,
    "growth": settings.rate_limit_growth,
    "scale": settings.rate_limit_scale,
}


def get_tier_credits(tier: str) -> int:
    """Get the monthly credit limit for a tier."""
    normalized = normalize_tier(tier)
    return TIER_CREDITS.get(normalized, TIER_CREDITS["pay_as_you_go"])


def get_tier_rate_limit(tier: str) -> int:
    """Get the rate limit (requests per minute) for a tier."""
    normalized = normalize_tier(tier)
    return TIER_RATE_LIMITS.get(normalized, TIER_RATE_LIMITS["pay_as_you_go"])


def get_tier_config(tier: str) -> dict:
    """Get full tier configuration."""
    normalized = normalize_tier(tier)
    return TIER_CONFIG.get(normalized, TIER_CONFIG['pay_as_you_go'])


# =============================================================================
# Endpoint Cost & Access Control
# =============================================================================


def get_endpoint_cost(endpoint: str, tier: str) -> Decimal:
    """
    Get the cost in USD for an endpoint call based on tier.

    Returns:
        Decimal: Cost in USD (0 for Pro/Business tiers)
    """
    normalized = normalize_tier(tier)
    config = TIER_CONFIG.get(normalized, TIER_CONFIG['pay_as_you_go'])

    # Pro and Business have unlimited (no cost)
    if not config['endpoint_costs']:
        return Decimal('0')

    # Normalize endpoint for pattern matching
    # e.g., /v1/companies/AAPL/changes -> /v1/companies/{ticker}/changes
    endpoint_pattern = endpoint
    if '/companies/' in endpoint and '/changes' in endpoint:
        endpoint_pattern = '/v1/companies/{ticker}/changes'
    elif '/bonds/' in endpoint and '/pricing/history' in endpoint:
        endpoint_pattern = '/v1/bonds/{cusip}/pricing/history'

    return config['endpoint_costs'].get(endpoint_pattern, Decimal('0.05'))


def check_tier_access(user: User, endpoint: str) -> Tuple[bool, Optional[str]]:
    """
    Check if user's tier has access to an endpoint.

    Returns:
        Tuple[bool, Optional[str]]: (allowed, error_message)
            - (True, None) if access is allowed
            - (False, "error message") if access is denied
    """
    normalized = normalize_tier(user.tier)
    config = TIER_CONFIG.get(normalized, TIER_CONFIG['pay_as_you_go'])

    # Normalize endpoint for pattern matching
    endpoint_pattern = endpoint
    if '/companies/' in endpoint and '/changes' in endpoint:
        endpoint_pattern = '/v1/companies/{ticker}/changes'
    elif '/bonds/' in endpoint and '/pricing/history' in endpoint:
        endpoint_pattern = '/v1/bonds/{cusip}/pricing/history'

    # Check if endpoint is excluded for this tier
    for excluded in config['excluded_endpoints']:
        if endpoint_pattern == excluded or endpoint.startswith(excluded.replace('{cusip}', '').replace('{ticker}', '')):
            if normalized == 'pay_as_you_go':
                return False, f"This endpoint requires a Pro or Business subscription. Upgrade at https://debtstack.ai/pricing"
            elif normalized == 'pro':
                return False, f"This endpoint requires a Business subscription. Upgrade at https://debtstack.ai/pricing"

    return True, None


async def check_and_deduct_credits(
    db: AsyncSession,
    user: User,
    endpoint: str,
    cost: Decimal
) -> Tuple[bool, Optional[str]]:
    """
    Check if user has sufficient credits and deduct if so.

    Only applies to Pay-as-You-Go tier. Pro/Business have unlimited.

    Returns:
        Tuple[bool, Optional[str]]: (success, error_message)
    """
    normalized = normalize_tier(user.tier)

    # Pro and Business have unlimited - no deduction needed
    if normalized in ('pro', 'business'):
        return True, None

    # Pay-as-You-Go: check credits
    if not user.credits:
        return False, "No credit balance. Purchase credits at https://debtstack.ai/pricing"

    if user.credits.credits_remaining < cost:
        return False, f"Insufficient credits. Balance: ${user.credits.credits_remaining:.2f}, Required: ${cost:.2f}. Purchase credits at https://debtstack.ai/pricing"

    # Deduct credits
    user.credits.credits_remaining -= cost
    user.credits.credits_used += cost
    user.credits.last_credit_usage = datetime.utcnow()

    await db.commit()

    # Track credit deduction in PostHog
    posthog_capture(
        distinct_id=user.api_key_hash,
        event="credits_deducted",
        properties={
            "endpoint": endpoint,
            "cost_usd": float(cost),
            "credits_remaining": float(user.credits.credits_remaining),
            "tier": user.tier,
        },
    )

    return True, None


async def add_credits(
    db: AsyncSession,
    user: User,
    amount: Decimal
) -> None:
    """
    Add credits to user's balance (after successful payment).

    Args:
        db: Database session
        user: User to add credits to
        amount: Amount in USD to add
    """
    if not user.credits:
        # Create credits record if it doesn't exist
        user.credits = UserCredits(
            user_id=user.id,
            credits_remaining=amount,
            credits_purchased=amount,
            credits_used=Decimal('0'),
            billing_cycle_start=date.today(),
            last_credit_purchase=datetime.utcnow(),
        )
        db.add(user.credits)
    else:
        user.credits.credits_remaining += amount
        user.credits.credits_purchased += amount
        user.credits.last_credit_purchase = datetime.utcnow()

    await db.commit()


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
