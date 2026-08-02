from unittest.mock import AsyncMock

import pytest

from app.utils import analytics


class MockAsyncClient:
    def __init__(self, post=None, enter_error=None):
        self.post = post or AsyncMock()
        self.enter_error = enter_error

    async def __aenter__(self):
        if self.enter_error:
            raise self.enter_error
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False


@pytest.mark.asyncio
async def test_missing_config_disables_analytics(monkeypatch):
    monkeypatch.setattr(
        analytics,
        "_get_analytics_config",
        lambda: {"enabled": True, "host_url": "", "website_id": ""},
    )
    monkeypatch.setattr(
        analytics.httpx,
        "AsyncClient",
        lambda **kwargs: pytest.fail("analytics client should not be created"),
    )

    assert await analytics.track_calculation_initiated() is False


@pytest.mark.asyncio
async def test_missing_user_agent_uses_service_default(monkeypatch):
    monkeypatch.setattr(
        analytics,
        "_get_analytics_config",
        lambda: {
            "enabled": True,
            "host_url": "https://analytics.example.org",
            "website_id": "website-id",
        },
    )
    response = type("Response", (), {"status_code": 200})()
    post = AsyncMock(return_value=response)
    monkeypatch.setattr(
        analytics.httpx,
        "AsyncClient",
        lambda **kwargs: MockAsyncClient(post=post),
    )

    assert await analytics.track_calculation_initiated() is True
    assert (
        post.await_args.kwargs["headers"]["User-Agent"] == analytics.DEFAULT_USER_AGENT
    )
    assert post.await_args.kwargs["json"]["payload"]["website"] == "website-id"


@pytest.mark.asyncio
async def test_client_failure_is_contained(monkeypatch):
    monkeypatch.setattr(
        analytics,
        "_get_analytics_config",
        lambda: {
            "enabled": True,
            "host_url": "https://analytics.example.org",
            "website_id": "website-id",
        },
    )
    monkeypatch.setattr(
        analytics.httpx,
        "AsyncClient",
        lambda **kwargs: MockAsyncClient(enter_error=RuntimeError("unavailable")),
    )

    assert await analytics.track_calculation_initiated() is False


@pytest.mark.asyncio
async def test_config_failure_is_contained(monkeypatch):
    def unavailable_config():
        raise RuntimeError("config unavailable")

    monkeypatch.setattr(analytics, "_get_analytics_config", unavailable_config)

    assert await analytics.track_calculation_initiated() is False
