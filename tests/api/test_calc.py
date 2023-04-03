from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
from app.main import app

client = TestClient(app)


def test_read_item():
    with open("tests/data/test.zip", "rb") as f:
        files = {"file": ("test.zip", f, "application/zip")}
        data = {"zoning_col": "area_k"}
        response = client.post("/calculate", data=data, files=files)
        assert response.status_code == 200

        json = response.json()
        assert json.get("sum") != None
        assert json.get("area") != None
