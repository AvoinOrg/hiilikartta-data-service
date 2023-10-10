import os
from functools import lru_cache

from pydantic import BaseSettings

from sqlalchemy.engine import URL
from app.utils.logger import get_logger
from dotenv import load_dotenv

load_dotenv()
env_vars = os.environ
logger = get_logger(__name__)


class Settings(BaseSettings):
    """
    BaseSettings, from Pydantic, validates the data so that when we create an instance of Settings,
     environment and testing will have types of str and bool, respectively.
    Parameters:
    gis_pg_user (str):
    gis_pg_pass (str):
    gis_pg_database: (str):
    gis_pg_port: (int):
    gis_pg_url: (URL):
    data_pg_user (str):
    data_pg_pass (str):
    data_pg_database: (str):
    data_pg_port: (int):
    data_pg_url: (URL):
    Returns:
    instance of Settings
    """

    gis_pg_user: str = env_vars["GIS_PG_USER"]
    gis_pg_pass: str = env_vars["GIS_PG_PASSWORD"]
    gis_pg_host: str = env_vars["GIS_PG_HOST"]
    gis_pg_db: str = env_vars["GIS_PG_DB"]
    gis_pg_port: int = env_vars["GIS_PG_PORT"]
    gis_pg_url: URL = URL.create(
        "postgresql+asyncpg",
        username=gis_pg_user,
        password=gis_pg_pass,
        host=gis_pg_host,
        database=gis_pg_db,
        port=gis_pg_port,
    )

    state_pg_user: str = env_vars["STATE_PG_USER"]
    state_pg_pass: str = env_vars["STATE_PG_PASSWORD"]
    state_pg_host: str = env_vars["STATE_PG_HOST"]
    state_pg_db: str = env_vars["STATE_PG_DB"]
    state_pg_port: int = env_vars["STATE_PG_PORT"]
    state_pg_url: URL = URL.create(
        "postgresql+asyncpg",
        username=state_pg_user,
        password=state_pg_pass,
        host=state_pg_host,
        database=state_pg_db,
        port=state_pg_port,
    )


@lru_cache
def get_settings():
    logger.info("Loading config settings from the environment...")
    return Settings()
