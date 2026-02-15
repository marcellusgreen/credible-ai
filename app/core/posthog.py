"""PostHog analytics client for backend event tracking."""

from typing import Any, Dict, Optional

from app.core.config import get_settings

settings = get_settings()

_posthog_client = None

if settings.posthog_api_key:
    try:
        import posthog

        posthog.project_api_key = settings.posthog_api_key
        posthog.host = settings.posthog_host
        posthog.debug = settings.debug
        _posthog_client = posthog
    except Exception:
        _posthog_client = None


def capture_event(
    distinct_id: str,
    event: str,
    properties: Optional[Dict[str, Any]] = None,
) -> None:
    """Capture an event in PostHog. No-op if PostHog is not configured."""
    if _posthog_client is None:
        return
    try:
        _posthog_client.capture(distinct_id, event, properties=properties or {})
    except Exception:
        pass


def identify_user(
    distinct_id: str,
    properties: Optional[Dict[str, Any]] = None,
) -> None:
    """Identify a user in PostHog. No-op if PostHog is not configured."""
    if _posthog_client is None:
        return
    try:
        _posthog_client.identify(distinct_id, properties=properties or {})
    except Exception:
        pass


def shutdown() -> None:
    """Flush pending events and shut down the PostHog client."""
    if _posthog_client is None:
        return
    try:
        _posthog_client.shutdown()
    except Exception:
        pass
