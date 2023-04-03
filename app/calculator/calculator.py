import rioxarray as rxr
import rasterio as rio
import geopandas as gpd
import xarray as xr
from geocube.api.core import make_geocube

from app.database.query import fetch_raster_for_region


ha_to_grid = 16 * 16 / 10000
grid_to_ha = 1 / ha_to_grid


class CarbonCalculator:
    def __init__(self, shapefile, zoning_col, db_session):
        self.db_session = db_session
        self.zoning_col = zoning_col
        zone = gpd.read_file(shapefile)

        self.zone: gpd.GeoDataFrame = zone
        self.zone_raster = None

    def rasterize_zone(self):
        if self.zone_raster != None:
            return

        # the value to fill the area of shapes
        self.zone["factor"] = 1

        zone_raster = make_geocube(
            self.zone,
            resolution=(-16, 16),
            measurements=["factor"],
            output_crs="EPSG:3067",
        )

        self.zone_raster = zone_raster

    async def calculate(self):
        wkt = self.zone.geometry.unary_union.wkt

        self.rasterize_zone()

        rast = await fetch_raster_for_region(self.db_session, wkt, 4326)

        if (rast == None):
            return None

        with rio.MemoryFile(rast).open() as dataset:
            carbon_data = rxr.open_rasterio(dataset)

        # no data is 32766, non-forest is 32767
        carbon_data = carbon_data.where(carbon_data < 32766)
        carbon_data = carbon_data * ha_to_grid

        carbon_arr = self.zone_raster["factor"] * carbon_data


        sum = carbon_arr.sum(skipna=True).item()
        area = self.zone.geometry.unary_union.area

        return {"sum": sum, "area": area}


# %%
# CarbonCalculator().calculate("data/vantaa_yk.shp")
