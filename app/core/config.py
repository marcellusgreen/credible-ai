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
    # Rate Limiting
    # ==========================================================================
    rate_limit_requests: int = 100
    rate_limit_window: int = 60  # seconds

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
