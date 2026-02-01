"""
Stripe billing integration for DebtStack API.

Three-Tier Pricing:
- Pay-as-You-Go: $0/month, pay per API call ($0.05-$0.15), 60 rpm
- Pro: $199/month, unlimited queries, 100 rpm
- Business: $499/month, full access + historical pricing + team seats, 500 rpm

Handles:
- Checkout session creation for Pro/Business upgrades
- Credit package purchases for Pay-as-You-Go users
- Webhook processing for subscription and payment events
- Customer portal for managing subscriptions
"""

import stripe
from typing import Optional
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models import User, UserCredits

settings = get_settings()

# Initialize Stripe
stripe.api_key = settings.stripe_api_key

# =============================================================================
# Stripe Price Configuration
# =============================================================================

# Subscription Price IDs (set in Stripe Dashboard)
STRIPE_PRICES = {
    "pro": settings.stripe_pro_price_id,  # $199/month
    "business": settings.stripe_business_price_id,  # $499/month
}

# Credit Package Price IDs (one-time payments)
STRIPE_CREDIT_PRICES = {
    10: settings.stripe_credits_10_price_id,  # $10 credit package
    25: settings.stripe_credits_25_price_id,  # $25 credit package
    50: settings.stripe_credits_50_price_id,  # $50 credit package
    100: settings.stripe_credits_100_price_id,  # $100 credit package
}

# =============================================================================
# Tier Configuration
# =============================================================================

TIER_CONFIG = {
    "pay_as_you_go": {
        "monthly_price": 0,
        "credits": 0,  # Pay per call
        "rate_limit": 60,
        "has_pricing": True,
        "has_historical_pricing": False,
        "has_covenant_compare": False,
        "has_export": False,
        "has_analytics": False,
        "team_seats": 1,
        "companies": 200,  # Full coverage
        "description": "Pay per API call. Perfect for testing and light usage.",
        "endpoint_costs": {
            "simple": 0.05,  # /v1/companies, /v1/bonds, etc.
            "complex": 0.10,  # /v1/companies/{ticker}/changes
            "advanced": 0.15,  # /v1/entities/traverse, /v1/documents/search
        },
    },
    "pro": {
        "monthly_price": 199,
        "credits": -1,  # Unlimited
        "rate_limit": 100,
        "has_pricing": True,
        "has_historical_pricing": False,  # Business only
        "has_covenant_compare": False,  # Business only
        "has_export": False,  # Business only
        "has_analytics": False,  # Business only
        "team_seats": 1,
        "companies": 200,  # Full coverage
        "description": "Unlimited queries for production applications.",
    },
    "business": {
        "monthly_price": 499,
        "credits": -1,  # Unlimited
        "rate_limit": 500,
        "has_pricing": True,
        "has_historical_pricing": True,  # Business only
        "has_covenant_compare": True,  # Business only
        "has_export": True,  # Business only bulk export
        "has_analytics": True,  # Business only usage analytics
        "team_seats": 5,
        "companies": 200,  # Full coverage + custom requests
        "priority_support": True,
        "sla": "99.9%",
        "description": "Full access with team features and priority support.",
    },
    # Legacy aliases
    "free": {
        "monthly_price": 0,
        "credits": 0,
        "rate_limit": 60,
        "has_pricing": True,
        "companies": 200,
    },
    "enterprise": {  # Legacy alias for business
        "monthly_price": 499,
        "credits": -1,
        "rate_limit": 500,
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
    tier: str = "pro",
) -> str:
    """
    Create a Stripe Checkout session for Pro or Business subscription.

    Args:
        user: User to create checkout for
        success_url: URL to redirect to on success
        cancel_url: URL to redirect to on cancel
        db: Database session
        tier: Subscription tier ("pro" or "business")

    Returns the checkout URL to redirect the user to.
    """
    if tier not in STRIPE_PRICES:
        raise ValueError(f"Invalid tier: {tier}. Must be 'pro' or 'business'")

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
                "price": STRIPE_PRICES[tier],
                "quantity": 1,
            }
        ],
        mode="subscription",
        success_url=success_url,
        cancel_url=cancel_url,
        metadata={
            "user_id": str(user.id),
            "tier": tier,
        },
    )

    return session.url


async def create_credit_checkout_session(
    user: User,
    amount: int,
    success_url: str,
    cancel_url: str,
    db: AsyncSession,
) -> str:
    """
    Create a Stripe Checkout session for credit package purchase.

    Args:
        user: User to create checkout for
        amount: Credit package amount (10, 25, 50, or 100)
        success_url: URL to redirect to on success
        cancel_url: URL to redirect to on cancel
        db: Database session

    Returns the checkout URL to redirect the user to.
    """
    if amount not in STRIPE_CREDIT_PRICES:
        raise ValueError(f"Invalid credit amount: {amount}. Must be 10, 25, 50, or 100")

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

    # Create checkout session for one-time payment
    session = stripe.checkout.Session.create(
        customer=customer_id,
        payment_method_types=["card"],
        line_items=[
            {
                "price": STRIPE_CREDIT_PRICES[amount],
                "quantity": 1,
            }
        ],
        mode="payment",  # One-time payment, not subscription
        success_url=success_url,
        cancel_url=cancel_url,
        metadata={
            "user_id": str(user.id),
            "credit_amount": str(amount),
            "type": "credit_purchase",
        },
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

    # Determine tier from subscription metadata or price
    tier = subscription.metadata.get("tier", "pro")
    if tier not in ("pro", "business"):
        # Check price ID to determine tier
        price_id = subscription.items.data[0].price.id if subscription.items.data else None
        if price_id == STRIPE_PRICES.get("business"):
            tier = "business"
        else:
            tier = "pro"

    # Update user tier
    user.tier = tier
    user.stripe_subscription_id = subscription.id

    # Set rate limit and team seats based on tier
    tier_config = TIER_CONFIG.get(tier, TIER_CONFIG["pro"])
    user.rate_limit_per_minute = tier_config.get("rate_limit", 100)
    user.team_seats = tier_config.get("team_seats", 1)

    # Update credits to unlimited (-1 means unlimited)
    credits_result = await db.execute(
        select(UserCredits).where(UserCredits.user_id == user.id)
    )
    credits = credits_result.scalar_one_or_none()

    if credits:
        credits.credits_remaining = Decimal("999999999")  # Effectively unlimited
        credits.credits_monthly_limit = -1  # -1 = unlimited

    await db.commit()
    print(f"User {user.email} upgraded to {tier.capitalize()}")


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
    """Handle subscription.deleted webhook event (downgrade to Pay-as-You-Go)."""
    customer_id = subscription.customer

    # Find user by Stripe customer ID
    result = await db.execute(
        select(User).where(User.stripe_customer_id == customer_id)
    )
    user = result.scalar_one_or_none()

    if not user:
        print(f"Warning: No user found for Stripe customer {customer_id}")
        return

    # Downgrade to Pay-as-You-Go tier
    user.tier = "pay_as_you_go"
    user.stripe_subscription_id = None
    user.rate_limit_per_minute = 60
    user.team_seats = 1

    # Reset credits - they keep any purchased balance but lose unlimited
    credits_result = await db.execute(
        select(UserCredits).where(UserCredits.user_id == user.id)
    )
    credits = credits_result.scalar_one_or_none()

    if credits:
        # Calculate remaining purchased credits
        remaining_purchased = credits.credits_purchased - credits.credits_used
        credits.credits_remaining = max(Decimal("0"), remaining_purchased)
        credits.credits_monthly_limit = 0  # Pay per call
        credits.billing_cycle_start = date.today()

    await db.commit()
    print(f"User {user.email} downgraded to Pay-as-You-Go")


async def handle_credit_purchase(
    session: stripe.checkout.Session,
    db: AsyncSession,
) -> None:
    """Handle checkout.session.completed webhook for credit purchases."""
    if session.metadata.get("type") != "credit_purchase":
        return

    user_id = session.metadata.get("user_id")
    credit_amount = session.metadata.get("credit_amount")

    if not user_id or not credit_amount:
        print(f"Warning: Missing metadata in credit purchase session {session.id}")
        return

    # Find user
    result = await db.execute(
        select(User).where(User.id == user_id)
    )
    user = result.scalar_one_or_none()

    if not user:
        print(f"Warning: No user found for credit purchase {session.id}")
        return

    amount = Decimal(credit_amount)

    # Add credits to user's balance
    credits_result = await db.execute(
        select(UserCredits).where(UserCredits.user_id == user.id)
    )
    credits = credits_result.scalar_one_or_none()

    if credits:
        credits.credits_remaining += amount
        credits.credits_purchased += amount
        credits.last_credit_purchase = datetime.utcnow()
    else:
        # Create credits record
        credits = UserCredits(
            user_id=user.id,
            credits_remaining=amount,
            credits_purchased=amount,
            credits_used=Decimal("0"),
            billing_cycle_start=date.today(),
            last_credit_purchase=datetime.utcnow(),
        )
        db.add(credits)

    await db.commit()
    print(f"User {user.email} purchased ${amount} credits")


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
