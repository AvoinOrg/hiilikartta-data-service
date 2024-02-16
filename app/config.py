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
    is_debug (bool):
    celery_broker_url (str):
    celery_result_backend (str):
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

    is_debug = env_vars.get("DEBUG", "false").lower() in ["true", "1", "t", "y", "yes"]

    redis_url: str = env_vars["REDIS_URL"]

    gis_pg_url: URL = URL.create(
        "postgresql+asyncpg",
        username=env_vars["GIS_PG_USER"],
        password=env_vars["GIS_PG_PASSWORD"],
        host="pgbouncer-gis",  # Use the PgBouncer service name for GIS DB
        port=5432,  # Default PgBouncer port
        database=env_vars["GIS_PG_DB"],
    )

    state_pg_url: URL = URL.create(
        "postgresql+asyncpg",
        username=env_vars["STATE_PG_USER"],
        password=env_vars["STATE_PG_PASSWORD"],
        host="pgbouncer-state",  # Use the PgBouncer service name for STATE DB
        port=5432,  # Default PgBouncer port
        database=env_vars["STATE_PG_DB"],
    )


@lru_cache
def get_settings():
    logger.info("Loading config settings from the environment...")
    return Settings()
