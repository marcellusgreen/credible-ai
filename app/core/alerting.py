"""
Slack alerting for DebtStack API.

Sends notifications to a Slack channel when monitoring detects
high error rates or excessive rate limiting.
"""

from typing import Optional

import httpx
import structlog

from app.core.config import get_settings

logger = structlog.get_logger()

LEVEL_EMOJI = {
    "critical": ":rotating_light:",
    "warning": ":warning:",
    "info": ":information_source:",
}


async def send_slack_alert(message: str, level: str = "warning") -> bool:
    """
    Send an alert to the configured Slack webhook.

    Returns True if sent successfully, False otherwise.
    """
    settings = get_settings()
    if not settings.slack_webhook_url:
        logger.debug("alerting.skip", reason="no slack_webhook_url configured")
        return False

    emoji = LEVEL_EMOJI.get(level, ":mega:")
    payload = {
        "text": f"{emoji} *DebtStack Alert* [{level.upper()}]\n{message}",
    }

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(settings.slack_webhook_url, json=payload)
            resp.raise_for_status()
        logger.info("alerting.sent", level=level)
        return True
    except Exception as exc:
        logger.error("alerting.failed", error=str(exc))
        return False


async def check_and_alert() -> None:
    """
    Run monitoring checks and send Slack alerts for any active alerts.

    Designed to be called on a schedule (e.g. every 15 minutes).
    """
    from app.core.monitoring import check_alerts

    alerts = await check_alerts()
    for alert in alerts:
        await send_slack_alert(
            message=alert["message"],
            level=alert.get("level", "warning"),
        )
