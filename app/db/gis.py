import asyncio
from contextlib import asynccontextmanager
from typing import AsyncIterator, List, Optional

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import text

from app.db.connection import (
    get_async_context_gis_db_with_retry,
)
from app.db.errors import GisRetryLaterError
from app.db.gis_semaphore import gis_operation_slot
from app.db.redis_semaphore import distributed_gis_slot
from app.utils.logger import get_logger

logger = get_logger(__name__)


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


async def fetch_segment_areas_ha_for_regions(
    wkts: List[str],
    crs: str,
    db_session: Optional[AsyncSession] = None,
    simplify_calcs: bool = False,
):
    """
    Return per-region segment-id areas (in hectares) from luke_mvmisegmentit_id_kokomaa.

    - simplify_calcs=False: uses exact pixel/polygon intersection areas (fractional pixels).
    - simplify_calcs=True: counts whole intersecting pixels only (no fractional weighting).
    """
    crs_int = int(crs)

    try:
        wkt_list_str = ",".join([f"('{wkt}')" for wkt in wkts])

        if simplify_calcs:
            statement = text(
                f"""
                WITH input_geoms AS (
                    SELECT
                        idx AS order_num,
                        ST_SetSRID(ST_GeomFromText(wkt), :crs) AS geom
                    FROM unnest(array[{wkt_list_str}]) WITH ORDINALITY AS indexed_wkt(wkt, idx)
                ),
                pixel_counts AS (
                    SELECT
                        g.order_num,
                        (vc).value::int AS segment_id,
                        SUM((vc).count)::double precision AS pixel_count
                    FROM luke_mvmisegmentit_id_kokomaa r
                    JOIN input_geoms g
                        ON ST_Intersects(r.rast, g.geom)
                    CROSS JOIN LATERAL ST_ValueCount(
                        ST_Clip(r.rast, g.geom, touched => FALSE),
                        1,
                        TRUE
                    ) AS vc
                    GROUP BY g.order_num, segment_id
                )
                SELECT
                    order_num,
                    segment_id,
                    (pixel_count * :grid_to_ha)::double precision AS area_ha
                FROM pixel_counts
                ORDER BY order_num, segment_id;
                """
            )
            params = {"crs": crs_int, "grid_to_ha": (16 * 16) / 10_000}
        else:
            statement = text(
                f"""
                WITH input_geoms AS (
                    SELECT
                        idx AS order_num,
                        ST_SetSRID(ST_GeomFromText(wkt), :crs) AS geom
                    FROM unnest(array[{wkt_list_str}]) WITH ORDINALITY AS indexed_wkt(wkt, idx)
                )
                SELECT
                    g.order_num,
                    (p).val::int AS segment_id,
                    SUM(
                        ST_Area(ST_Intersection((p).geom, g.geom)) / 10000.0
                    )::double precision AS area_ha
                FROM luke_mvmisegmentit_id_kokomaa r
                JOIN input_geoms g
                    ON ST_Intersects(r.rast, g.geom)
                CROSS JOIN LATERAL ST_PixelAsPolygons(
                    ST_Clip(r.rast, g.geom, touched => TRUE),
                    1,
                    TRUE
                ) AS p
                GROUP BY g.order_num, segment_id
                ORDER BY g.order_num, segment_id;
                """
            )
            params = {"crs": crs_int}

        async with _get_throttled_session(db_session) as session:
            result = await session.execute(statement, params)
            rows = result.fetchall()

        return rows

    except SQLAlchemyError as ex:
        logger.exception(ex)
        raise


async def fetch_weighted_raster_sum_ha_by_segment_for_regions(
    raster_table: str,
    wkts: List[str],
    crs: str,
    db_session: Optional[AsyncSession] = None,
    simplify_calcs: bool = False,
):
    """
    Return per-region, per-segment Σ(value * area_ha) for a per-hectare raster.

    Segment ids come from luke_mvmisegmentit_id_kokomaa. Raster values are sampled at
    each segment pixel and aggregated by segment id.
    """
    crs_int = int(crs)

    try:
        wkt_list_str = ",".join([f"('{wkt}')" for wkt in wkts])

        if simplify_calcs:
            statement = text(
                f"""
                WITH input_geoms AS (
                    SELECT
                        idx AS order_num,
                        ST_SetSRID(ST_GeomFromText(wkt), :crs) AS geom
                    FROM unnest(array[{wkt_list_str}]) WITH ORDINALITY AS indexed_wkt(wkt, idx)
                ),
                segment_pixels AS (
                    SELECT
                        g.order_num,
                        (p).val::int AS segment_id,
                        ST_PointOnSurface((p).geom) AS sample_point
                    FROM luke_mvmisegmentit_id_kokomaa r
                    JOIN input_geoms g
                        ON ST_Intersects(r.rast, g.geom)
                    CROSS JOIN LATERAL ST_PixelAsPolygons(
                        ST_Clip(r.rast, g.geom, touched => FALSE),
                        1,
                        TRUE
                    ) AS p
                ),
                sampled AS (
                    SELECT
                        sp.order_num,
                        sp.segment_id,
                        :grid_to_ha::double precision AS area_ha,
                        COALESCE(rv.raster_value, 0.0)::double precision AS raster_value
                    FROM segment_pixels sp
                    LEFT JOIN LATERAL (
                        SELECT ST_Value(r.rast, 1, sp.sample_point)::double precision AS raster_value
                        FROM {raster_table} r
                        WHERE ST_Intersects(r.rast, sp.sample_point)
                        LIMIT 1
                    ) rv ON TRUE
                )
                SELECT
                    order_num,
                    segment_id,
                    SUM(raster_value * area_ha)::double precision AS sum_weighted_ha
                FROM sampled
                GROUP BY order_num, segment_id
                ORDER BY order_num, segment_id;
                """
            )
            params = {"crs": crs_int, "grid_to_ha": (16 * 16) / 10_000}
        else:
            statement = text(
                f"""
                WITH input_geoms AS (
                    SELECT
                        idx AS order_num,
                        ST_SetSRID(ST_GeomFromText(wkt), :crs) AS geom
                    FROM unnest(array[{wkt_list_str}]) WITH ORDINALITY AS indexed_wkt(wkt, idx)
                ),
                segment_pixels AS (
                    SELECT
                        g.order_num,
                        (p).val::int AS segment_id,
                        ST_Intersection((p).geom, g.geom) AS intersect_geom,
                        (
                            ST_Area(ST_Intersection((p).geom, g.geom)) / 10000.0
                        )::double precision AS area_ha
                    FROM luke_mvmisegmentit_id_kokomaa r
                    JOIN input_geoms g
                        ON ST_Intersects(r.rast, g.geom)
                    CROSS JOIN LATERAL ST_PixelAsPolygons(
                        ST_Clip(r.rast, g.geom, touched => TRUE),
                        1,
                        TRUE
                    ) AS p
                ),
                sampled AS (
                    SELECT
                        sp.order_num,
                        sp.segment_id,
                        sp.area_ha,
                        COALESCE(rv.raster_value, 0.0)::double precision AS raster_value
                    FROM segment_pixels sp
                    LEFT JOIN LATERAL (
                        SELECT ST_Value(
                            r.rast,
                            1,
                            ST_PointOnSurface(sp.intersect_geom)
                        )::double precision AS raster_value
                        FROM {raster_table} r
                        WHERE ST_Intersects(r.rast, ST_PointOnSurface(sp.intersect_geom))
                        LIMIT 1
                    ) rv ON TRUE
                    WHERE NOT ST_IsEmpty(sp.intersect_geom)
                      AND sp.area_ha > 0
                )
                SELECT
                    order_num,
                    segment_id,
                    SUM(raster_value * area_ha)::double precision AS sum_weighted_ha
                FROM sampled
                GROUP BY order_num, segment_id
                ORDER BY order_num, segment_id;
                """
            )
            params = {"crs": crs_int}

        async with _get_throttled_session(db_session) as session:
            result = await session.execute(statement, params)
            rows = result.fetchall()

        return rows

    except SQLAlchemyError as ex:
        logger.exception(ex)
        raise


async def fetch_weighted_raster_sum_ha_for_regions(
    raster_table: str,
    wkts: List[str],
    crs: str,
    db_session: Optional[AsyncSession] = None,
    simplify_calcs: bool = False,
):
    """
    Return per-region Σ(value * intersect_area_ha) for a per-hectare raster.

    This matches the Python-side approach of applying a per-pixel overlap fraction,
    then multiplying by cell area (in hectares).

    - simplify_calcs=False: exact pixel/polygon intersection areas (fractional pixels).
    - simplify_calcs=True: whole-cell inclusion only (no fractional weighting).
    """
    crs_int = int(crs)

    try:
        wkt_list_str = ",".join([f"('{wkt}')" for wkt in wkts])

        if simplify_calcs:
            statement = text(
                f"""
                WITH input_geoms AS (
                    SELECT
                        idx AS order_num,
                        ST_SetSRID(ST_GeomFromText(wkt), :crs) AS geom
                    FROM unnest(array[{wkt_list_str}]) WITH ORDINALITY AS indexed_wkt(wkt, idx)
                ),
                stats AS (
                    SELECT
                        g.order_num,
                        (ST_SummaryStatsAgg(
                            ST_Clip(r.rast, g.geom, touched => FALSE),
                            1,
                            TRUE
                        )).sum::double precision AS sum_per_ha
                    FROM {raster_table} r
                    JOIN input_geoms g
                        ON ST_Intersects(r.rast, g.geom)
                    GROUP BY g.order_num
                )
                SELECT
                    order_num,
                    (sum_per_ha * :grid_to_ha)::double precision AS sum_weighted_ha
                FROM stats
                ORDER BY order_num;
                """
            )
            params = {"crs": crs_int, "grid_to_ha": (16 * 16) / 10_000}
        else:
            statement = text(
                f"""
                WITH input_geoms AS (
                    SELECT
                        idx AS order_num,
                        ST_SetSRID(ST_GeomFromText(wkt), :crs) AS geom
                    FROM unnest(array[{wkt_list_str}]) WITH ORDINALITY AS indexed_wkt(wkt, idx)
                )
                SELECT
                    g.order_num,
                    SUM(
                        (p).val::double precision
                        * (ST_Area(ST_Intersection((p).geom, g.geom)) / 10000.0)
                    )::double precision AS sum_weighted_ha
                FROM {raster_table} r
                JOIN input_geoms g
                    ON ST_Intersects(r.rast, g.geom)
                CROSS JOIN LATERAL ST_PixelAsPolygons(
                    ST_Clip(r.rast, g.geom, touched => TRUE),
                    1,
                    TRUE
                ) AS p
                GROUP BY g.order_num
                ORDER BY g.order_num;
                """
            )
            params = {"crs": crs_int}

        async with _get_throttled_session(db_session) as session:
            result = await session.execute(statement, params)
            rows = result.fetchall()

        return rows

    except SQLAlchemyError as ex:
        logger.exception(ex)
        raise


async def fetch_natcode_for_regions(
    wkts: List[str],
    crs: str,
    db_session: Optional[AsyncSession] = None,
):
    """
    Resolve the best-matching region code for each input geometry.

    Uses the `maakunta` table:
    - geometry column: `geom`
    - code column: `natcode` (string like "01", "11")

    Strategy: pick the `maakunta` polygon with the largest intersection area
    with the input geometry.
    """
    crs_int = int(crs)

    try:
        wkt_list_str = ",".join([f"('{wkt}')" for wkt in wkts])

        statement = text(
            f"""
            WITH input_geoms AS (
                SELECT
                    idx AS order_num,
                    ST_SetSRID(ST_GeomFromText(wkt), :crs) AS geom
                FROM unnest(array[{wkt_list_str}]) WITH ORDINALITY AS indexed_wkt(wkt, idx)
            ),
            matches AS (
                SELECT
                    g.order_num,
                    m.natcode::text AS natcode,
                    ST_Area(ST_Intersection(m.geom, g.geom)) AS intersect_area
                FROM maakunta m
                JOIN input_geoms g
                    ON ST_Intersects(m.geom, g.geom)
            ),
            ranked AS (
                SELECT
                    order_num,
                    natcode,
                    intersect_area,
                    ROW_NUMBER() OVER (
                        PARTITION BY order_num
                        ORDER BY intersect_area DESC
                    ) AS rn
                FROM matches
            )
            SELECT order_num, natcode
            FROM ranked
            WHERE rn = 1
            ORDER BY order_num;
            """
        )

        async with _get_throttled_session(db_session) as session:
            result = await session.execute(statement, {"crs": crs_int})
            rows = result.fetchall()

        return rows

    except SQLAlchemyError as ex:
        logger.exception(ex)
        raise
