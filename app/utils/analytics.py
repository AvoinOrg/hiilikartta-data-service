"""Best-effort Umami event tracking for calculation requests."""

from datetime import datetime, timezone
from functools import lru_cache
from typing import Optional

import httpx

from app.config import get_settings
from app.utils.logger import get_logger

logger = get_logger(__name__)

DEFAULT_USER_AGENT = "Hiilikartta-Data-Service/1.0"
HIILIKARTTA_HOSTNAME = "hiilikartta.avoin.org"


@lru_cache
def _get_analytics_config():
    settings = get_settings()
    return {
        "enabled": settings.umami_enabled,
        "host_url": settings.umami_host_url.rstrip("/"),
        "website_id": settings.umami_website_id,
    }


def is_analytics_enabled() -> bool:
    config = _get_analytics_config()
    return bool(config["enabled"] and config["host_url"] and config["website_id"])


async def send_event(
    event_name: str,
    event_data: Optional[dict] = None,
    url: str = "/api/calculation",
    user_agent: Optional[str] = None,
) -> bool:
    """Send an Umami event, returning ``False`` for disabled or failed sends."""
    try:
        config = _get_analytics_config()
        if not bool(config["enabled"] and config["host_url"] and config["website_id"]):
            return False

        now = datetime.now(timezone.utc)
        payload = {
            "website": config["website_id"],
            "hostname": HIILIKARTTA_HOSTNAME,
            "url": url,
            "name": event_name,
            "data": {
                "year": now.year,
                "month": now.month,
                "year_month": now.strftime("%Y-%m"),
                **(event_data or {}),
            },
        }

        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.post(
                f"{config['host_url']}/api/send",
                json={"type": "event", "payload": payload},
                headers={
                    "Content-Type": "application/json",
                    "User-Agent": user_agent or DEFAULT_USER_AGENT,
                },
            )
        if response.status_code == 200:
            return True

        logger.warning(
            "Analytics event '%s' failed with status %s",
            event_name,
            response.status_code,
        )
    except httpx.TimeoutException:
        logger.warning("Analytics event '%s' timed out", event_name)
    except Exception as error:
        logger.warning("Analytics event '%s' failed: %s", event_name, error)

    return False


async def track_calculation_initiated(user_agent: Optional[str] = None) -> bool:
    return await send_event("Calculation initiated", user_agent=user_agent)


async def track_calculation_new_plan(user_agent: Optional[str] = None) -> bool:
    return await send_event(
        "Calculation initiated with a new zoning plan", user_agent=user_agent
    )
