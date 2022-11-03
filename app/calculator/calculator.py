
import rioxarray as rxr
import rasterio as rio
import geopandas as gpd
import xarray as xr
from geocube.api.core import make_geocube


ha_to_grid = 16 * 16 / 10000
grid_to_ha = 1 / ha_to_grid

class CarbonCalculator:

    def __init__(self):
        carbon_data = rxr.open_rasterio("data/carbon_2022-11-01.tif")

        # no data is 32766, non-forest is 32767
        carbon_data = carbon_data.where(carbon_data < 32766)

        self.carbon_data = carbon_data * ha_to_grid

    def calculate(self, shapefile):
        zone = gpd.read_file(shapefile)

        # the value to fill the area of shapes
        zone["factor"] = 1

        zone_raster = make_geocube(
            zone, resolution=(-16, 16), measurements=["factor"], output_crs="EPSG:3067"
        )

        carbon_arr = zone_raster["factor"] * self.carbon_data

        sum = carbon_arr.sum(skipna=True).item()

        return sum

# %%
# CarbonCalculator().calculate("data/vantaa_yk.shp")