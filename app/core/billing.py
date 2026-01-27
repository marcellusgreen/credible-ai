"""
Stripe billing integration for DebtStack API.

Handles:
- Checkout session creation for Pro upgrades
- Webhook processing for subscription events
- Customer portal for managing subscriptions
"""

import stripe
from typing import Optional
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models import User, UserCredits

settings = get_settings()

# Initialize Stripe
stripe.api_key = settings.stripe_api_key

# Price IDs - Update these in Stripe Dashboard
# Free: $0/month - 25 queries/day, 25 companies (curated sample)
# Pro: $49/month - Unlimited queries, 200+ companies, historical pricing
# Business: $499/month - Priority support, custom coverage, 99.9% SLA
STRIPE_PRICES = {
    "pro": "price_1StwgYAmvjlETourYUAbKPlB",  # $49/month
    "business": "price_1SuFq6AmvjlETourFzfIesa5",  # $499/month
}

# Tier configuration
TIER_CONFIG = {
    "free": {
        "credits": 25,  # 25 queries/day
        "rate_limit": 10,
        "has_pricing": True,  # Bond pricing included (updated throughout trading day)
        "companies": 25,  # Curated sample
    },
    "pro": {
        "credits": -1,  # Unlimited
        "rate_limit": 120,
        "has_pricing": True,
        "has_historical_pricing": True,
        "companies": 200,  # Full coverage
    },
    "business": {
        "credits": -1,  # Unlimited
        "rate_limit": 1000,
        "has_pricing": True,
        "has_historical_pricing": True,
        "companies": 200,  # Full coverage + custom requests
        "priority_support": True,
        "sla": "99.9%",
    },
    "enterprise": {  # Legacy alias for business
        "credits": -1,
        "rate_limit": 1000,
        "has_pricing": True,
        "has_historical_pricing": True,
        "companies": 200,
    },
}


async def create_checkout_session(
    user: User,
    success_url: str,
    cancel_url: str,
    db: AsyncSession,
) -> str:
    """
    Create a Stripe Checkout session for Pro upgrade.

    Returns the checkout URL to redirect the user to.
    """
    # Create or get Stripe customer
    if user.stripe_customer_id:
        customer_id = user.stripe_customer_id
    else:
        customer = stripe.Customer.create(
            email=user.email,
            metadata={"user_id": str(user.id)},
        )
        customer_id = customer.id

        # Save customer ID to user
        user.stripe_customer_id = customer_id
        await db.commit()

    # Create checkout session
    session = stripe.checkout.Session.create(
        customer=customer_id,
        payment_method_types=["card"],
        line_items=[
            {
                "price": STRIPE_PRICES["pro"],
                "quantity": 1,
            }
        ],
        mode="subscription",
        success_url=success_url,
        cancel_url=cancel_url,
        metadata={"user_id": str(user.id)},
    )

    return session.url


async def create_portal_session(user: User) -> str:
    """
    Create a Stripe Customer Portal session for managing subscription.

    Returns the portal URL to redirect the user to.
    """
    if not user.stripe_customer_id:
        raise ValueError("User has no Stripe customer ID")

    session = stripe.billing_portal.Session.create(
        customer=user.stripe_customer_id,
        return_url="https://debtstack.ai/dashboard",
    )

    return session.url


async def handle_subscription_created(
    subscription: stripe.Subscription,
    db: AsyncSession,
) -> None:
    """Handle subscription.created webhook event."""
    customer_id = subscription.customer

    # Find user by Stripe customer ID
    result = await db.execute(
        select(User).where(User.stripe_customer_id == customer_id)
    )
    user = result.scalar_one_or_none()

    if not user:
        # Try to find by metadata
        customer = stripe.Customer.retrieve(customer_id)
        user_id = customer.metadata.get("user_id")
        if user_id:
            result = await db.execute(
                select(User).where(User.id == user_id)
            )
            user = result.scalar_one_or_none()

    if not user:
        print(f"Warning: No user found for Stripe customer {customer_id}")
        return

    # Update user to Pro tier
    user.tier = "pro"
    user.stripe_subscription_id = subscription.id

    # Update credits to unlimited (-1 means unlimited)
    credits_result = await db.execute(
        select(UserCredits).where(UserCredits.user_id == user.id)
    )
    credits = credits_result.scalar_one_or_none()

    if credits:
        credits.credits_remaining = Decimal("999999999")  # Effectively unlimited
        credits.credits_monthly_limit = -1  # -1 = unlimited

    await db.commit()
    print(f"User {user.email} upgraded to Pro")


async def handle_subscription_updated(
    subscription: stripe.Subscription,
    db: AsyncSession,
) -> None:
    """Handle subscription.updated webhook event."""
    # Check if subscription is still active
    if subscription.status in ["active", "trialing"]:
        await handle_subscription_created(subscription, db)
    elif subscription.status in ["canceled", "unpaid", "past_due"]:
        await handle_subscription_deleted(subscription, db)


async def handle_subscription_deleted(
    subscription: stripe.Subscription,
    db: AsyncSession,
) -> None:
    """Handle subscription.deleted webhook event (downgrade to free)."""
    customer_id = subscription.customer

    # Find user by Stripe customer ID
    result = await db.execute(
        select(User).where(User.stripe_customer_id == customer_id)
    )
    user = result.scalar_one_or_none()

    if not user:
        print(f"Warning: No user found for Stripe customer {customer_id}")
        return

    # Downgrade to free tier
    user.tier = "free"
    user.stripe_subscription_id = None

    # Reset credits to free tier (25 queries/day)
    credits_result = await db.execute(
        select(UserCredits).where(UserCredits.user_id == user.id)
    )
    credits = credits_result.scalar_one_or_none()

    if credits:
        credits.credits_remaining = Decimal("25")
        credits.credits_monthly_limit = 25  # Daily limit for free tier
        credits.billing_cycle_start = date.today()  # Reset to today for daily tracking

    await db.commit()
    print(f"User {user.email} downgraded to Free")


async def handle_invoice_paid(
    invoice: stripe.Invoice,
    db: AsyncSession,
) -> None:
    """Handle invoice.paid webhook event (subscription renewed)."""
    customer_id = invoice.customer

    # Find user by Stripe customer ID
    result = await db.execute(
        select(User).where(User.stripe_customer_id == customer_id)
    )
    user = result.scalar_one_or_none()

    if not user:
        return

    # Reset billing cycle for new month (Pro has unlimited, but track for analytics)
    credits_result = await db.execute(
        select(UserCredits).where(UserCredits.user_id == user.id)
    )
    credits = credits_result.scalar_one_or_none()

    if credits:
        credits.billing_cycle_start = date.today().replace(day=1)
        credits.overage_credits_used = 0

    await db.commit()


def verify_webhook_signature(payload: bytes, sig_header: str) -> stripe.Event:
    """
    Verify Stripe webhook signature and return the event.

    Raises ValueError if signature is invalid.
    """
    if not settings.stripe_webhook_secret:
        raise ValueError("Stripe webhook secret not configured")

    try:
        event = stripe.Webhook.construct_event(
            payload, sig_header, settings.stripe_webhook_secret
        )
        return event
    except stripe.error.SignatureVerificationError as e:
        raise ValueError(f"Invalid signature: {e}")
