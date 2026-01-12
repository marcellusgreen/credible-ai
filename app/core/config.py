"""Application configuration."""

from functools import lru_cache
from typing import Optional

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    # API
    api_title: str = "Credible.ai"
    api_version: str = "1.0.0"
    api_description: str = "The credit API for AI agents"

    # Database
    database_url: str = "postgresql+asyncpg://user:pass@localhost:5432/credible"

    # Redis (optional for MVP)
    redis_url: Optional[str] = None

    # External APIs
    anthropic_api_key: str = ""

    # Security
    secret_key: str = "change-me-in-production"
    allowed_origins: str = "*"

    # Rate limiting
    rate_limit_requests: int = 100
    rate_limit_window: int = 60  # seconds

    class Config:
        env_file = ".env"
        extra = "ignore"


@lru_cache
def get_settings() -> Settings:
    return Settings()
