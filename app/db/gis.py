from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.sql import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.utils.logger import get_logger

logger = get_logger(__name__)


async def fetch_variables_for_region(db_session: AsyncSession, wkt: str, crs: str):
    crs = int(crs)

    try:
        statement = text(
            f"""
                SELECT 
                    luke_mvmisegls_2021_muuttujat.*,
                    ST_AsTIFF(rast, 'LZW') as tiff
                FROM luke_mvmisegls_2021_muuttujat
                JOIN luke_mvmisegls_2021_id 
                    ON luke_mvmisegls_2021_muuttujat.kuvio_id = luke_mvmisegls_2021_id.rid
                WHERE ST_Intersects(
                    luke_mvmisegls_2021_id.rast,
                    ST_Transform(
                        ST_SetSRID(
                            ST_GeomFromText(:wkt),
                            :crs
                        ),
                        3067
                    )
                );
                """
        )

        result = await db_session.execute(statement, {"wkt": wkt, "crs": crs})

        rows = result.fetchall()

        # Fetch column names
        column_names = list(result.keys())

        return rows, column_names

    except SQLAlchemyError as ex:
        logger.exception(ex)


async def fetch_bio_carbon_for_region(db_session: AsyncSession, wkt: str, crs: str):
    crs = int(crs)

    try:
        statement = text(
            f"""
                SELECT
                    ST_AsTIFF(ST_UNION(rast), 'LZW') as tiff
                    FROM luke_mvmipuustonhiili_2021_tcha
                    WHERE ST_Intersects(
                        rast, 
                            ST_SetSRID(
                                ST_GeomFromText(
                                    :wkt
                                ),
                            3067
                        )
                    );
                """
        )

        result = await db_session.execute(statement, {"wkt": wkt, "crs": crs})

        return result.fetchall()[0][0]

    except SQLAlchemyError as ex:
        logger.exception(ex)


async def fetch_ground_carbon_for_region(db_session: AsyncSession, wkt: str, crs: str):
    crs = int(crs)

    try:
        statement = text(
            f"""
                SELECT
                    ST_AsTIFF(ST_UNION(rast), 'LZW') as tiff
                    FROM gtkjaluke_maaperanhiili_xxxx_tcha
                    WHERE ST_Intersects(
                        rast, 
                            ST_SetSRID(
                                ST_GeomFromText(
                                    :wkt
                                ),
                            3067
                        )
                    );
                """
        )

        result = await db_session.execute(statement, {"wkt": wkt, "crs": crs})

        return result.fetchall()[0][0]

    except SQLAlchemyError as ex:
        logger.exception(ex)