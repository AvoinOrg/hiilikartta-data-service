import asyncio
import gzip
import json
import time
from pathlib import Path
from typing import Optional
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from httpx import AsyncClient

pytest_plugins = ["app.db.connection_mock"]

from app.db import connection
from app.db.connection_mock import install_inline_queue
from app.db.plan import get_plan_by_ui_id
from app.main import app, get_current_user
from app.saq_worker import calculate_piece
from app.types.general import CalculationStatus

TEST_TIMEOUT_SECONDS = 60
TEST_DATA_PATH = Path("tests/data/testarea1.zip")
TEST_USER = {"user_id": "test-user"}

# Ranges will be filled in later; when left as None the test is skipped.
EXPECTED_TOTAL_RANGES = {
    # "bio_carbon_total_nochange_2023": (1000, 2000),
    # "ground_carbon_total_nochange_2023": (1000, 2000),
}
EXPECTED_AREA_RANGES = {
    # "bio_carbon_total_nochange_2023": (100, 200),
    # "ground_carbon_total_nochange_2023": (100, 200),
}

pytestmark = [
    pytest.mark.order(103),
    pytest.mark.usefixtures("monkeypatch_get_async_context_db"),
]

install_inline_queue()


async def _fake_user():
    return TEST_USER


app.dependency_overrides[get_current_user] = _fake_user


@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


@pytest_asyncio.fixture(scope="session")
async def async_client():
    async with AsyncClient(app=app, base_url="http://testserver") as client:
        yield client


def _decode_gzip_json(response):
    return json.loads(gzip.decompress(response.content))


async def _post_testarea(
    client: AsyncClient,
    plan_id: Optional[UUID] = None,
    visible_id: Optional[str] = None,
    name: Optional[str] = None,
):
    plan_id = plan_id or uuid4()
    visible_id = visible_id or f"visible-{uuid4().hex[:6]}"
    name = name or f"test-plan-{plan_id.hex[:8]}"

    with TEST_DATA_PATH.open("rb") as f:
        files = {"file": (TEST_DATA_PATH.name, f, "application/zip")}
        response = await client.post(
            "/calculation",
            params={"id": str(plan_id), "visible_id": visible_id, "name": name},
            files=files,
        )

    return plan_id, visible_id, name, response


async def _wait_for_completion(plan_id: UUID):
    start = time.monotonic()

    while time.monotonic() - start < TEST_TIMEOUT_SECONDS:
        await calculate_piece({}, ui_id=str(plan_id))

        async with connection.get_async_context_state_db() as session:
            plan = await get_plan_by_ui_id(session, plan_id)

        if plan and plan.calculation_status.value == CalculationStatus.FINISHED.value:
            return plan, time.monotonic() - start

        await asyncio.sleep(0.5)

    raise AssertionError("Calculation did not complete within the allotted time.")


async def _fetch_calculation_payload(client: AsyncClient, plan_id: UUID):
    response = await client.get("/calculation", params={"id": str(plan_id)})
    assert response.status_code == 200
    return _decode_gzip_json(response)


def _parse_geojson_field(raw_value):
    if isinstance(raw_value, str):
        return json.loads(raw_value)
    return raw_value


def _assert_ranges(values, expected_ranges, scope_label):
    if not expected_ranges:
        pytest.skip(f"Add expected ranges for {scope_label}")

    unset_ranges = [
        key for key, bounds in expected_ranges.items() if None in (bounds or [])
    ]
    if unset_ranges:
        pytest.skip(
            f"Set expected ranges for {scope_label}: {', '.join(sorted(unset_ranges))}"
        )

    for key, (lower, upper) in expected_ranges.items():
        assert key in values, f"{key} missing from {scope_label}"
        assert lower <= values[key] <= upper, (
            f"{scope_label} value for {key} "
            f"({values[key]}) outside expected range ({lower}, {upper})"
        )


@pytest.mark.asyncio
async def test_post_testarea1_starts_calculation(async_client):
    _, _, _, response = await _post_testarea(async_client)
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == CalculationStatus.PROCESSING.value


@pytest.mark.asyncio
async def test_calculation_finishes_within_timeout(async_client):
    plan_id, _, _, response = await _post_testarea(async_client)
    assert response.status_code == 200

    plan, duration = await _wait_for_completion(plan_id)
    assert plan.calculation_status.value == CalculationStatus.FINISHED.value
    assert duration < TEST_TIMEOUT_SECONDS


@pytest.mark.asyncio
async def test_calculation_values_match_ranges(async_client):
    plan_id, _, _, response = await _post_testarea(async_client)
    assert response.status_code == 200

    await _wait_for_completion(plan_id)
    payload = await _fetch_calculation_payload(async_client, plan_id)

    totals_geojson = _parse_geojson_field(payload["data"]["totals"])
    totals_properties = totals_geojson["features"][0]["properties"]
    _assert_ranges(totals_properties, EXPECTED_TOTAL_RANGES, "totals")

    areas_geojson = _parse_geojson_field(payload["data"]["areas"])
    area_properties = [feature["properties"] for feature in areas_geojson["features"]]
    if area_properties:
        _assert_ranges(area_properties[0], EXPECTED_AREA_RANGES, "areas")


@pytest.mark.asyncio
async def test_recalculation_for_existing_plan(async_client):
    plan_id = uuid4()
    visible_id = f"visible-{plan_id.hex[:6]}"
    name = f"recalc-{plan_id.hex[:6]}"

    _, _, _, first_response = await _post_testarea(
        async_client, plan_id=plan_id, visible_id=visible_id, name=name
    )
    assert first_response.status_code == 200
    first_plan, _ = await _wait_for_completion(plan_id)
    first_saved_ts = first_plan.saved_ts

    _, _, _, second_response = await _post_testarea(
        async_client, plan_id=plan_id, visible_id=visible_id, name=name
    )
    assert second_response.status_code == 200

    second_plan, _ = await _wait_for_completion(plan_id)
    assert second_plan.calculation_status.value == CalculationStatus.FINISHED.value
    assert second_plan.saved_ts >= first_saved_ts


@pytest.mark.asyncio
async def test_deleting_plan_succeeds(async_client):
    plan_id, _, _, response = await _post_testarea(async_client)
    assert response.status_code == 200

    await _wait_for_completion(plan_id)

    delete_response = await async_client.delete(
        "/plan",
        params={"id": str(plan_id)},
    )
    assert delete_response.status_code == 200

    async with connection.get_async_context_state_db() as session:
        assert await get_plan_by_ui_id(session, plan_id) is None
