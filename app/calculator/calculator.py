import rioxarray as rxr
import rasterio as rio
import geopandas as gpd
import xarray as xr
from geocube.api.core import make_geocube
import asyncio
import numpy as np
import time

from app.database.query import (
    fetch_variables_for_region,
    fetch_bio_carbon_for_region,
    fetch_ground_carbon_for_region,
)


ha_to_grid = 16 * 16 / 10000
grid_to_ha = 1 / ha_to_grid
crs = "3067"


class CarbonCalculator:
    def __init__(self, shapefile, zoning_col, db_session):
        self.db_session = db_session
        self.zoning_col = zoning_col
        zone = gpd.read_file(shapefile)
        zone = zone.to_crs(f"EPSG:{crs}")

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
            output_crs=f"EPSG:{crs}",
        )

        self.zone_raster = zone_raster

    def add_zone_factors(self, zone):
        zone["factor"] = 1

        return zone

    async def get_area_das(self, zone_df, ds):
        bounds = zone_df.geometry.unary_union.bounds
        minx, miny, maxx, maxy = bounds

        # Define the resolution you want
        res = 1  # Adjust as needed

        # Compute dimensions based on bounding box and resolution
        height = int((maxy - miny) / res)
        width = int((maxx - minx) / res)

        # Define the transform
        transform = rio.transform.from_origin(minx, maxy, res, res)

        crs_code = zone_df.crs.to_string()
        data_arrays = []

        for index, row in zone_df.iterrows():
            # Create a binary mask where the polygon is
            mask = rio.features.geometry_mask(
                [row["geometry"]],
                transform=transform,
                invert=True,
                out_shape=(height, width),
            )

            # Convert mask to xarray DataArray
            da = xr.DataArray(
                np.where(
                    mask, row["factor"], np.nan
                ),  # Assign the "factor" value where the mask is True, otherwise NaN
                dims=("y", "x"),
                coords={
                    "x": np.linspace(minx, maxx - res, width),
                    "y": np.linspace(maxy, miny - res, height),
                },
                name=f"polygon_{row['id']}",
            )
            da.rio.set_crs(crs_code, inplace=True)
            da.attrs["df_index"] = index
            da = da.interp(y=ds.y, x=ds.x)
            data_arrays.append(da)

        return data_arrays

    async def get_variables(self, wkt: str, crs: str) -> xr.Dataset:
        rows, column_names = await fetch_variables_for_region(self.db_session, wkt, crs)

        datasets = []

        for row in rows:
            rast = row[column_names.index("tiff")]

            with rio.MemoryFile(rast).open() as dataset:
                data_array = rxr.open_rasterio(dataset, masked=True)

            # 1. Convert data_array to a dataset
            ds = data_array.to_dataset(name="raster")

            # 2. Add other variables from the row to the dataset
            for col_name in column_names:
                if col_name != "tiff":
                    # Add as a data variable (with the same value for all coordinates since it's a scalar)
                    ds[col_name] = ("y", "x"), [
                        [row[column_names.index(col_name)]] * len(ds["x"])
                    ] * len(ds["y"])

            # 3. Append the dataset to datasets
            datasets.append(ds)

        variables_ds = datasets[0]

        for ds in datasets[1:]:
            variables_ds = xr.combine_by_coords(
                [variables_ds, ds], combine_attrs="override"
            )

        variables_ds = variables_ds.squeeze(dim="band", drop=True)

        return variables_ds

    async def get_bio_carbon(self, wkt: str, crs: str) -> xr.DataArray:
        rast = await fetch_bio_carbon_for_region(self.db_session, wkt, crs)

        with rio.MemoryFile(rast).open() as dataset:
            bio_carbon_da = rxr.open_rasterio(dataset, masked=True)
            # no data is 32766, non-forest is 32767
            bio_carbon_da.where(bio_carbon_da < 32766)

            return bio_carbon_da

    async def get_ground_carbon(self, wkt: str, crs: str) -> xr.DataArray:
        rast = await fetch_ground_carbon_for_region(self.db_session, wkt, crs)

        with rio.MemoryFile(rast).open() as dataset:
            ground_carbon_da = rxr.open_rasterio(dataset, masked=True)
            # no data is 32766, non-forest is 32767
            ground_carbon_da.where(ground_carbon_da < 32766)

            return ground_carbon_da

    def combine_data(
        self,
        variables_ds: xr.Dataset,
        bio_carbon_da: xr.DataArray,
        ground_carbon_da: xr.DataArray,
    ):
        variables_ds["ground_carbon"] = ground_carbon_da.sel(band=1)
        variables_ds["bio_carbon"] = bio_carbon_da.sel(band=1)

        return variables_ds

    async def calculate(self):
        wkt = self.zone.geometry.unary_union.wkt

        self.zone = self.add_zone_factors(self.zone)

        variables_ds, bio_carbon_da, ground_carbon_da = await asyncio.gather(
            self.get_variables(wkt, crs),
            self.get_bio_carbon(wkt, crs),
            self.get_ground_carbon(wkt, crs),
        )

        variables_ds = self.combine_data(variables_ds, bio_carbon_da, ground_carbon_da)

        area_das = await self.get_area_das(self.zone, variables_ds)

        zone = self.zone.copy()

        columns = [
            "bio_carbon_sum",
            "ground_carbon_sum",
            "bio_carbon_per_area",
            "ground_carbon_per_area",
        ]
        years = ["now", "2035", "2045", "2055"]

        all_columns = []

        for col in columns:
            for year in years:
                for suffix in ["nochange", "planned"]:
                    all_columns.append(f"{col}_{year}_{suffix}")

        for col in all_columns:
            zone[col] = None

        for da in area_das:
            bio_carbon_sum = (da * variables_ds["bio_carbon"]).sum(skipna=True).item()
            ground_carbon_sum = (
                (da * variables_ds["ground_carbon"]).sum(skipna=True).item()
            )
            index = da.attrs[
                "df_index"
            ]  # Get the appropriate index/row from the DataArray attribute
            zone.at[index, "bio_carbon_sum_nochange_now"] = bio_carbon_sum
            zone.at[index, "ground_carbon_sum_nochange_now"] = ground_carbon_sum
            zone.at[index, "bio_carbon_per_area_nochange_now"] = (
                bio_carbon_sum * ha_to_grid
            )
            zone.at[index, "ground_carbon_per_area_nochange_now"] = (
                ground_carbon_sum * ha_to_grid
            )

            zone.at[index, "bio_carbon_sum_planned"] = 0
            zone.at[index, "ground_carbon_sum_planned"] = ground_carbon_sum
            zone.at[index, "bio_carbon_per_area_planned"] = 0
            zone.at[index, "ground_carbon_per_area_planned"] = (
                ground_carbon_sum * ha_to_grid
            )

            zone.at[index, "bio_carbon_sum_planned_2035"] = 0
            zone.at[index, "ground_carbon_sum_planned_2035"] = ground_carbon_sum
            zone.at[index, "bio_carbon_per_area_planned_2035"] = 0
            zone.at[index, "ground_carbon_per_area_planned_2035"] = (
                ground_carbon_sum * ha_to_grid
            )

            zone.at[index, "bio_carbon_sum_planned_2045"] = 0
            zone.at[index, "ground_carbon_sum_planned_2045"] = ground_carbon_sum
            zone.at[index, "bio_carbon_per_area_planned_2045"] = 0
            zone.at[index, "ground_carbon_per_area_planned_2045"] = (
                ground_carbon_sum * ha_to_grid
            )

            zone.at[index, "bio_carbon_sum_planned_2055"] = 0
            zone.at[index, "ground_carbon_sum_planned_2055"] = ground_carbon_sum
            zone.at[index, "bio_carbon_per_area_planned_2055"] = 0
            zone.at[index, "ground_carbon_per_area_planned_2055"] = (
                ground_carbon_sum * ha_to_grid
            )

        sums = zone[all_columns].sum()
        combined_geometry = zone.geometry.unary_union

        # Create a new GeoDataFrame with the summed values and combined geometry
        summed_data = {column: [value] for column, value in sums.items()}
        summed_data["geometry"] = [combined_geometry]

        summed_gdf = gpd.GeoDataFrame(summed_data)

        return {
            "areas": zone.to_json(),
            "totals": summed_gdf.to_json(),
            "metadata": {"timestamp": int(time.time())},
        }


# %%
# CarbonCalculator().calculate("data/vantaa_yk.shp")
