from collections.abc import AsyncGenerator
from http.client import HTTPException

from fastapi.encoders import jsonable_encoder
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.engine import URL

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
    json_serializer=jsonable_encoder,
)

state_engine = create_async_engine(
    state_url,
    future=True,
    echo=True,
    json_serializer=jsonable_encoder,
)

# expire_on_commit=False will prevent attributes from being expired
# after commit.
GisAsyncSessionFactory = sessionmaker(
    gis_engine, autoflush=False, expire_on_commit=False, class_=AsyncSession
)

StateAsyncSessionFactory = sessionmaker(
    state_engine, autoflush=False, expire_on_commit=False, class_=AsyncSession
)


async def get_gis_db() -> AsyncGenerator:
    async with GisAsyncSessionFactory() as session:
        logger.debug(f"ASYNC Pool: {gis_engine.pool.status()}")
        yield session


async def get_state_db() -> AsyncGenerator:
    async with StateAsyncSessionFactory() as session:
        logger.debug(f"ASYNC Pool 2: {state_engine.pool.status()}")
        yield session


async def get_async_gis_db() -> AsyncGenerator:
    try:
        session: AsyncSession = GisAsyncSessionFactory()
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
        session: AsyncSession = StateAsyncSessionFactory()
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
