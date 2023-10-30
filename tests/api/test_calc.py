import pytest
from fastapi import FastAPI
import asyncio
from app.types.general import CalculationStatus
import httpx
from app.main import app
import pytest_asyncio
import geopandas as gpd
import numpy as np


test_data_path = "tests/data/testarea1.zip"
id = "d4ed5b10-2f1a-4c2a-a4e8-9a2d27a344da"


@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


@pytest_asyncio.fixture(scope="session")
async def async_client_fixture():
    async with httpx.AsyncClient(
        app=app, base_url="http://localhost:8000"
    ) as async_client:
        yield async_client  # this allows the test to use the async_client


@pytest_asyncio.fixture(scope="session")
async def post_calculation_result(async_client_fixture):
    with open(test_data_path, "rb") as f:
        files = {"file": ("testarea1.zip", f, "application/zip")}
        data = {"zoning_col": "kaavemerki"}
        response = await async_client_fixture.post(
            f"/calculation?id={id}", data=data, files=files
        )
        return response


@pytest_asyncio.fixture(scope="session")
async def get_calculation_result(async_client_fixture, post_calculation_result):
    assert post_calculation_result.status_code == 200
    status = post_calculation_result.json()["status"]
    for _ in range(10):
        await asyncio.sleep(2)
        status_response = await async_client_fixture.get(f"/calculation?id={id}")
        if status_response.status_code == 200:
            status = status_response.json()["status"]
            if status == CalculationStatus.FINISHED.value:
                return status_response
    assert (
        False
    ), f"Calculation did not complete in expected time. Last known status: {status}"


@pytest.mark.asyncio
async def test_post_calculation(post_calculation_result):
    assert post_calculation_result.status_code == 200


@pytest.mark.asyncio
async def test_get_calculation(get_calculation_result):
    assert get_calculation_result.status_code == 200


@pytest.mark.asyncio
async def test_areas_are_returned(get_calculation_result):
    assert get_calculation_result.json()["data"]["areas"] != None


@pytest.mark.asyncio
async def test_totals_are_returned(get_calculation_result):
    assert get_calculation_result.json()["data"]["totals"] != None


@pytest_asyncio.fixture(scope="session")
async def gdf_response_areas_data(get_calculation_result):
    gdf = gpd.GeoDataFrame(get_calculation_result.json()["data"]["areas"])
    return gdf


@pytest.fixture(scope="session")
async def gdf_response_totals_data(get_calculation_result):
    gdf = gpd.GeoDataFrame(get_calculation_result.json()["data"]["totals"])
    return gdf


@pytest.mark.asyncio
async def test_areas_data_contains_items(gdf_response_areas_data):
    assert len(gdf_response_areas_data) > 0


@pytest.mark.asyncio
async def test_totals_data_contains_items(gdf_response_areas_data):
    assert len(gdf_response_areas_data) > 0


# @pytest.fixture(scope="session")
# def gdf_test_data():
#     test_gdf = gpd.read_file(test_data_path)
#     return test_gdf


# def test_calculation_can_be_started_again_after_being_completed():
#     with open(test_data_path, "rb") as f:
#         files = {"file": ("testarea1.zip", f, "application/zip")}
#         data = {"zoning_col": "kaavamerki", "id": id}
#         response = client.post("/calculation", data=data, files=files)
#         assert response.status_code == 200
#         status = response.json()["status"]
#         # Check the calculation status every 2 seconds for a max of 10 times (i.e., wait for up to 20 seconds).
#         for _ in range(20):
#             time.sleep(2)
#             status_response = client.get(f"/calculation?id={id}")
#             if status_response.status_code == 200:
#                 response = client.post("/calculation", data=data, files=files)
#                 assert response.status_code == 200
#         assert (
#             False
#         ), f"Calculation did not complete in expected time. Last known status: {status}"


# def test_calculation_fails_if_duplicate_id_in_progress():
#     with open(test_data_path, "rb") as f:
#         files = {"file": ("testarea1.zip", f, "application/zip")}
#         data = {"zoning_col": "kaavamerki", "id": id}
#         response = client.post("/calculation", data=data, files=files)
#         assert response.status_code == 400


# @pytest.mark.asyncio
# async def test_geojson_bio_values_correct(gdf_response_areas_data, gdf_test_data):
#     bio_carbon_values = gdf_response_areas_data["bio_carbon_per_area"].values

#     hiili_mets_values = gdf_test_data["hiili_mets"].values

#     print(gdf_response_areas_data["bio_carbon_per_area"])
#     print(gdf_test_data["hiili_mets"])
#     # print(gdf_response_geojson_data["bio_carbon_sum"])

#     # Using numpy's allclose to check if the values are approximately equal
#     assert np.allclose(
#         bio_carbon_values, hiili_mets_values, rtol=10, atol=0
#     ), "The values are not approximately equal"
