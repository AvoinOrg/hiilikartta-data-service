import asyncio
from contextlib import asynccontextmanager
from typing import AsyncIterator, List, Optional

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import text

from app.db.connection import (
    get_async_context_gis_db,
    get_async_context_gis_db_with_retry,
)
from app.db.errors import GisRetryLaterError
from app.db.gis_semaphore import gis_operation_slot
from app.db.redis_semaphore import distributed_gis_slot
from app.utils.logger import get_logger

logger = get_logger(__name__)


@asynccontextmanager
async def _get_session(
    provided_session: Optional[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    if provided_session is not None:
        yield provided_session
    else:
        async with get_async_context_gis_db() as session:
            yield session


@asynccontextmanager
async def _get_throttled_session(
    provided_session: Optional[AsyncSession],
    use_distributed_lock: bool = True,
) -> AsyncIterator[AsyncSession]:
    """
    Get a GIS session with throttling to prevent pool exhaustion.

    This applies two layers of throttling:
    1. Local semaphore: Limits concurrent GIS ops within this process
    2. Distributed semaphore: Limits concurrent GIS ops across all processes (via Redis)

    Args:
        provided_session: Optional pre-existing session. If provided, assumes
                          caller handles throttling.
        use_distributed_lock: Whether to use distributed (Redis) locking
                              in addition to local semaphore.
    """
    if provided_session is not None:
        # If session is provided, assume caller handles throttling
        yield provided_session
        return

    # Apply local semaphore (per-process limiting)
    try:
        async with gis_operation_slot(timeout=0):
            # Optionally apply distributed semaphore (cross-process limiting)
            if use_distributed_lock:
                async with distributed_gis_slot():
                    async with get_async_context_gis_db_with_retry() as session:
                        yield session
            else:
                async with get_async_context_gis_db_with_retry() as session:
                    yield session
    except asyncio.TimeoutError as exc:
        raise GisRetryLaterError(
            "No local GIS semaphore slots available",
            retry_in_seconds=60.0,
        ) from exc


async def fetch_variables_for_ids(
    ids: List[str], db_session: Optional[AsyncSession] = None
):
    try:
        ids_int = tuple([int(item) for item in ids])
        statement = text(
            """
            SELECT *
            FROM luke_mvmisegmentit_muuttujat_kokomaa
            WHERE kuvio = ANY(:ids);
            """
        )

        async with _get_throttled_session(db_session) as session:
            result = await session.execute(statement, {"ids": ids_int})
            col_names = list(result.keys())
            rows = result.fetchall()

        return rows, col_names

    except SQLAlchemyError as ex:
        logger.exception(ex)
        raise


def _build_raster_union_clip_statement(table_name: str, wkt_list_str: str):
    return text(
        f"""
        WITH input_geoms AS (
            SELECT
                idx as order_num,
                ST_SetSRID(
                    ST_GeomFromText(wkt),
                    :crs
                ) as geom
            FROM unnest(array[{wkt_list_str}]) WITH ORDINALITY as indexed_wkt(wkt, idx)
        ),
        unioned AS (
            SELECT
                g.order_num,
                ST_Union(r.rast) as union_rast
            FROM {table_name} r
            JOIN input_geoms g
                ON ST_Intersects(r.rast, g.geom)
            GROUP BY g.order_num
        )
        SELECT
            ST_AsTIFF(ST_Clip(u.union_rast, g.geom, touched => :touched), 'DEFLATE9') as tiff,
            g.order_num
        FROM unioned u
        JOIN input_geoms g USING (order_num)
        ORDER BY g.order_num;
        """
    )


async def fetch_rasters_for_regions(
    wkts: List[str],
    crs: str,
    db_session: Optional[AsyncSession] = None,
    simplify_calcs: bool = False,
):
    crs_int = int(crs)

    try:
        # Prepare the WKT geometries as a string to use in the SQL query
        wkt_list_str = ",".join([f"('{wkt}')" for wkt in wkts])

        statement = _build_raster_union_clip_statement(
            "luke_mvmisegmentit_id_kokomaa",
            wkt_list_str,
        )

        async with _get_throttled_session(db_session) as session:
            result = await session.execute(
                statement,
                {
                    "crs": crs_int,
                    "touched": not simplify_calcs,
                },
            )
            rows = result.fetchall()

        # Fetching all rows, each row containing a raster for a WKT geometry
        return rows
    except SQLAlchemyError as ex:
        logger.exception(ex)
        raise


async def fetch_bio_carbon_for_regions(
    wkts: List[str],
    crs: str,
    db_session: Optional[AsyncSession] = None,
    simplify_calcs: bool = False,
):
    crs_int = int(crs)

    try:
        wkt_list_str = ",".join([f"('{wkt}')" for wkt in wkts])

        statement = _build_raster_union_clip_statement(
            "hiilikartta_kasvillisuudenhiili_2021_tcha",
            wkt_list_str,
        )

        async with _get_throttled_session(db_session) as session:
            result = await session.execute(
                statement,
                {
                    "crs": crs_int,
                    "touched": not simplify_calcs,
                },
            )
            rows = result.fetchall()

        return rows

    except SQLAlchemyError as ex:
        logger.exception(ex)
        raise


async def fetch_ground_carbon_for_regions(
    wkts: List[str],
    crs: str,
    db_session: Optional[AsyncSession] = None,
    simplify_calcs: bool = False,
):
    crs_int = int(crs)

    try:
        # Prepare the WKT geometries as a string to use in the SQL query
        wkt_list_str = ",".join([f"('{wkt}')" for wkt in wkts])

        statement = _build_raster_union_clip_statement(
            "hiilikartta_maaperanhiili_2023_tcha",
            wkt_list_str,
        )

        async with _get_throttled_session(db_session) as session:
            result = await session.execute(
                statement,
                {
                    "crs": crs_int,
                    "touched": not simplify_calcs,
                },
            )
            rows = result.fetchall()

        return rows

    except SQLAlchemyError as ex:
        logger.exception(ex)
        raise
