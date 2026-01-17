"""
Monitoring and analytics for DebtStack API.

Tracks:
- Request counts by endpoint
- Response latency percentiles
- Error rates by status code
- Rate limit hits

All metrics are stored in Redis with TTL for automatic cleanup.
"""

from datetime import datetime, timedelta
from typing import Optional, Dict, Any
import json

from app.core.cache import get_redis


# Redis key prefixes
METRICS_PREFIX = "metrics:"
HOURLY_PREFIX = f"{METRICS_PREFIX}hourly:"
DAILY_PREFIX = f"{METRICS_PREFIX}daily:"


async def record_request(
    path: str,
    method: str,
    status_code: int,
    duration_ms: float,
    client_ip: str,
) -> None:
    """
    Record a request for analytics.

    Stores hourly and daily aggregates in Redis.
    """
    client = await get_redis()
    if not client:
        return  # Skip if Redis unavailable

    try:
        now = datetime.utcnow()
        hour_key = now.strftime("%Y-%m-%d-%H")
        day_key = now.strftime("%Y-%m-%d")

        # Normalize path (remove specific IDs/tickers)
        normalized_path = _normalize_path(path)

        # Pipeline for atomic updates
        pipe = client.pipeline()

        # Hourly metrics (TTL: 48 hours)
        hourly_key = f"{HOURLY_PREFIX}{hour_key}"
        pipe.hincrby(hourly_key, "total_requests", 1)
        pipe.hincrby(hourly_key, f"path:{normalized_path}", 1)
        pipe.hincrby(hourly_key, f"status:{status_code}", 1)
        pipe.hincrby(hourly_key, f"method:{method}", 1)
        pipe.expire(hourly_key, 48 * 3600)  # 48 hours TTL

        # Track latency buckets (for percentile estimation)
        latency_bucket = _get_latency_bucket(duration_ms)
        pipe.hincrby(hourly_key, f"latency:{latency_bucket}", 1)

        # Daily metrics (TTL: 30 days)
        daily_key = f"{DAILY_PREFIX}{day_key}"
        pipe.hincrby(daily_key, "total_requests", 1)
        pipe.hincrby(daily_key, f"path:{normalized_path}", 1)
        pipe.hincrby(daily_key, f"status:{status_code}", 1)
        pipe.hincrbyfloat(daily_key, "total_latency_ms", duration_ms)
        pipe.expire(daily_key, 30 * 24 * 3600)  # 30 days TTL

        # Track unique IPs (daily)
        pipe.sadd(f"{daily_key}:ips", client_ip)
        pipe.expire(f"{daily_key}:ips", 30 * 24 * 3600)

        # Track errors separately for alerting
        if status_code >= 500:
            pipe.hincrby(hourly_key, "errors_5xx", 1)
            pipe.hincrby(daily_key, "errors_5xx", 1)
        elif status_code >= 400:
            pipe.hincrby(hourly_key, "errors_4xx", 1)
            pipe.hincrby(daily_key, "errors_4xx", 1)

        await pipe.execute()

    except Exception:
        pass  # Don't fail requests due to monitoring errors


async def record_rate_limit_hit(client_ip: str) -> None:
    """Record a rate limit hit for monitoring."""
    client = await get_redis()
    if not client:
        return

    try:
        now = datetime.utcnow()
        hour_key = f"{HOURLY_PREFIX}{now.strftime('%Y-%m-%d-%H')}"
        day_key = f"{DAILY_PREFIX}{now.strftime('%Y-%m-%d')}"

        pipe = client.pipeline()
        pipe.hincrby(hour_key, "rate_limit_hits", 1)
        pipe.hincrby(day_key, "rate_limit_hits", 1)
        await pipe.execute()
    except Exception:
        pass


async def get_hourly_metrics(hours_back: int = 24) -> list[Dict[str, Any]]:
    """Get hourly metrics for the last N hours."""
    client = await get_redis()
    if not client:
        return []

    try:
        metrics = []
        now = datetime.utcnow()

        for i in range(hours_back):
            hour = now - timedelta(hours=i)
            hour_key = f"{HOURLY_PREFIX}{hour.strftime('%Y-%m-%d-%H')}"

            data = await client.hgetall(hour_key)
            if data:
                metrics.append({
                    "hour": hour.strftime("%Y-%m-%d %H:00"),
                    "total_requests": int(data.get("total_requests", 0)),
                    "errors_4xx": int(data.get("errors_4xx", 0)),
                    "errors_5xx": int(data.get("errors_5xx", 0)),
                    "rate_limit_hits": int(data.get("rate_limit_hits", 0)),
                })

        return metrics
    except Exception:
        return []


async def get_daily_metrics(days_back: int = 7) -> list[Dict[str, Any]]:
    """Get daily metrics for the last N days."""
    client = await get_redis()
    if not client:
        return []

    try:
        metrics = []
        now = datetime.utcnow()

        for i in range(days_back):
            day = now - timedelta(days=i)
            day_key = f"{DAILY_PREFIX}{day.strftime('%Y-%m-%d')}"

            data = await client.hgetall(day_key)
            if data:
                total_requests = int(data.get("total_requests", 0))
                total_latency = float(data.get("total_latency_ms", 0))

                # Get unique IPs count
                unique_ips = await client.scard(f"{day_key}:ips")

                metrics.append({
                    "date": day.strftime("%Y-%m-%d"),
                    "total_requests": total_requests,
                    "unique_clients": unique_ips or 0,
                    "avg_latency_ms": round(total_latency / total_requests, 2) if total_requests > 0 else 0,
                    "errors_4xx": int(data.get("errors_4xx", 0)),
                    "errors_5xx": int(data.get("errors_5xx", 0)),
                    "rate_limit_hits": int(data.get("rate_limit_hits", 0)),
                })

        return metrics
    except Exception:
        return []


async def get_endpoint_breakdown(day: Optional[str] = None) -> Dict[str, int]:
    """Get request counts by endpoint for a specific day."""
    client = await get_redis()
    if not client:
        return {}

    try:
        if day is None:
            day = datetime.utcnow().strftime("%Y-%m-%d")

        day_key = f"{DAILY_PREFIX}{day}"
        data = await client.hgetall(day_key)

        endpoints = {}
        for key, value in data.items():
            if key.startswith("path:"):
                path = key[5:]  # Remove "path:" prefix
                endpoints[path] = int(value)

        return dict(sorted(endpoints.items(), key=lambda x: x[1], reverse=True))
    except Exception:
        return {}


async def check_alerts() -> list[Dict[str, Any]]:
    """
    Check for alert conditions.

    Returns list of active alerts.
    """
    alerts = []

    try:
        hourly = await get_hourly_metrics(hours_back=1)
        if hourly:
            current = hourly[0]

            # High error rate alert (>5% 5xx errors)
            total = current.get("total_requests", 0)
            errors_5xx = current.get("errors_5xx", 0)
            if total > 10 and errors_5xx / total > 0.05:
                alerts.append({
                    "level": "critical",
                    "type": "high_error_rate",
                    "message": f"High 5xx error rate: {errors_5xx}/{total} ({errors_5xx/total*100:.1f}%)",
                })

            # High rate limit hits (>10% of requests)
            rate_limits = current.get("rate_limit_hits", 0)
            if total > 10 and rate_limits / total > 0.10:
                alerts.append({
                    "level": "warning",
                    "type": "high_rate_limiting",
                    "message": f"High rate limit hits: {rate_limits}/{total} ({rate_limits/total*100:.1f}%)",
                })
    except Exception:
        pass

    return alerts


def _normalize_path(path: str) -> str:
    """Normalize path by replacing dynamic segments with placeholders."""
    import re

    # Replace ticker patterns (e.g., /companies/AAPL -> /companies/{ticker})
    path = re.sub(r'/companies/[A-Z0-9]+/', '/companies/{ticker}/', path)
    path = re.sub(r'/companies/[A-Z0-9]+$', '/companies/{ticker}', path)

    # Replace UUIDs
    path = re.sub(r'/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}', '/{id}', path)

    return path


def _get_latency_bucket(duration_ms: float) -> str:
    """Get latency bucket for histogram."""
    if duration_ms < 50:
        return "0-50ms"
    elif duration_ms < 100:
        return "50-100ms"
    elif duration_ms < 250:
        return "100-250ms"
    elif duration_ms < 500:
        return "250-500ms"
    elif duration_ms < 1000:
        return "500-1000ms"
    else:
        return "1000ms+"
