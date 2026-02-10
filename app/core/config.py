"""Application configuration."""

from functools import lru_cache
from typing import Optional

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    # ==========================================================================
    # Environment
    # ==========================================================================
    environment: str = "development"  # development, staging, production
    debug: bool = False

    # ==========================================================================
    # API
    # ==========================================================================
    api_title: str = "DebtStack.ai"
    api_version: str = "1.0.0"
    api_description: str = "The credit API for AI agents"

    # ==========================================================================
    # Database (Neon PostgreSQL)
    # ==========================================================================
    database_url: str = "postgresql+asyncpg://user:pass@localhost:5432/debtstack"

    # ==========================================================================
    # Redis Cache (Upstash)
    # ==========================================================================
    redis_url: Optional[str] = None

    # ==========================================================================
    # Cloud Storage (Cloudflare R2)
    # ==========================================================================
    r2_account_id: Optional[str] = None
    r2_access_key_id: Optional[str] = None
    r2_secret_access_key: Optional[str] = None
    r2_bucket_name: str = "debtstack-documents"

    # ==========================================================================
    # External APIs
    # ==========================================================================
    # Anthropic (Claude) - Required
    anthropic_api_key: str = ""

    # Google Gemini - Recommended for Tier 1 extraction
    gemini_api_key: Optional[str] = None

    # SEC-API.io - Recommended for fast filing retrieval
    sec_api_key: Optional[str] = None

    # DeepSeek - Alternative Tier 1 (optional)
    deepseek_api_key: Optional[str] = None

    # Finnhub - For TRACE bond pricing (optional, requires premium)
    finnhub_api_key: Optional[str] = None

    # OpenFIGI - For CUSIP mapping (optional)
    openfigi_api_key: Optional[str] = None

    # ==========================================================================
    # Security
    # ==========================================================================
    secret_key: str = "change-me-in-production"
    allowed_origins: str = "*"

    # ==========================================================================
    # Authentication & Billing
    # ==========================================================================
    # Stripe for payment processing
    stripe_api_key: Optional[str] = None
    stripe_webhook_secret: Optional[str] = None

    # Stripe Price IDs for subscriptions (set in Stripe Dashboard)
    stripe_pro_price_id: str = "price_pro_199"  # $199/month Pro tier
    stripe_business_price_id: str = "price_business_499"  # $499/month Business tier

    # Stripe Price IDs for credit packages (Pay-as-You-Go)
    stripe_credits_10_price_id: str = "price_credits_10"  # $10 credit package
    stripe_credits_25_price_id: str = "price_credits_25"  # $25 credit package
    stripe_credits_50_price_id: str = "price_credits_50"  # $50 credit package
    stripe_credits_100_price_id: str = "price_credits_100"  # $100 credit package

    # API Key settings
    api_key_prefix: str = "ds_"  # All API keys start with "ds_"

    # Auth bypass for development (set to True to disable auth in dev)
    auth_bypass: bool = False

    # ==========================================================================
    # Observability
    # ==========================================================================
    sentry_dsn: Optional[str] = None
    slack_webhook_url: Optional[str] = None

    # ==========================================================================
    # Rate Limiting
    # ==========================================================================
    rate_limit_requests: int = 100
    rate_limit_window: int = 60  # seconds

    # Per-tier rate limits (requests per minute)
    rate_limit_pay_as_you_go: int = 60  # Pay-as-You-Go tier
    rate_limit_pro: int = 100  # Pro tier ($199/month)
    rate_limit_business: int = 500  # Business tier ($499/month)
    # Legacy tier aliases (for backwards compatibility)
    rate_limit_free: int = 60  # Maps to pay_as_you_go
    rate_limit_enterprise: int = 500  # Maps to business
    rate_limit_starter: int = 60
    rate_limit_growth: int = 100
    rate_limit_scale: int = 500

    class Config:
        env_file = ".env"
        extra = "ignore"

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    @property
    def has_redis(self) -> bool:
        return self.redis_url is not None

    @property
    def has_r2(self) -> bool:
        return all([self.r2_account_id, self.r2_access_key_id, self.r2_secret_access_key])


@lru_cache
def get_settings() -> Settings:
    return Settings()
