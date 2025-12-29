"""
Umami Analytics integration for tracking calculation events.

This module provides functionality to send custom events to Umami analytics.
Events are only sent when UMAMI_ENABLED=true (production environment).

Tracked events:
- calculation_initiated: Fired for every calculation request (total calculations)
- calculation_new_plan: Fired only for calculations on new plans (unique plans)

Both events include a timestamp for monthly aggregation in Umami.
"""

import httpx
from datetime import datetime, timezone
from typing import Optional
from functools import lru_cache

from app.config import get_settings
from app.utils.logger import get_logger

logger = get_logger(__name__)


@lru_cache
def _get_analytics_config():
    """Get analytics configuration from settings."""
    settings = get_settings()
    return {
        "enabled": settings.umami_enabled,
        "host_url": settings.umami_host_url.rstrip("/") if settings.umami_host_url else "",
        "website_id": settings.umami_website_id,
    }


def is_analytics_enabled() -> bool:
    """Check if analytics is enabled."""
    config = _get_analytics_config()
    return config["enabled"] and config["host_url"] and config["website_id"]


async def send_event(
    event_name: str,
    event_data: Optional[dict] = None,
    url: str = "/api/calculation",
    user_agent: Optional[str] = None,
) -> bool:
    """
    Send a custom event to Umami analytics.
    
    Args:
        event_name: Name of the event (e.g., 'calculation_initiated', 'calculation_new_plan')
        event_data: Optional dictionary of additional event data
        url: The URL/page to associate with this event
        user_agent: User agent string from the client (forwarded via X-User-Agent header)
    
    Returns:
        True if the event was sent successfully, False otherwise
    """
    config = _get_analytics_config()
    
    if not is_analytics_enabled():
        logger.info(
            f"Analytics disabled - enabled: {config['enabled']}, "
            f"host_url: {'set' if config['host_url'] else 'empty'}, "
            f"website_id: {'set' if config['website_id'] else 'empty'}"
        )
        return False
    
    # Add timestamp for monthly tracking
    now = datetime.now(timezone.utc)
    data = {
        "year": now.year,
        "month": now.month,
        "year_month": now.strftime("%Y-%m"),
        **(event_data or {}),
    }
    
    payload = {
        "website": config["website_id"],
        "hostname": "hiilikartta.avoin.org",
        "url": url,
        "name": event_name,
        "data": data,
    }
    
    headers = {
        "Content-Type": "application/json",
        "User-Agent": user_agent or "Hiilikartta-Data-Service/1.0",
    }
    
    try:
        logger.info(f"Sending analytics event '{event_name}' to {config['host_url']}/api/send")
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.post(
                f"{config['host_url']}/api/send",
                json={"type": "event", "payload": payload},
                headers=headers,
            )
            
            if response.status_code == 200:
                logger.info(f"Analytics event '{event_name}' sent successfully")
                return True
            else:
                logger.warning(
                    f"Analytics event '{event_name}' failed with status {response.status_code}: {response.text}"
                )
                return False
                
    except httpx.TimeoutException:
        logger.warning(f"Analytics event '{event_name}' timed out")
        return False
    except Exception as e:
        logger.warning(f"Analytics event '{event_name}' failed: {e}")
        return False


async def track_calculation_initiated(user_agent: Optional[str] = None) -> bool:
    """
    Track that a calculation was initiated.
    This is called for ALL calculations (new and re-calculations).
    
    Use this metric for: Total number of calculations initiated.
    
    Args:
        user_agent: User agent string from the client
    """
    return await send_event("Calculation initiated", user_agent=user_agent)


async def track_calculation_new_plan(user_agent: Optional[str] = None) -> bool:
    """
    Track that a calculation was initiated for a NEW plan.
    This is called only when creating a new plan, not for re-calculations.
    
    Use this metric for: Number of calculations for unique plans.
    
    Args:
        user_agent: User agent string from the client
    """
    return await send_event("Calculation initiated with a new zoning plan", user_agent=user_agent)
