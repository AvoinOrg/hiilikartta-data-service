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
    zitadel_client_id: (str):
    zitadel_client_secret: (str):
    zitadel_domain: (str):
    Returns:
    instance of Settings
    """

    is_debug = env_vars.get("DEBUG", "false").lower() in ["true", "1", "t", "y", "yes"]

    redis_host: str = env_vars["REDIS_HOST"]
    redis_port: str = env_vars["REDIS_PORT"]
    redis_url: str = f"redis://{redis_host}:{redis_port}/0"

    gis_pg_url: URL = URL.create(
        "postgresql+asyncpg",
        username=env_vars["GIS_PG_USER"],
        password=env_vars["GIS_PG_PASSWORD"],
        host=env_vars.get("GIS_PG_HOST", "pgbouncer-gis"),
        port=int(env_vars.get("GIS_PG_PORT", 5432)),
        database=env_vars["GIS_PG_DB"],
    )

    state_pg_url: URL = URL.create(
        "postgresql+asyncpg",
        username=env_vars["STATE_PG_USER"],
        password=env_vars["STATE_PG_PASSWORD"],
        host=env_vars.get("STATE_PG_HOST", "pgbouncer-state"),
        port=int(env_vars.get("STATE_PG_PORT", 5432)),
        database=env_vars["STATE_PG_DB"],
    )

    zitadel_domain: str = os.getenv("ZITADEL_DOMAIN") or ""
    zitadel_client_id: str = os.getenv("ZITADEL_CLIENT_ID") or ""
    zitadel_client_secret: str = os.getenv("ZITADEL_CLIENT_SECRET") or ""


@lru_cache
def get_settings():
    logger.info("Loading config settings from the environment...")
    return Settings()
