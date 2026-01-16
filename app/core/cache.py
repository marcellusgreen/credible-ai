"""Redis cache client for DebtStack API."""

from typing import Optional
import redis.asyncio as redis
from app.core.config import get_settings


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
