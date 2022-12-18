from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.sql import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.utils.logger import get_logger

logger = get_logger(__name__)


async def fetch_raster_for_region(db_session: AsyncSession, wkt, crs):
    try:
        statement = text(
            f"""
                    SELECT
                    ST_AsTIFF(ST_Union(rast), 'LZW') as tiff
                    FROM vantaa_luke_mvmi2017_segmpuustonhiili10kgha
                    WHERE ST_Intersects(
                    rast, 
                    ST_Transform(
                        ST_SetSRID(
                            ST_GeomFromText(
                                :wkt
                            ), 
                            :crs
                        ), 
                        3067
                    )
                    );
                """
        )

        await db_session.execute(text("SET postgis.gdal_enabled_drivers TO 'GTiff';"))
        result = await db_session.execute(statement, {"wkt": wkt, "crs": crs})

        return result.fetchall()[0][0]

    except SQLAlchemyError as ex:
        logger.exception(ex)
