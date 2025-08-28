import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
import pytest_asyncio
import asyncio
from app.db.gis import (
    fetch_variables_for_ids,
    fetch_rasters_for_regions,
    fetch_bio_carbon_for_regions,
    fetch_ground_carbon_for_regions,
)
from app.db.connection import get_async_context_gis_db

# Constants
TEST_WKT = "POLYGON ((323383.0893000001 6823223.647, 323394.52799999993 6823222.464000002, 323405.9667999996 6823221.2809000015, 323412.03610000014 6823279.966600001, 323475.19799999986 6823273.434300002, 323469.12849999964 6823214.748599999, 323430.8428999996 6823218.7082, 323428.0423999997 6823191.629799999, 323399.5087000001 6823186.453400001, 323399.9550000001 6823183.9936, 323356.17059999984 6823176.0506, 323283.85250000004 6823162.931200001, 323275.90950000007 6823206.715599999, 323314.7742999997 6823213.766199999, 323314.1496000001 6823217.209899999, 323322.0208999999 6823218.637800001, 323321.2742999997 6823222.753400002, 323318.0575000001 6823241.262600001, 323322.8071999997 6823287.1844, 323323.01300000027 6823289.173700001, 323389.1588000003 6823282.332699999, 323383.0893000001 6823223.647))"
TEST_CRS = "3067"


@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


# Test for fetch_variables_for_region
@pytest.mark.asyncio
async def test_fetch_variables_for_ids():
    async with get_async_context_gis_db() as session:
        rows, column_names = await fetch_variables_for_ids(session, ["100", "2"])
        assert rows is not None
        assert isinstance(column_names, list)


@pytest.mark.asyncio
async def test_fetch_rasters_for_regions():
    async with get_async_context_gis_db() as session:
        rows = await fetch_rasters_for_regions(session, [TEST_WKT, TEST_WKT], TEST_CRS)
        assert rows is not None


@pytest.mark.asyncio
async def test_fetch_bio_carbon_for_regions():
    async with get_async_context_gis_db() as session:
        result = await fetch_bio_carbon_for_regions(session, [TEST_WKT, TEST_WKT], TEST_CRS)
        assert result is not None
    # Add more assertions based on expected results


# # Test for fetch_ground_carbon_for_region
@pytest.mark.asyncio
async def test_fetch_ground_carbon_for_regions():
    async with get_async_context_gis_db() as session:
        result = await fetch_ground_carbon_for_regions(session, [TEST_WKT, TEST_WKT], TEST_CRS)
        assert result is not None


# Add more test cases as needed to cover different scenarios,
# including testing for exceptions and edge cases.
