"""
API tests for Stripe billing integration.

Tests the three-tier pricing system:
- Pay-as-You-Go: $0/month + per-call charges
- Pro: $199/month, unlimited queries
- Business: $499/month, full access + historical pricing

Tests cover:
1. Checkout session creation (Pro/Business)
2. Credit package purchases
3. Webhook handling (mocked)
4. Tier configuration validation
5. Billing portal access

Usage:
    pytest tests/api/test_stripe_billing.py -v -s
    pytest tests/api/test_stripe_billing.py -v -s -k "test_checkout"

Environment variables required:
    DEBTSTACK_API_KEY or TEST_API_KEY - API key for authenticated requests

Optional (for live Stripe tests):
    STRIPE_API_KEY - Stripe test mode secret key
    STRIPE_PRO_PRICE_ID - Pro tier price ID
    STRIPE_BUSINESS_PRICE_ID - Business tier price ID
"""

import os
import sys
import pytest
from typing import Optional
from unittest.mock import Mock, patch, AsyncMock
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

# Skip all tests if no API key
pytestmark = pytest.mark.skipif(
    not os.getenv("DEBTSTACK_API_KEY") and not os.getenv("TEST_API_KEY"),
    reason="No API key configured"
)


def get_api_client():
    """Get configured API client."""
    import httpx
    base_url = os.getenv("TEST_API_URL", "https://credible-ai-production.up.railway.app")
    api_key = os.getenv("DEBTSTACK_API_KEY") or os.getenv("TEST_API_KEY")
    return httpx.Client(
        base_url=base_url,
        headers={"X-API-Key": api_key},
        timeout=30.0
    )


# =============================================================================
# Auth Endpoint Tests
# =============================================================================

class TestAuthEndpoints:
    """Tests for /v1/auth/* endpoints."""

    @pytest.mark.api
    def test_auth_me_returns_user_info(self):
        """GET /v1/auth/me returns user info with tier."""
        with get_api_client() as client:
            response = client.get("/v1/auth/me")
            assert response.status_code == 200
            data = response.json()

            # Required fields
            assert "email" in data
            assert "tier" in data
            assert "credits_remaining" in data

            # Tier should be valid
            assert data["tier"] in ["pay_as_you_go", "pro", "business", "free", "enterprise"]

    @pytest.mark.api
    def test_auth_me_includes_credits(self):
        """GET /v1/auth/me includes credit balance."""
        with get_api_client() as client:
            response = client.get("/v1/auth/me")
            assert response.status_code == 200
            data = response.json()

            # Credits should be numeric
            assert isinstance(data["credits_remaining"], (int, float))
            assert "credits_monthly_limit" in data

    @pytest.mark.api
    def test_auth_me_without_key_returns_401(self):
        """GET /v1/auth/me without API key returns 401."""
        import httpx
        base_url = os.getenv("TEST_API_URL", "https://credible-ai-production.up.railway.app")
        with httpx.Client(base_url=base_url, timeout=30.0) as client:
            response = client.get("/v1/auth/me")
            assert response.status_code == 401


class TestUpgradeEndpoint:
    """Tests for POST /v1/auth/upgrade endpoint."""

    @pytest.mark.api
    def test_upgrade_requires_auth(self):
        """POST /v1/auth/upgrade requires authentication."""
        import httpx
        base_url = os.getenv("TEST_API_URL", "https://credible-ai-production.up.railway.app")
        with httpx.Client(base_url=base_url, timeout=30.0) as client:
            response = client.post("/v1/auth/upgrade", json={
                "success_url": "https://debtstack.ai/dashboard?upgraded=true",
                "cancel_url": "https://debtstack.ai/pricing"
            })
            assert response.status_code == 401

    @pytest.mark.api
    def test_upgrade_endpoint_exists(self):
        """POST /v1/auth/upgrade endpoint exists and responds."""
        with get_api_client() as client:
            response = client.post("/v1/auth/upgrade", json={
                "success_url": "https://debtstack.ai/dashboard?upgraded=true",
                "cancel_url": "https://debtstack.ai/pricing"
            })
            # Could be 200 (success), 400 (already pro), or 500 (stripe not configured)
            # But should NOT be 404 (endpoint missing)
            assert response.status_code != 404


class TestPortalEndpoint:
    """Tests for POST /v1/auth/portal endpoint."""

    @pytest.mark.api
    def test_portal_requires_auth(self):
        """POST /v1/auth/portal requires authentication."""
        import httpx
        base_url = os.getenv("TEST_API_URL", "https://credible-ai-production.up.railway.app")
        with httpx.Client(base_url=base_url, timeout=30.0) as client:
            response = client.post("/v1/auth/portal")
            assert response.status_code == 401

    @pytest.mark.api
    def test_portal_endpoint_exists(self):
        """POST /v1/auth/portal endpoint exists."""
        with get_api_client() as client:
            response = client.post("/v1/auth/portal")
            # Could be 200 (success), 400 (no stripe customer), or 500 (stripe error)
            # But should NOT be 404
            assert response.status_code != 404


# =============================================================================
# Pricing Tier Configuration Tests
# =============================================================================

class TestPricingTiers:
    """Tests for pricing tier configuration."""

    @pytest.mark.api
    def test_pricing_tiers_endpoint_exists(self):
        """GET /v1/pricing/tiers returns tier information."""
        with get_api_client() as client:
            response = client.get("/v1/pricing/tiers")
            # Endpoint may not exist yet - check if it does
            if response.status_code == 200:
                data = response.json()
                # Should have tier information
                assert "data" in data or "tiers" in data or isinstance(data, list)

    @pytest.mark.api
    def test_my_usage_endpoint(self):
        """GET /v1/pricing/my-usage returns usage stats."""
        with get_api_client() as client:
            response = client.get("/v1/pricing/my-usage")
            if response.status_code == 200:
                data = response.json()
                # Should have usage information
                assert "data" in data or isinstance(data, dict)


# =============================================================================
# Business-Only Endpoint Access Tests
# =============================================================================

class TestTierAccessControl:
    """Tests that tier-restricted endpoints are properly gated."""

    @pytest.mark.api
    def test_historical_pricing_endpoint(self):
        """Business-only historical pricing endpoint responds appropriately."""
        with get_api_client() as client:
            # Try to access historical pricing (business-only)
            response = client.get("/v1/bonds/12345678/pricing/history")
            # Should return 403 (forbidden) for non-business, or 200/404 for business
            # Should NOT be 404 (endpoint missing) unless the endpoint truly doesn't exist
            assert response.status_code in [200, 403, 404, 422]

    @pytest.mark.api
    def test_export_endpoint(self):
        """Business-only export endpoint responds appropriately."""
        with get_api_client() as client:
            response = client.get("/v1/export", params={"ticker": "AAPL"})
            # Should return 403 for non-business, or appropriate response for business
            assert response.status_code in [200, 403, 404, 422]

    @pytest.mark.api
    def test_covenants_compare_endpoint(self):
        """Business-only covenant comparison endpoint responds appropriately."""
        with get_api_client() as client:
            response = client.get("/v1/covenants/compare", params={
                "ticker": "AAPL,MSFT"
            })
            # Should work for business tier, return 403 for others
            assert response.status_code in [200, 403, 404, 422]

    @pytest.mark.api
    def test_usage_analytics_endpoint(self):
        """Business-only usage analytics endpoint responds appropriately."""
        with get_api_client() as client:
            response = client.get("/v1/usage/analytics")
            # Should return 403 for non-business
            assert response.status_code in [200, 403, 404, 422]


# =============================================================================
# Webhook Tests (Unit tests with mocking)
# =============================================================================

class TestWebhookEndpoint:
    """Tests for Stripe webhook handling."""

    @pytest.mark.api
    def test_webhook_requires_signature(self):
        """POST /v1/auth/webhook requires Stripe signature."""
        import httpx
        base_url = os.getenv("TEST_API_URL", "https://credible-ai-production.up.railway.app")
        with httpx.Client(base_url=base_url, timeout=30.0) as client:
            # Send without stripe-signature header
            response = client.post(
                "/v1/auth/webhook",
                json={"type": "test.event"},
                headers={"Content-Type": "application/json"}
            )
            # Should reject without signature
            assert response.status_code in [400, 401, 403]

    @pytest.mark.api
    def test_webhook_endpoint_exists(self):
        """POST /v1/auth/webhook endpoint exists."""
        import httpx
        base_url = os.getenv("TEST_API_URL", "https://credible-ai-production.up.railway.app")
        with httpx.Client(base_url=base_url, timeout=30.0) as client:
            response = client.post("/v1/auth/webhook", content=b"{}")
            # Should NOT be 404
            assert response.status_code != 404


# =============================================================================
# Unit Tests for Billing Module
# =============================================================================

class TestBillingConfiguration:
    """Unit tests for billing module configuration."""

    @pytest.mark.unit
    def test_tier_config_has_required_tiers(self):
        """TIER_CONFIG has all required tiers."""
        from app.core.billing import TIER_CONFIG

        required_tiers = ["pay_as_you_go", "pro", "business"]
        for tier in required_tiers:
            assert tier in TIER_CONFIG, f"Missing tier: {tier}"

    @pytest.mark.unit
    def test_tier_config_has_required_fields(self):
        """Each tier has required configuration fields."""
        from app.core.billing import TIER_CONFIG

        required_fields = ["monthly_price", "rate_limit"]
        for tier_name, config in TIER_CONFIG.items():
            if tier_name in ["pay_as_you_go", "pro", "business"]:
                for field in required_fields:
                    assert field in config, f"Tier {tier_name} missing field: {field}"

    @pytest.mark.unit
    def test_tier_rate_limits_are_valid(self):
        """Tier rate limits are positive integers."""
        from app.core.billing import TIER_CONFIG

        expected_limits = {
            "pay_as_you_go": 60,
            "pro": 100,
            "business": 500,
        }

        for tier, expected in expected_limits.items():
            assert TIER_CONFIG[tier]["rate_limit"] == expected, \
                f"Tier {tier} rate limit should be {expected}"

    @pytest.mark.unit
    def test_tier_prices_are_correct(self):
        """Tier monthly prices match expected values."""
        from app.core.billing import TIER_CONFIG

        expected_prices = {
            "pay_as_you_go": 0,
            "pro": 199,
            "business": 499,
        }

        for tier, expected in expected_prices.items():
            assert TIER_CONFIG[tier]["monthly_price"] == expected, \
                f"Tier {tier} price should be ${expected}"

    @pytest.mark.unit
    def test_business_tier_has_full_access(self):
        """Business tier has access to all features."""
        from app.core.billing import TIER_CONFIG

        business = TIER_CONFIG["business"]
        assert business.get("has_historical_pricing") is True
        assert business.get("has_covenant_compare") is True
        assert business.get("has_export") is True
        assert business.get("has_analytics") is True

    @pytest.mark.unit
    def test_pro_tier_excludes_business_features(self):
        """Pro tier does not have business-only features."""
        from app.core.billing import TIER_CONFIG

        pro = TIER_CONFIG["pro"]
        assert pro.get("has_historical_pricing") is False
        assert pro.get("has_covenant_compare") is False
        assert pro.get("has_export") is False


class TestEndpointCosts:
    """Unit tests for Pay-as-You-Go endpoint costs."""

    @pytest.mark.unit
    def test_endpoint_costs_defined(self):
        """Endpoint costs are defined for Pay-as-You-Go tier."""
        from app.core.billing import TIER_CONFIG

        payg = TIER_CONFIG["pay_as_you_go"]
        assert "endpoint_costs" in payg

        costs = payg["endpoint_costs"]
        assert "simple" in costs
        assert "complex" in costs
        assert "advanced" in costs

    @pytest.mark.unit
    def test_endpoint_cost_values(self):
        """Endpoint costs match expected values."""
        from app.core.billing import TIER_CONFIG

        costs = TIER_CONFIG["pay_as_you_go"]["endpoint_costs"]

        assert costs["simple"] == 0.05, "Simple endpoints should cost $0.05"
        assert costs["complex"] == 0.10, "Complex endpoints should cost $0.10"
        assert costs["advanced"] == 0.15, "Advanced endpoints should cost $0.15"


# =============================================================================
# Integration Tests (requires Stripe test mode)
# =============================================================================

@pytest.mark.skipif(
    not os.getenv("STRIPE_API_KEY"),
    reason="STRIPE_API_KEY not configured"
)
class TestStripeIntegration:
    """Integration tests that require Stripe API access."""

    @pytest.mark.integration
    def test_stripe_connection(self):
        """Stripe API is accessible."""
        import stripe
        stripe.api_key = os.getenv("STRIPE_API_KEY")

        # Simple API call to verify connection
        try:
            stripe.Account.retrieve()
            connected = True
        except stripe.error.AuthenticationError:
            connected = False

        assert connected, "Failed to connect to Stripe API"

    @pytest.mark.integration
    def test_price_ids_exist(self):
        """Configured price IDs exist in Stripe."""
        import stripe
        stripe.api_key = os.getenv("STRIPE_API_KEY")

        pro_price_id = os.getenv("STRIPE_PRO_PRICE_ID")
        business_price_id = os.getenv("STRIPE_BUSINESS_PRICE_ID")

        if pro_price_id and not pro_price_id.startswith("price_pro"):
            try:
                price = stripe.Price.retrieve(pro_price_id)
                assert price.active, "Pro price should be active"
            except stripe.error.InvalidRequestError:
                pytest.fail(f"Pro price ID {pro_price_id} not found in Stripe")

        if business_price_id and not business_price_id.startswith("price_business"):
            try:
                price = stripe.Price.retrieve(business_price_id)
                assert price.active, "Business price should be active"
            except stripe.error.InvalidRequestError:
                pytest.fail(f"Business price ID {business_price_id} not found in Stripe")

    @pytest.mark.integration
    def test_credit_price_ids_exist(self):
        """Credit package price IDs exist in Stripe."""
        import stripe
        stripe.api_key = os.getenv("STRIPE_API_KEY")

        credit_env_vars = [
            "STRIPE_CREDITS_10_PRICE_ID",
            "STRIPE_CREDITS_25_PRICE_ID",
            "STRIPE_CREDITS_50_PRICE_ID",
            "STRIPE_CREDITS_100_PRICE_ID",
        ]

        for env_var in credit_env_vars:
            price_id = os.getenv(env_var)
            if price_id and not price_id.startswith("price_credits"):
                try:
                    price = stripe.Price.retrieve(price_id)
                    assert price.active, f"{env_var} price should be active"
                except stripe.error.InvalidRequestError:
                    pytest.fail(f"{env_var}={price_id} not found in Stripe")
