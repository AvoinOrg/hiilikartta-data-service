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
    pg_user (str):
    pg_pass (str):
    pg_database: (str):
    pg_port: (int):
    pg_url: (URL):
    Returns:
    instance of Settings
    """

    pg_user: str = env_vars["PG_USER"]
    pg_pass: str = env_vars["PG_PASSWORD"]
    pg_host: str = env_vars["PG_HOST"]
    pg_db: str = env_vars["PG_DB"]
    pg_port: int = env_vars["PG_PORT"]
    pg_url: URL = URL.create(
        "postgresql+asyncpg",
        username=pg_user,
        password=pg_pass,
        host=pg_host,
        database=pg_db,
        port=pg_port,
    )


@lru_cache
def get_settings():
    logger.info("Loading config settings from the environment...")
    return Settings()
