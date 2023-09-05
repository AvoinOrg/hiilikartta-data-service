from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
from app.main import app
import geopandas as gpd
import numpy as np

client = TestClient(app)

test_data_path = "tests/data/testarea1.zip"


@pytest.fixture(scope="module")
def fetched_data():
    with open(test_data_path, "rb") as f:
        files = {"file": ("testarea1.zip", f, "application/zip")}
        data = {"zoning_col": "kaavamerki"}
        response = client.post("/calculate", data=data, files=files)
        assert response.status_code == 200
        return response


def test_response_code(fetched_data):
    assert fetched_data.status_code == 200


def test_geojson_is_returned(fetched_data):
    assert fetched_data.json()["geojson"] != None


@pytest.fixture(scope="module")
def gdf_response_geojson_data(fetched_data):
    gdf = gpd.read_file(fetched_data.json()["geojson"])
    gdf.sort_values(by=["OBJECTID"], inplace=True)
    return gdf


def test_geojson_contains_data(gdf_response_geojson_data):
    assert len(gdf_response_geojson_data) > 0


@pytest.fixture(scope="module")
def gdf_test_data():
    test_gdf = gpd.read_file(test_data_path)
    return test_gdf


# def test_geojson_bio_values_correct(gdf_response_geojson_data, gdf_test_data):
#     bio_carbon_values = gdf_response_geojson_data["bio_carbon_per_area"].values

#     hiili_mets_values = gdf_test_data["hiili_mets"].values

#     print(gdf_response_geojson_data["bio_carbon_per_area"])
#     print(gdf_test_data["hiili_mets"])
#     # print(gdf_response_geojson_data["bio_carbon_sum"])

#     # Using numpy's allclose to check if the values are approximately equal
#     assert np.allclose(
#         bio_carbon_values, hiili_mets_values, rtol=10, atol=0
#     ), "The values are not approximately equal"
