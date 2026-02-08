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
    redis_port: str = env_vars.get("REDIS_PORT", "6379")
    redis_url: str = f"redis://{redis_host}:{redis_port}/0"

    gis_pg_url: URL = URL.create(
        "postgresql+asyncpg",
        username=env_vars["GIS_PG_USER"],
        password=env_vars["GIS_PG_PASSWORD"],
        host=env_vars.get("GIS_PG_HOST"),
        port=int(env_vars.get("GIS_PG_PORT", 5432)),
        database=env_vars["GIS_PG_DB"],
    )

    state_pg_url: URL = URL.create(
        "postgresql+asyncpg",
        username=env_vars["STATE_PG_USER"],
        password=env_vars["STATE_PG_PASSWORD"],
        host=env_vars.get("STATE_PG_HOST"),
        port=int(env_vars.get("STATE_PG_PORT", 5432)),
        database=env_vars["STATE_PG_DB"],
    )

    zitadel_domain: str = os.getenv("ZITADEL_DOMAIN") or ""
    zitadel_client_id: str = os.getenv("ZITADEL_CLIENT_ID") or ""
    zitadel_client_secret: str = os.getenv("ZITADEL_CLIENT_SECRET") or ""

    gis_local_max_concurrent: int = int(env_vars.get("GIS_LOCAL_MAX_CONCURRENT", "3"))

    # Database pool settings
    db_pool_size: int = int(env_vars.get("DB_POOL_SIZE", "10"))
    db_max_overflow: int = int(env_vars.get("DB_MAX_OVERFLOW", "0"))
    db_pool_timeout: int = int(env_vars.get("DB_POOL_TIMEOUT", "30"))
    db_pool_recycle: int = int(env_vars.get("DB_POOL_RECYCLE", "1800"))

    # Distributed GIS semaphore settings
    gis_distributed_max_concurrent: int = int(env_vars.get("GIS_DISTRIBUTED_MAX_CONCURRENT", "5"))
    gis_slot_ttl: int = int(env_vars.get("GIS_SLOT_TTL", "7200"))  # 2 hours default
    gis_operation_timeout: float = float(env_vars.get("GIS_OPERATION_TIMEOUT", "3600"))  # 1 hour default
    gis_statement_timeout_seconds: int = int(
        env_vars.get(
            "GIS_STATEMENT_TIMEOUT_SECONDS",
            env_vars.get("GIS_SLOT_TTL", "7200"),
        )
    )

    # Test database settings (optional, for testing)
    state_pg_test_user: str = env_vars.get("STATE_PG_TEST_USER", "state_test_user")
    state_pg_test_password: str = env_vars.get("STATE_PG_TEST_PASSWORD", "state_test_password")
    state_pg_test_host: str = env_vars.get("STATE_PG_TEST_HOST", "state-db-test")
    state_pg_test_port: str = env_vars.get("STATE_PG_TEST_PORT", "5432")
    state_pg_test_db: str = env_vars.get("STATE_PG_TEST_DB", "state_test_db")

    # Umami Analytics settings (production only)
    umami_enabled: bool = env_vars.get("UMAMI_ENABLED", "false").lower() in ["true", "1", "t", "y", "yes"]
    umami_host_url: str = env_vars.get("UMAMI_HOST_URL", "")
    umami_website_id: str = env_vars.get("UMAMI_WEBSITE_ID", "")


@lru_cache
def get_settings():
    logger.info("Loading config settings from the environment...")
    return Settings()
