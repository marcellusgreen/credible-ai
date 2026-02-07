"""
Pytest configuration and fixtures for DebtStack eval tests.

Provides:
- API client fixture with authentication
- Database session fixture for ground truth queries
- Ground truth data fixtures for common test companies
"""

import os
import sys
import time
from typing import Generator, AsyncGenerator

import pytest
import pytest_asyncio
import httpx
from dotenv import load_dotenv

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from tests.eval.ground_truth import GroundTruthManager

load_dotenv()


# =============================================================================
# CONFIGURATION
# =============================================================================

# API configuration
DEFAULT_API_URL = os.getenv("TEST_API_URL", "https://api.debtstack.ai")
API_KEY = os.getenv("DEBTSTACK_API_KEY") or os.getenv("TEST_API_KEY")

# Database configuration
DATABASE_URL = os.getenv("DATABASE_URL", "")
# Convert postgres:// to postgresql+asyncpg:// for async
if DATABASE_URL.startswith("postgres://"):
    ASYNC_DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql+asyncpg://", 1)
elif DATABASE_URL.startswith("postgresql://"):
    ASYNC_DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+asyncpg://", 1)
else:
    ASYNC_DATABASE_URL = DATABASE_URL


# =============================================================================
# TEST COMPANY FIXTURES
# =============================================================================

# Companies used for ground truth testing (diverse sectors, data coverage)
TEST_COMPANIES = {
    "CHTR": {"name": "Charter Communications", "sector": "Communication Services"},
    "AAPL": {"name": "Apple Inc.", "sector": "Technology"},
    "RIG": {"name": "Transocean Ltd.", "sector": "Energy"},
    "MSFT": {"name": "Microsoft Corporation", "sector": "Technology"},
    "GOOGL": {"name": "Alphabet Inc.", "sector": "Communication Services"},
    "T": {"name": "AT&T Inc.", "sector": "Communication Services"},
    "VZ": {"name": "Verizon Communications", "sector": "Communication Services"},
}

# Test bonds with known data
# Note: RIG bonds don't have CUSIPs in our database
# Actual RIG bonds: 6.875% 2027, 8.00% 2028, 8.375% 2028, 8.75% 2030
TEST_BONDS = {
    # Add bonds with CUSIPs as they become available in the database
}


# =============================================================================
# API CLIENT FIXTURES
# =============================================================================

@pytest.fixture(scope="session")
def api_base_url() -> str:
    """Base URL for API tests."""
    return DEFAULT_API_URL


@pytest.fixture(scope="session")
def api_key() -> str:
    """API key for authenticated requests."""
    if not API_KEY:
        pytest.skip("DEBTSTACK_API_KEY or TEST_API_KEY environment variable not set")
    return API_KEY


@pytest.fixture(scope="session")
def api_headers(api_key: str) -> dict:
    """Headers for API requests."""
    return {"X-API-Key": api_key}


# Global rate limiter state (shared across all tests)
_global_last_request_time = 0.0
_global_rate_limit_lock = None


class RateLimitedClient:
    """HTTP client wrapper with rate limiting to avoid 429 errors."""

    def __init__(self, client: httpx.Client, requests_per_minute: int = 20):
        self._client = client
        self._min_interval = 60.0 / requests_per_minute

    def _wait_for_rate_limit(self):
        """Wait if needed to respect rate limit."""
        global _global_last_request_time
        elapsed = time.time() - _global_last_request_time
        if elapsed < self._min_interval:
            time.sleep(self._min_interval - elapsed)
        _global_last_request_time = time.time()

    def get(self, *args, **kwargs):
        self._wait_for_rate_limit()
        return self._client.get(*args, **kwargs)

    def post(self, *args, **kwargs):
        self._wait_for_rate_limit()
        return self._client.post(*args, **kwargs)

    def __getattr__(self, name):
        return getattr(self._client, name)


@pytest.fixture
def api_client(api_base_url: str, api_headers: dict) -> Generator[RateLimitedClient, None, None]:
    """Synchronous HTTP client for API requests with rate limiting."""
    with httpx.Client(
        base_url=api_base_url,
        headers=api_headers,
        timeout=30.0,
    ) as client:
        # 20 requests per minute = 3 seconds between requests
        yield RateLimitedClient(client, requests_per_minute=20)


@pytest.fixture
async def async_api_client(api_base_url: str, api_headers: dict) -> AsyncGenerator[httpx.AsyncClient, None]:
    """Async HTTP client for API requests."""
    async with httpx.AsyncClient(
        base_url=api_base_url,
        headers=api_headers,
        timeout=30.0,
    ) as client:
        yield client


# =============================================================================
# DATABASE FIXTURES
# =============================================================================

@pytest_asyncio.fixture(scope="function")
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    """Async database session for ground truth queries."""
    if not ASYNC_DATABASE_URL:
        pytest.skip("DATABASE_URL environment variable not set")

    engine = create_async_engine(ASYNC_DATABASE_URL, echo=False)
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with async_session() as session:
        yield session

    await engine.dispose()


@pytest_asyncio.fixture
async def ground_truth(db_session: AsyncSession) -> GroundTruthManager:
    """Ground truth manager for database queries."""
    return GroundTruthManager(db_session)


# =============================================================================
# HELPER FIXTURES
# =============================================================================

@pytest.fixture
def test_companies() -> dict:
    """Dictionary of test companies."""
    return TEST_COMPANIES


@pytest.fixture
def test_bonds() -> dict:
    """Dictionary of test bonds."""
    return TEST_BONDS


@pytest.fixture
def make_api_request(api_client: httpx.Client):
    """Factory for making API requests with error handling."""
    def _make_request(method: str, endpoint: str, **kwargs) -> dict:
        if method.upper() == "GET":
            response = api_client.get(endpoint, **kwargs)
        elif method.upper() == "POST":
            response = api_client.post(endpoint, **kwargs)
        else:
            raise ValueError(f"Unsupported method: {method}")

        response.raise_for_status()
        return response.json()

    return _make_request


# =============================================================================
# MARKERS AND CONFIGURATION
# =============================================================================

def pytest_configure(config):
    """Register custom markers."""
    config.addinivalue_line("markers", "eval: marks test as an eval test")
    config.addinivalue_line("markers", "slow: marks test as slow running")
    config.addinivalue_line("markers", "requires_db: marks test as requiring database")
    config.addinivalue_line("markers", "requires_pricing: marks test as requiring bond pricing data")


def pytest_collection_modifyitems(config, items):
    """Add markers to tests based on location."""
    for item in items:
        # Add eval marker to all tests in eval directory
        if "tests/eval" in str(item.fspath):
            item.add_marker(pytest.mark.eval)
