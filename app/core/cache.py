"""Redis cache client for DebtStack API."""

from typing import Optional, Tuple
import redis.asyncio as redis
from app.core.config import get_settings


# Rate limiting defaults
DEFAULT_RATE_LIMIT = 100  # requests per window
DEFAULT_RATE_WINDOW = 60  # seconds


_redis_client: Optional[redis.Redis] = None


async def get_redis() -> Optional[redis.Redis]:
    """Get Redis client, creating if needed."""
    global _redis_client

    settings = get_settings()
    if not settings.redis_url:
        return None

    if _redis_client is None:
        # Upstash requires TLS - the rediss:// scheme handles this
        _redis_client = redis.from_url(
            settings.redis_url,
            encoding="utf-8",
            decode_responses=True,
            ssl_cert_reqs=None,  # Don't verify SSL cert (Upstash uses self-signed)
        )

    return _redis_client


async def close_redis():
    """Close Redis connection."""
    global _redis_client
    if _redis_client:
        await _redis_client.close()
        _redis_client = None


async def cache_get(key: str) -> Optional[str]:
    """Get value from cache."""
    client = await get_redis()
    if client:
        try:
            return await client.get(key)
        except Exception:
            return None
    return None


async def cache_set(key: str, value: str, ttl_seconds: int = 3600) -> bool:
    """Set value in cache with TTL."""
    client = await get_redis()
    if client:
        try:
            await client.setex(key, ttl_seconds, value)
            return True
        except Exception:
            return False
    return False


async def cache_delete(key: str) -> bool:
    """Delete value from cache."""
    client = await get_redis()
    if client:
        try:
            await client.delete(key)
            return True
        except Exception:
            return False
    return False


async def cache_ping() -> tuple[bool, str]:
    """Check if Redis is reachable. Returns (success, message)."""
    client = await get_redis()
    if client:
        try:
            await client.ping()
            return True, "connected"
        except Exception as e:
            return False, str(e)
    return False, "no client"


async def check_rate_limit(
    identifier: str,
    limit: int = DEFAULT_RATE_LIMIT,
    window: int = DEFAULT_RATE_WINDOW,
) -> Tuple[bool, int, int]:
    """
    Check and update rate limit for an identifier.

    Uses sliding window counter algorithm with Redis.

    Args:
        identifier: Unique identifier (IP address, API key, etc.)
        limit: Maximum requests allowed per window
        window: Window size in seconds

    Returns:
        Tuple of (allowed, remaining, reset_seconds)
        - allowed: True if request should be allowed
        - remaining: Number of requests remaining in window
        - reset_seconds: Seconds until window resets
    """
    client = await get_redis()

    # If Redis unavailable, allow all requests (fail open)
    if not client:
        return True, limit, window

    key = f"ratelimit:{identifier}"

    try:
        # Use Redis pipeline for atomic operations
        pipe = client.pipeline()
        pipe.incr(key)
        pipe.ttl(key)
        results = await pipe.execute()

        current_count = results[0]
        ttl = results[1]

        # Set expiry on first request in window
        if ttl == -1:
            await client.expire(key, window)
            ttl = window

        remaining = max(0, limit - current_count)
        allowed = current_count <= limit

        return allowed, remaining, ttl if ttl > 0 else window

    except Exception:
        # Fail open on Redis errors
        return True, limit, window
