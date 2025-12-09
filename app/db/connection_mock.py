"""
Test helpers for routing the state DB to the dedicated test instance and
stubbing the task queue during API tests.
"""

import asyncio
import os
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any, Dict, Tuple

import pytest
from alembic import command
from alembic.config import Config
from _pytest.monkeypatch import MonkeyPatch
from fastapi.encoders import jsonable_encoder
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app import config as app_config
from app.db import connection

TEST_STATE_USER = os.getenv("STATE_PG_TEST_USER", "state_test_user")
TEST_STATE_PASSWORD = os.getenv("STATE_PG_TEST_PASSWORD", "state_test_password")
TEST_STATE_HOST = os.getenv("STATE_PG_TEST_HOST", "state-db-test")
TEST_STATE_PORT = os.getenv("STATE_PG_TEST_PORT", "5432")
TEST_STATE_DB = os.getenv("STATE_PG_TEST_DB", "state_test_db")

TEST_STATE_DATABASE_URL = (
    f"postgresql+asyncpg://{TEST_STATE_USER}:{TEST_STATE_PASSWORD}@"
    f"{TEST_STATE_HOST}:{TEST_STATE_PORT}/{TEST_STATE_DB}"
)


class InlineQueue:
    async def enqueue(self, *args: Any, **kwargs: Any) -> Dict[str, Any]:
        function_name = args[0] if args else kwargs.get("function")
        return {"function": function_name, "kwargs": kwargs}


def _configure_state_env() -> None:
    os.environ["STATE_PG_USER"] = TEST_STATE_USER
    os.environ["STATE_PG_PASSWORD"] = TEST_STATE_PASSWORD
    os.environ["STATE_PG_HOST"] = TEST_STATE_HOST
    os.environ["STATE_PG_PORT"] = str(TEST_STATE_PORT)
    os.environ["STATE_PG_DB"] = TEST_STATE_DB


def _build_test_state_engine() -> Tuple[AsyncEngine, async_sessionmaker[AsyncSession]]:
    pool_kwargs = {
        "pool_size": int(os.getenv("DB_POOL_SIZE", "10")),
        "max_overflow": int(os.getenv("DB_MAX_OVERFLOW", "0")),
        "pool_timeout": int(os.getenv("DB_POOL_TIMEOUT", "30")),
        "pool_recycle": int(os.getenv("DB_POOL_RECYCLE", "1800")),
        "pool_pre_ping": True,
    }

    test_engine = create_async_engine(
        TEST_STATE_DATABASE_URL,
        future=True,
        echo=False,
        json_serializer=jsonable_encoder,
        **pool_kwargs,
    )
    session_factory = async_sessionmaker(
        test_engine,
        autoflush=False,
        expire_on_commit=False,
    )
    return test_engine, session_factory


@pytest.fixture(scope="session")
def monkeypatch_get_async_context_db():
    """
    Patch the state DB helpers to point at the dedicated test database.
    """

    _configure_state_env()
    app_config.get_settings.cache_clear()
    connection.global_settings = app_config.get_settings()
    connection.gis_url = connection.global_settings.gis_pg_url
    test_engine, session_factory = _build_test_state_engine()

    @asynccontextmanager
    async def mock_get_async_context_state_db() -> AsyncGenerator:
        async with connection.base_async_db_context(
            session_factory, f"ASYNC Pool: {test_engine.pool.status()}"
        ) as session:
            yield session

    async def mock_get_async_state_db() -> AsyncGenerator:
        async with mock_get_async_context_state_db() as session:
            yield session

    connection.state_engine = test_engine
    connection.StateAsyncSessionLocal = session_factory
    connection.state_url = connection.global_settings.state_pg_url

    from app import saq_worker
    from app import main as main_app

    m = MonkeyPatch()
    m.setattr(
        connection,
        "get_async_context_state_db",
        mock_get_async_context_state_db,
    )
    m.setattr(connection, "get_async_state_db", mock_get_async_state_db)
    m.setattr(
        saq_worker, "get_async_context_state_db", mock_get_async_context_state_db
    )
    m.setattr(main_app, "get_async_state_db", mock_get_async_state_db)
    main_app.app.dependency_overrides[main_app.get_async_state_db] = (
        mock_get_async_state_db
    )

    return test_engine


async def async_setup():
    async with connection.get_async_context_state_db() as session:
        engine = session.bind
        url = str(engine.url)
        assert TEST_STATE_DB in url
        assert TEST_STATE_USER in url
        assert TEST_STATE_HOST in url
        assert str(TEST_STATE_PORT) in url

        await session.execute(text('CREATE EXTENSION IF NOT EXISTS "pgcrypto";'))
        await session.commit()

        alembic_cfg = Config("alembic.ini")
        alembic_cfg.set_main_option("is_testing", "True")

    command.downgrade(alembic_cfg, "base")
    command.upgrade(alembic_cfg, "head")


async def async_teardown():
    alembic_cfg = Config("alembic.ini")
    alembic_cfg.set_main_option("is_testing", "True")
    command.downgrade(alembic_cfg, "base")


@pytest.fixture(scope="session", autouse=True)
def setup_and_teardown(request):
    request.getfixturevalue("monkeypatch_get_async_context_db")
    asyncio.run(async_setup())
    yield
    asyncio.run(async_teardown())


def install_inline_queue() -> InlineQueue:
    """
    Replace the Redis-backed saq queue with a lightweight inline stub so
    tests can run without Redis.
    """

    inline_queue = InlineQueue()

    from app import main as main_app
    from app import saq_worker

    saq_worker.queue = inline_queue
    saq_worker.settings["queue"] = inline_queue
    main_app.queue = inline_queue

    return inline_queue
