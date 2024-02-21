from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.sql import text
from sqlalchemy.ext.asyncio import AsyncSession
from typing import List

from app.utils.logger import get_logger

logger = get_logger(__name__)


async def fetch_variables_for_ids(db_session: AsyncSession, ids: List[str]):
    try:
        ids_int = tuple([int(item) for item in ids])
        statement = text(
            """
            SELECT *
            FROM luke_mvmisegmentit_muuttujat_kokomaa
            WHERE kuvio = ANY(:ids);
            """
        )

        result = await db_session.execute(statement, {"ids": ids_int})
        col_names = list(result.keys())

        return result.fetchall(), col_names

    except SQLAlchemyError as ex:
        logger.exception(ex)


async def fetch_rasters_for_regions(
    db_session: AsyncSession, wkts: List[str], crs: str
):
    crs_int = int(crs)

    try:
        # Prepare the WKT geometries as a string to use in the SQL query
        wkt_list_str = ",".join([f"('{wkt}')" for wkt in wkts])

        statement = text(
            f"""
            WITH rasters AS (
                SELECT 
                    wkt,
                    ST_Union(rast) as union_rast,
                    ST_SetSRID(
                        ST_GeomFromText(wkt), 
                        :crs
                    ) as geom,
                    idx as order_num
                FROM luke_mvmisegmentit_id_kokomaa,
                    unnest(array[{wkt_list_str}]) WITH ORDINALITY as indexed_wkt(wkt, idx)
                WHERE ST_Intersects(
                    rast, 
                    ST_SetSRID(
                        ST_GeomFromText(wkt), 
                        :crs
                    )
                )
                GROUP BY wkt, idx
            )
            SELECT 
                array_agg(ST_AsTIFF(ST_Clip(union_rast, geom), 'DEFLATE9')) as tiffs,
                order_num
            FROM rasters
            GROUP BY wkt, order_num;
            """
        )

        result = await db_session.execute(statement, {"crs": crs_int})

        # Fetching all rows, each row containing a raster for a WKT geometry
        return result.fetchall()
    except SQLAlchemyError as ex:
        logger.exception(ex)


async def fetch_bio_carbon_for_regions(
    db_session: AsyncSession, wkts: List[str], crs: str
):
    crs_int = int(crs)

    try:
        wkt_list_str = ",".join([f"('{wkt}')" for wkt in wkts])

        statement = text(
            f"""
            WITH rasters AS (
                SELECT 
                    wkt,
                    ST_Union(rast) as union_rast,
                    ST_SetSRID(
                        ST_GeomFromText(wkt), 
                        :crs
                    ) as geom,
                    idx as order_num
                FROM hiilikartta_kasvillisuudenhiili_2021_tcha,
                    unnest(array[{wkt_list_str}]) WITH ORDINALITY as indexed_wkt(wkt, idx)
                WHERE ST_Intersects(
                    rast,
                    ST_SetSRID(
                        ST_GeomFromText(wkt), 
                        :crs
                    )
                )
                GROUP BY wkt, idx
            )
            SELECT 
                array_agg(ST_AsTIFF(ST_Clip(union_rast, geom), 'DEFLATE9')) as tiffs,
                order_num
            FROM rasters
            GROUP BY wkt, order_num;
            """
        )

        result = await db_session.execute(statement, {"crs": crs_int})

        return result.fetchall()

    except SQLAlchemyError as ex:
        logger.exception(ex)


async def fetch_ground_carbon_for_regions(
    db_session: AsyncSession, wkts: List[str], crs: str
):
    crs_int = int(crs)

    try:
        # Prepare the WKT geometries as a string to use in the SQL query
        wkt_list_str = ",".join([f"('{wkt}')" for wkt in wkts])

        statement = text(
            f"""
            WITH rasters AS (
                SELECT 
                    wkt,
                    ST_Union(rast) as union_rast,
                    ST_SetSRID(
                        ST_GeomFromText(wkt), 
                        :crs
                    ) as geom,
                    idx as order_num
                FROM hiilikartta_maaperanhiili_2023_tcha,
                    unnest(array[{wkt_list_str}]) WITH ORDINALITY as indexed_wkt(wkt, idx)
                WHERE ST_Intersects(
                    rast, 
                    ST_SetSRID(
                        ST_GeomFromText(wkt), 
                        :crs
                    )
                )
                GROUP BY wkt, idx
            )
            SELECT 
                array_agg(ST_AsTIFF(ST_Clip(union_rast, geom), 'DEFLATE9')) as tiffs,
                order_num
            FROM rasters
            GROUP BY wkt, order_num;
            """
        )

        result = await db_session.execute(statement, {"crs": crs_int})

        return result.fetchall()

    except SQLAlchemyError as ex:
        logger.exception(ex)
