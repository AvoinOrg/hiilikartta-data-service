import asyncio
import gzip
import json
import time
from datetime import datetime as _real_datetime
from pathlib import Path
from typing import Optional, cast
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from httpx import AsyncClient
from pytest import MonkeyPatch

pytest_plugins = ["app.db.connection_mock"]

from app.db import connection
from app.db.plan import get_plan_by_ui_id
from app.main import app, get_current_user
from app.saq_worker import calculate_piece
from app.types.general import CalculationStatus

TEST_TIMEOUT_SECONDS = 60
TEST_DATA_PATH = Path("tests/data/test-data-small-polygon.zip")
EXPECTED_RESULTS_PATH = Path("tests/data/test-data-small-polygon-results.geojson")
TEST_USER = {"user_id": "test-user"}

TEST_EXPECTED_CALCULATION_YEAR = 2025
TEST_VALUE_MARGIN_RATIO = 0.15  # 15%
SQM_TO_HA = 1 / 10_000
_ZERO_ABS_TOL = 1e-9

pytestmark = [
    pytest.mark.order(103),
    pytest.mark.usefixtures("monkeypatch_get_async_context_db"),
]


async def _fake_user():
    return TEST_USER


app.dependency_overrides[get_current_user] = _fake_user


@pytest.fixture(scope="session")
def event_loop():
    policy = asyncio.get_event_loop_policy()
    loop = policy.new_event_loop()
    asyncio.set_event_loop(loop)
    yield loop
    loop.close()


@pytest_asyncio.fixture(scope="session")
async def async_client():
    async with AsyncClient(app=app, base_url="http://testserver") as client:
        yield client


@pytest.fixture(scope="session", autouse=True)
def freeze_calculator_year():
    import app.calculator.calculator as calculator_module

    class _FixedDatetime(_real_datetime):
        @classmethod
        def now(cls, tz=None):  # type: ignore[override]
            return _real_datetime(TEST_EXPECTED_CALCULATION_YEAR, 1, 1, tzinfo=tz)

        @classmethod
        def utcnow(cls):  # type: ignore[override]
            return _real_datetime(TEST_EXPECTED_CALCULATION_YEAR, 1, 1)

    m = MonkeyPatch()
    m.setattr(calculator_module, "datetime", _FixedDatetime)
    yield
    m.undo()


def _decode_gzip_json(response):
    content = response.content
    if content[:2] == b"\x1f\x8b":
        content = gzip.decompress(content)
    return json.loads(content)


async def _post_testarea(
    client: AsyncClient,
    plan_id: Optional[UUID] = None,
    visible_id: Optional[str] = None,
    name: Optional[str] = None,
    forestry_scenario: Optional[int] = None,
):
    plan_id = plan_id or uuid4()
    visible_id = visible_id or f"visible-{uuid4().hex[:6]}"
    name = name or f"test-plan-{plan_id.hex[:8]}"

    with TEST_DATA_PATH.open("rb") as f:
        files = {"file": (TEST_DATA_PATH.name, f, "application/zip")}
        params = {"id": str(plan_id), "visible_id": visible_id, "name": name}
        if forestry_scenario is not None:
            params["forestry_scenario"] = str(forestry_scenario)
        response = await client.post(
            "/calculation",
            params=params,
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


def _feature_id(feature):
    if "id" in feature and feature["id"] is not None:
        return str(feature["id"])
    props = feature.get("properties") or {}
    if "id" in props and props["id"] is not None:
        return str(props["id"])
    return None


def _flatten_expected_properties(properties):
    flattened = {}
    for key, value in properties.items():
        if (
            isinstance(value, dict)
            and value
            and all(isinstance(v, dict) for v in value.values())
        ):
            for scenario, by_year in value.items():
                for year, numeric in by_year.items():
                    flattened[f"{key}_{scenario}_{year}"] = numeric
            continue
        flattened[key] = value
    return flattened


def _load_expected_features():
    expected_geojson = json.loads(EXPECTED_RESULTS_PATH.read_text())
    features = expected_geojson.get("features") or []
    assert features, f"No features found in {EXPECTED_RESULTS_PATH}"
    return features


def _assert_values_within_margin(actual_properties, expected_properties, *, scope_label):
    for key, expected in expected_properties.items():
        assert key in actual_properties, f"{scope_label}: missing key {key}"
        actual = actual_properties[key]

        if isinstance(expected, (int, float)) and not isinstance(expected, bool):
            assert isinstance(actual, (int, float)) and not isinstance(actual, bool), (
                f"{scope_label}: {key} expected a number, got {type(actual).__name__}"
            )
            expected_float = float(expected)
            actual_float = float(actual)

            if expected_float == 0:
                lower, upper = -_ZERO_ABS_TOL, _ZERO_ABS_TOL
            else:
                tolerance = abs(expected_float) * TEST_VALUE_MARGIN_RATIO
                lower, upper = expected_float - tolerance, expected_float + tolerance
                if lower > upper:
                    lower, upper = upper, lower

            assert lower <= actual_float <= upper, (
                f"{scope_label}: {key}={actual_float} outside expected range "
                f"[{lower}, {upper}] for expected={expected_float}"
            )
            continue

        assert actual == expected, (
            f"{scope_label}: {key}={actual!r} does not match expected={expected!r}"
        )


def _aggregate_expected_totals(expected_flat_properties_by_feature):
    area_sum = sum(float(props["area"]) for props in expected_flat_properties_by_feature)
    aggregated = {"area": area_sum}

    for props in expected_flat_properties_by_feature:
        for key, value in props.items():
            if (
                isinstance(value, (int, float))
                and not isinstance(value, bool)
                and "_total_" in key
            ):
                aggregated[key] = aggregated.get(key, 0.0) + float(value)

    for key, value in list(aggregated.items()):
        if key == "area" or "_total_" not in key:
            continue
        aggregated[key.replace("_total_", "_ha_")] = value / (area_sum * SQM_TO_HA)

    return aggregated


@pytest.mark.asyncio
async def test_post_testarea1_starts_calculation(async_client):
    _, _, _, response = await _post_testarea(async_client)
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == CalculationStatus.PROCESSING.value
    assert body["forestry_scenario"] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("scenario", [2, 3])
async def test_post_calculation_accepts_forestry_scenario(async_client, scenario):
    plan_id, _, _, response = await _post_testarea(
        async_client, forestry_scenario=scenario
    )
    assert response.status_code == 200
    assert response.json()["forestry_scenario"] == scenario

    async with connection.get_async_context_state_db() as session:
        plan = await get_plan_by_ui_id(session, plan_id)

    assert plan is not None
    assert plan.forestry_scenario == scenario


@pytest.mark.asyncio
@pytest.mark.parametrize("scenario", ["0", "4", "abc"])
async def test_post_calculation_rejects_invalid_forestry_scenario(async_client, scenario):
    plan_id = uuid4()
    with TEST_DATA_PATH.open("rb") as f:
        files = {"file": (TEST_DATA_PATH.name, f, "application/zip")}
        response = await async_client.post(
            "/calculation",
            params={
                "id": str(plan_id),
                "visible_id": f"visible-{plan_id.hex[:6]}",
                "name": f"invalid-{plan_id.hex[:6]}",
                "forestry_scenario": scenario,
            },
            files=files,
        )

    assert response.status_code == 400


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

    areas_geojson = _parse_geojson_field(payload["data"]["areas"])
    area_features = areas_geojson.get("features") or []
    assert area_features, "No features found in calculation areas output"

    expected_features = _load_expected_features()
    expected_flat_by_id = {}
    expected_flat_list = []
    for index, feature in enumerate(expected_features):
        feature_key = _feature_id(feature) or str(index)
        flattened = _flatten_expected_properties(feature.get("properties") or {})
        expected_flat_by_id[feature_key] = flattened
        expected_flat_list.append(flattened)

    actual_by_id = {}
    for index, feature in enumerate(area_features):
        feature_key = _feature_id(feature) or str(index)
        actual_by_id[feature_key] = feature.get("properties") or {}

    assert len(actual_by_id) == len(expected_flat_by_id), (
        f"Expected {len(expected_flat_by_id)} area features, got {len(actual_by_id)}"
    )

    for feature_key, expected_props in expected_flat_by_id.items():
        assert feature_key in actual_by_id, f"Missing area feature {feature_key}"
        _assert_values_within_margin(
            actual_by_id[feature_key], expected_props, scope_label=f"areas[{feature_key}]"
        )

    expected_totals = _aggregate_expected_totals(expected_flat_list)
    _assert_values_within_margin(totals_properties, expected_totals, scope_label="totals")


@pytest.mark.asyncio
async def test_finished_payloads_include_forestry_scenario(async_client):
    forestry_scenario = 2
    plan_id, _, _, response = await _post_testarea(
        async_client, forestry_scenario=forestry_scenario
    )
    assert response.status_code == 200

    await _wait_for_completion(plan_id)

    calculation_payload = await _fetch_calculation_payload(async_client, plan_id)
    assert calculation_payload["forestry_scenario"] == forestry_scenario
    assert (
        calculation_payload["data"]["metadata"]["forestry_scenario"] == forestry_scenario
    )

    plan_response = await async_client.get("/plan", params={"id": str(plan_id)})
    assert plan_response.status_code == 200
    plan_payload = _decode_gzip_json(plan_response)
    assert plan_payload["forestry_scenario"] == forestry_scenario
    assert (
        plan_payload["report_data"]["metadata"]["forestry_scenario"] == forestry_scenario
    )

    external_response = await async_client.get(
        "/plan/external", params={"id": str(plan_id)}
    )
    assert external_response.status_code == 200
    external_payload = _decode_gzip_json(external_response)
    assert external_payload["forestry_scenario"] == forestry_scenario
    assert (
        external_payload["report_data"]["metadata"]["forestry_scenario"]
        == forestry_scenario
    )


@pytest.mark.asyncio
async def test_current_year_planned_matches_nochange(async_client):
    plan_id, _, _, response = await _post_testarea(async_client)
    assert response.status_code == 200

    await _wait_for_completion(plan_id)
    payload = await _fetch_calculation_payload(async_client, plan_id)

    totals_geojson = _parse_geojson_field(payload["data"]["totals"])
    totals_props = totals_geojson["features"][0]["properties"]

    assert (
        totals_props["bio_carbon_total_planned_2025"]
        == totals_props["bio_carbon_total_nochange_2025"]
    )
    assert (
        totals_props["ground_carbon_total_planned_2025"]
        == totals_props["ground_carbon_total_nochange_2025"]
    )
    assert totals_props["bio_carbon_ha_planned_2025"] == totals_props["bio_carbon_ha_nochange_2025"]
    assert (
        totals_props["ground_carbon_ha_planned_2025"]
        == totals_props["ground_carbon_ha_nochange_2025"]
    )


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


@pytest.mark.asyncio
async def test_calculate_piece_requeues_on_capacity(async_client, monkeypatch):
    plan_id, _, _, response = await _post_testarea(async_client)
    assert response.status_code == 200

    from app import saq_worker
    from app.calculator.calculator import CarbonCalculator
    from app.db.connection_mock import InlineQueue
    from app.db.errors import GisRetryLaterError

    async def fake_calculate(self):  # type: ignore[no-untyped-def]
        raise GisRetryLaterError("GIS at capacity", retry_in_seconds=12.0)

    monkeypatch.setattr(CarbonCalculator, "calculate", fake_calculate)
    queue_stub = cast(InlineQueue, saq_worker.queue)
    queue_stub.enqueued.clear()

    start = time.time()
    await calculate_piece({}, ui_id=str(plan_id))

    assert queue_stub.enqueued, "Expected calculate_piece to be re-enqueued"
    call = queue_stub.enqueued[-1]
    assert call["function"] == "calculate_piece"
    kwargs = call["kwargs"]
    assert kwargs["ui_id"] == str(plan_id)
    assert kwargs["retries"] == 0
    assert "scheduled" in kwargs
    assert start + 11.0 <= kwargs["scheduled"] <= start + 14.0

    async with connection.get_async_context_state_db() as session:
        plan = await get_plan_by_ui_id(session, plan_id)

    assert plan is not None
    assert plan.last_index == -1
    assert plan.last_area_calculation_retries == 0
    assert plan.last_area_calculation_status.value == CalculationStatus.PROCESSING.value


@pytest.mark.asyncio
async def test_calculate_piece_skips_only_timed_out_feature(async_client, monkeypatch):
    plan_id, _, _, response = await _post_testarea(async_client)
    assert response.status_code == 200

    from app import saq_worker
    from app.calculator.calculator import CarbonCalculator
    from app.db.connection_mock import InlineQueue
    from app.db.errors import GisOperationTimedOutError

    async def fake_calculate(self):  # type: ignore[no-untyped-def]
        raise GisOperationTimedOutError("statement timeout")

    monkeypatch.setattr(CarbonCalculator, "calculate", fake_calculate)
    queue_stub = cast(InlineQueue, saq_worker.queue)
    queue_stub.enqueued.clear()

    await calculate_piece({}, ui_id=str(plan_id))

    assert queue_stub.enqueued, "Expected calculate_piece to be re-enqueued"
    call = queue_stub.enqueued[-1]
    assert call["function"] == "calculate_piece"
    kwargs = call["kwargs"]
    assert kwargs["ui_id"] == str(plan_id)
    assert "scheduled" not in kwargs

    async with connection.get_async_context_state_db() as session:
        plan = await get_plan_by_ui_id(session, plan_id)

    assert plan is not None
    assert plan.calculation_status.value == CalculationStatus.PROCESSING.value
    assert plan.last_area_calculation_status.value == CalculationStatus.ERROR.value
    assert plan.last_area_calculation_retries == 0
    assert plan.last_index == 0
