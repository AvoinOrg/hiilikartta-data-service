import asyncio
from collections.abc import AsyncGenerator
from http.client import HTTPException
from typing import Callable
from contextlib import asynccontextmanager

from fastapi.encoders import jsonable_encoder
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app import config
from app.db.errors import (
    GisOperationTimedOutError,
    GisRetryLaterError,
    is_db_capacity_error,
    is_statement_timeout,
)
from app.utils.logger import get_logger

logger = get_logger(__name__)


class PoolExhaustedError(Exception):
    """Raised when the connection pool is exhausted and retries are exceeded."""
    pass

global_settings = config.get_settings()
gis_url = global_settings.gis_pg_url
state_url = global_settings.state_pg_url
debug = global_settings.is_debug

# Keep pools bounded so connections queue instead of exhausting Postgres.
pool_kwargs = {
    "pool_size": global_settings.db_pool_size,
    "max_overflow": global_settings.db_max_overflow,
    "pool_timeout": global_settings.db_pool_timeout,
    "pool_recycle": global_settings.db_pool_recycle,
    "pool_pre_ping": True,
}

gis_engine = create_async_engine(
    gis_url, future=True, echo=False, json_serializer=jsonable_encoder, **pool_kwargs
)

state_engine = create_async_engine(
    state_url, future=True, echo=False, json_serializer=jsonable_encoder, **pool_kwargs
)

# expire_on_commit=False will prevent attributes from being expired
# after commit.
GisAsyncSessionLocal = async_sessionmaker(
    gis_engine, autoflush=False, expire_on_commit=False
)

StateAsyncSessionLocal = async_sessionmaker(
    state_engine, autoflush=False, expire_on_commit=False
)


@asynccontextmanager
async def base_async_db_context(
    session_generator: Callable[[], AsyncSession], logger_msg: str
) -> AsyncGenerator:
    try:
        session: AsyncSession = session_generator()
        logger.debug(logger_msg)
        yield session
    except SQLAlchemyError as sql_ex:
        await session.rollback()
        raise sql_ex
    except HTTPException as http_ex:
        await session.rollback()
        raise http_ex
    else:
        await session.commit()
    finally:
        await session.close()


@asynccontextmanager
async def base_async_db_context_with_retry(
    session_generator: Callable[[], AsyncSession],
    logger_msg: str,
    max_retries: int = 5,
    base_delay: float = 1.0,
    max_delay: float = 60.0,
) -> AsyncGenerator:
    """
    Context manager with retry logic for pool exhaustion and connection errors.
    Uses exponential backoff between retries.
    
    Args:
        session_generator: Callable that creates a new session
        logger_msg: Message to log when acquiring session
        max_retries: Maximum number of retry attempts (default: 5)
        base_delay: Initial delay between retries in seconds (default: 1.0)
        max_delay: Maximum delay between retries in seconds (default: 60.0)
    
    Raises:
        PoolExhaustedError: If all retries are exhausted
        SQLAlchemyError: If error is not retryable
    """
    last_exception = None
    
    for attempt in range(max_retries):
        session = None
        try:
            session = session_generator()
            logger.debug(f"{logger_msg} (attempt {attempt + 1})")
            yield session
            await session.commit()
            return  # Success, exit the retry loop
            
        except SQLAlchemyError as e:
            last_exception = e
            if session is not None:
                try:
                    await session.rollback()
                except Exception:
                    pass  # Ignore rollback errors

            if is_statement_timeout(e):
                raise GisOperationTimedOutError(str(e)) from e

            if is_db_capacity_error(e):
                raise GisRetryLaterError(
                    "GIS database is at capacity; retry later",
                    retry_in_seconds=max_delay,
                ) from e

            raise
            
        except HTTPException as http_ex:
            if session is not None:
                try:
                    await session.rollback()
                except Exception:
                    pass
            raise http_ex
            
        finally:
            if session is not None:
                try:
                    await session.close()
                except Exception:
                    pass
    
    raise PoolExhaustedError(
        f"Failed to acquire database connection after {max_retries} attempts"
    ) from last_exception


async def get_async_state_db() -> AsyncGenerator:
    async with base_async_db_context(
        StateAsyncSessionLocal, f"ASYNC Pool: {state_engine.pool.status()}"
    ) as session:
        yield session


@asynccontextmanager
async def get_async_context_state_db() -> AsyncGenerator:
    async with base_async_db_context(
        StateAsyncSessionLocal, f"ASYNC Pool: {state_engine.pool.status()}"
    ) as session:
        yield session


async def get_async_gis_db() -> AsyncGenerator:
    async with base_async_db_context(
        GisAsyncSessionLocal, f"ASYNC Pool: {gis_engine.pool.status()}"
    ) as session:
        yield session


@asynccontextmanager
async def get_async_context_gis_db() -> AsyncGenerator:
    async with base_async_db_context(
        GisAsyncSessionLocal, f"ASYNC Pool: {gis_engine.pool.status()}"
    ) as session:
        yield session


@asynccontextmanager
async def get_async_context_gis_db_with_retry(
    max_retries: int = 5,
    base_delay: float = 1.0,
) -> AsyncGenerator:
    """
    GIS DB context with automatic retry on pool exhaustion.
    
    Use this for long-running GIS operations that should retry on
    transient connection failures.
    """
    async with base_async_db_context_with_retry(
        GisAsyncSessionLocal,
        f"ASYNC Pool: {gis_engine.pool.status()}",
        max_retries=max_retries,
        base_delay=base_delay,
    ) as session:
        timeout_seconds = max(0, int(global_settings.gis_statement_timeout_seconds))
        if timeout_seconds:
            timeout_ms = timeout_seconds * 1000
            await session.execute(text(f"SET LOCAL statement_timeout = {timeout_ms}"))
        yield session
