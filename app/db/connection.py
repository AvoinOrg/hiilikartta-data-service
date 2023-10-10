from collections.abc import AsyncGenerator
from http.client import HTTPException

from fastapi.encoders import jsonable_encoder
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.engine import URL
from contextlib import asynccontextmanager
from sqlalchemy.pool import QueuePool

from app import config
from app.utils.logger import get_logger

logger = get_logger(__name__)

global_settings = config.get_settings()
gis_url = global_settings.gis_pg_url
state_url = global_settings.state_pg_url

gis_engine = create_async_engine(
    gis_url,
    future=True,
    echo=True,
    poolclass=QueuePool,
    json_serializer=jsonable_encoder,
)

state_engine = create_async_engine(
    state_url,
    future=True,
    echo=True,
    poolclass=QueuePool,
    json_serializer=jsonable_encoder,
)

# expire_on_commit=False will prevent attributes from being expired
# after commit.
GisAsyncSessionLocal = async_sessionmaker(
    gis_engine, autoflush=False, expire_on_commit=False
)

StateAsyncSessionLocal = async_sessionmaker(
    state_engine, autoflush=False, expire_on_commit=False
)


async def get_async_gis_db() -> AsyncGenerator:
    try:
        session: AsyncSession = GisAsyncSessionLocal()
        logger.debug(f"ASYNC Pool: {gis_engine.pool.status()}")
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


async def get_async_state_db() -> AsyncGenerator:
    try:
        session: AsyncSession = StateAsyncSessionLocal()
        logger.debug(f"ASYNC Pool: {state_engine.pool.status()}")
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
