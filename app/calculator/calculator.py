import asyncio
import tempfile
import pandas as pd
import rioxarray as rxr
import geopandas as gpd
import xarray as xr
import numpy as np
from datetime import datetime
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Any, Dict, TypedDict, List
import json
from warnings import simplefilter

from app.calculator.utils import get_bm_curve_values_for_years_mabp, get_overlap_mask
from app.db.gis import (
    fetch_bio_carbon_for_regions,
    fetch_ground_carbon_for_regions,
    fetch_rasters_for_regions,
    fetch_variables_for_ids,
)
from app.utils.data_loader import (
    get_bm_curve_df,
    get_area_multipliers_df,
)
from app.utils.logger import get_logger

logger = get_logger(__name__)
simplefilter(action="ignore", category=pd.errors.PerformanceWarning)

grid_to_ha = 16 * 16 / 10_000
ha_to_grid = 1 / grid_to_ha
sqm_to_ha = 1 / 10_000  # 1 hectare is 10,000 square meters
crs = "3067"
zoning_col = "zoning_code"
c_to_co2 = 3.6


class CalculationResult(TypedDict):
    areas: gpd.GeoDataFrame
    totals: gpd.GeoDataFrame
    metadata: Dict[str, str]


class CarbonCalculator:
    def __init__(self, data, sort_col="id"):
        zone = gpd.GeoDataFrame.from_features(data["features"])
        if sort_col and sort_col in zone.columns:
            zone = zone.sort_values(by=sort_col)
        zone.set_geometry("geometry", inplace=True)
        zone.set_crs("EPSG:4326", inplace=True)
        zone = zone.to_crs(f"EPSG:{crs}")

        zone["is_valid"] = zone["geometry"].is_valid
        # Fixing invalid geometries with buffer(0)
        zone.loc[~zone["is_valid"], "geometry"] = zone.loc[
            ~zone["is_valid"], "geometry"
        ].apply(lambda geom: geom.buffer(0))
        # Checking validity again
        zone["is_valid"] = zone["geometry"].is_valid

        if not zone["is_valid"].all():
            raise ValueError(
                "Geometries are not valid, even after trying to fix them with buffer(0)"
            )

        self.simplify_calcs = False
        # Simplify calculations for large areas
        if zone.area.sum() > 50000:
            self.simplify_calcs = True

        if not self.simplify_calcs:
            zone["buffered_geometry"] = zone.geometry.buffer(22.7)

        self.zone: gpd.GeoDataFrame = zone
        self.zone_raster = None

    # def rasterize_zone(self):
    #     if self.zone_raster != None:
    #         return

    #     # the value to fill the area of shapes
    #     self.zone["factor"] = 1

    #     zone_raster = make_geocube(
    #         self.zone,
    #         resolution=(-16, 16),
    #         measurements=["factor"],
    #         output_crs=f"EPSG:{crs}",
    #     )

    #     self.zone_raster = zone_raster

    # def add_zone_factors(self, zone):
    #     zone["factor"] = 1

    #     return zone

    async def get_rasts(
        self, db_session, wkt_list: List[str], crs: str
    ) -> List[xr.DataArray]:
        rasts = await fetch_rasters_for_regions(db_session, wkt_list, crs)
        sorted_rasts = sorted(rasts, key=lambda x: x[1])

        rast_das = []
        for rast in sorted_rasts:
            try:
                with tempfile.NamedTemporaryFile(
                    suffix=".tiff", delete=True
                ) as tmpfile:
                    await asyncio.to_thread(tmpfile.write, rast[0][0])
                    tmpfile.flush()

                    # Use rioxarray to directly open the temporary raster file
                    rast_da: xr.DataArray = rxr.open_rasterio(
                        tmpfile.name, masked=True
                    ).isel(band=0)

                    rast_das.append(rast_da)
            except Exception as e:
                print(e)

        return rast_das

    async def get_variables(self, db_session, ids: List[str]):
        variable_rows, col_names = await fetch_variables_for_ids(db_session, ids)

        variables_dict = {}
        for row in variable_rows:
            variable_dict = dict(zip(col_names, row))
            variables_dict[variable_dict["kuvio"]] = variable_dict

        return variables_dict

    async def get_bio_carbon(
        self, db_session, wkts: List[str], crs: str
    ) -> List[xr.DataArray]:
        rasts = await fetch_bio_carbon_for_regions(db_session, wkts, crs)
        sorted_rasts = sorted(rasts, key=lambda x: x[1])

        rast_das = []
        for rast in sorted_rasts:
            try:
                with tempfile.NamedTemporaryFile(
                    suffix=".tiff", delete=True
                ) as tmpfile:
                    await asyncio.to_thread(tmpfile.write, rast[0][0])
                    tmpfile.flush()

                    # Use rioxarray to directly open the temporary raster file
                    rast_da = rxr.open_rasterio(tmpfile.name, masked=True).isel(band=0)
                    rast_da.where(rast_da < 32766)

                    rast_das.append(rast_da)
            except Exception as e:
                print(e)

        return rast_das

    async def get_ground_carbon(
        self, db_session, wkts: List[str], crs: str
    ) -> List[xr.DataArray]:
        rasts = await fetch_ground_carbon_for_regions(db_session, wkts, crs)
        sorted_rasts = sorted(rasts, key=lambda x: x[1])

        rast_das = []
        for rast in sorted_rasts:
            try:
                with tempfile.NamedTemporaryFile(
                    suffix=".tiff", delete=True
                ) as tmpfile:
                    await asyncio.to_thread(tmpfile.write, rast[0][0])
                    tmpfile.flush()

                    # Use rioxarray to directly open the temporary raster file
                    rast_da = rxr.open_rasterio(tmpfile.name, masked=True).isel(band=0)
                    rast_da.where(rast_da < 32766)

                    rast_das.append(rast_da)
            except Exception as e:
                print(e)

        return rast_das

    # def dummy_combine_data(
    #     self,
    #     variables_ds: xr.Dataset,  # This is not used but still received
    #     bio_carbon_da: xr.DataArray,
    #     ground_carbon_da: xr.DataArray,
    # ):
    #     ds = xr.Dataset(
    #         {
    #             "ground_carbon": ground_carbon_da.sel(band=1),
    #             "bio_carbon": bio_carbon_da.sel(band=1),
    #         }
    #     )

    #     return ds

    # def combine_data(
    #     self,
    #     variables_ds: xr.Dataset,
    #     bio_carbon_da: xr.DataArray,
    #     ground_carbon_da: xr.DataArray,
    # ):
    #     variables_ds["ground_carbon"] = ground_carbon_da.sel(band=1)
    #     variables_ds["bio_carbon"] = bio_carbon_da.sel(band=1)

    #     return variables_ds

    async def calculate_totals(self):
        sum_cols = [
            col for col in self.zone.columns if "nochange" in col or "planned" in col
        ]

        sum_result = self.zone[sum_cols].sum()

        # Merge the results
        # agg_results = {**sum_result.to_dict(), **weighted_averages}
        agg_results = {**sum_result.to_dict()}
        agg_results["geometry"] = self.zone.geometry.unary_union
        summed_gdf = gpd.GeoDataFrame([agg_results], geometry="geometry")
        summed_gdf["area"] = summed_gdf["geometry"].area

        for col in sum_cols:
            new_col = col.replace("_total_", "_ha_")
            summed_gdf[new_col] = summed_gdf[col] / (summed_gdf["area"] * sqm_to_ha)

        summed_gdf.set_crs(epsg=3067, inplace=True)

        return {
            "totals": summed_gdf.to_crs(epsg=4326).to_json(),
            "metadata": {"timestamp": datetime.utcnow()},
        }

    async def calculate(self, db_session: AsyncSession) -> CalculationResult:
        bm_curves_df = get_bm_curve_df()
        area_multipliers_df = get_area_multipliers_df()
        area_multipliers = []

        for index, row in self.zone.iterrows():
            code = row[zoning_col]

            multiplier = 0
            if code in area_multipliers_df.index:
                multiplier = area_multipliers_df.loc[code]["average_efficiency"]

            area_multipliers.append(multiplier)

        wkt_list = []
        if self.simplify_calcs:
            wkt_list = self.zone.geometry.to_wkt().tolist()
        else:
            wkt_list = self.zone.buffered_geometry.to_wkt().tolist()

        rasts = await self.get_rasts(db_session, wkt_list=wkt_list, crs=crs)

        i = 0
        rast_overlaps = []
        for rast in rasts:
            overlap_mask = get_overlap_mask(
                rast, self.zone.iloc[i].geometry, self.simplify_calcs
            )
            rast = rast.where(overlap_mask != 0, np.nan)
            rast_overlaps.append(overlap_mask)
            i += 1

        uniq_vals = np.array([])
        for data_array in rasts:
            uniq_vals = np.concatenate([uniq_vals, np.unique(data_array)])

        uniq_vals = np.unique(uniq_vals[~np.isnan(uniq_vals)])

        uniq_ids_list = [int(val) for val in uniq_vals]

        variables_dict = await self.get_variables(db_session, uniq_ids_list)

        bio_carbon_rasts = await self.get_bio_carbon(db_session, wkt_list, crs=crs)
        ground_carbon_rasts = await self.get_ground_carbon(
            db_session, wkt_list, crs=crs
        )

        bio_carbon_masks = []
        i = 0
        for rast in bio_carbon_rasts:
            overlap_mask = get_overlap_mask(
                rast, self.zone.iloc[i].geometry, self.simplify_calcs
            )
            rast = rast.where(overlap_mask != 0, np.nan)
            bio_carbon_masks.append(overlap_mask)
            i += 1

        ground_carbon_masks = []
        i = 0
        for rast in ground_carbon_rasts:
            overlap_mask = get_overlap_mask(
                rast, self.zone.iloc[i].geometry, self.simplify_calcs
            )
            rast = rast.where(overlap_mask != 0, np.nan)
            ground_carbon_masks.append(overlap_mask)
            i += 1

        calcs_df = self.zone[["id", "geometry", zoning_col]].copy()
        calcs_df["area"] = self.zone.geometry.area
        calcs_df.set_crs(epsg=3067, inplace=True)
        calcs_df.set_geometry("geometry", inplace=True)

        sum_cols = []
        current_year = datetime.now().year
        years_int = [current_year] + list(range(2030, 2100, 5))
        years = [str(year) for year in years_int]

        bm_curve_values, bm_curve_masks = await get_bm_curve_values_for_years_mabp(
            rasts, years, bm_curves_df, variables_dict, rast_overlaps
        )

        # generate bio carbon values
        base_col = "bio_carbon_total"
        bio_masks_no_curve_vals = []
        base_vals = []
        base_vals_no_bm_curve = []
        for index, rast in enumerate(bio_carbon_rasts):
            rast_masked = rast * bio_carbon_masks[index]

            sum = rast_masked.sum().values.item()
            base_vals.append(sum * grid_to_ha)

            # sum_no_bm_curve_val = sum
            # if (bm_curve_masks[index] is not None):
            #     sum_no_bm_curve_val = (rast_masked * ~bm_curve_masks[index]).sum().values.item()
            # base_vals_no_bm_curve.append(sum_no_bm_curve_val)

        for suffix in ["nochange", "planned"]:
            use_multiplier = False
            if suffix == "planned":
                use_multiplier = True

            for year in years:
                vals = []

                for idx, year_dict in enumerate(bm_curve_values):
                    val = base_vals[idx] * c_to_co2
                    if year_dict is not None:
                        val += year_dict[year] * grid_to_ha
                    if use_multiplier and year != str(current_year):
                        vals.append(val * (1 - area_multipliers[idx]))
                    else:
                        vals.append(val)

                col = f"{base_col}_{suffix}_{year}"
                sum_cols.append(col)
                calcs_df[col] = vals

        # generate ground carbon values
        base_col = "ground_carbon_total"
        base_vals = []
        for index, rast in enumerate(ground_carbon_rasts):
            rast_masked = rast * ground_carbon_masks[index]

            sum = rast_masked.sum().values.item()
            base_vals.append(sum * grid_to_ha)

            # if (bm_curve_masks[index] is not None):
            #     sum_no_bm_curve_val = (rast_masked * ~bm_curve_masks[index]).sum().values.item()
            # base_vals_no_bm_curve.append(sum_no_bm_curve_val)

        for suffix in ["nochange", "planned"]:
            use_multiplier = False
            if suffix == "planned":
                use_multiplier = True

            for year in years:
                vals = []

                for idx, base_val in enumerate(base_vals):
                    val = base_val
                    if use_multiplier and year != str(current_year):
                        vals.append(val * (1 - area_multipliers[idx]))
                    else:
                        vals.append(val)

                col = f"{base_col}_{suffix}_{year}"
                sum_cols.append(col)
                calcs_df[col] = vals

        for col in sum_cols:
            new_col = col.replace("_total_", "_ha_")
            calcs_df[new_col] = calcs_df[col] / (calcs_df["area"] * sqm_to_ha)

        # all_columns = all_columns + total_columns

        # sum_cols = [col for col in all_columns if "grid_sum" in col]
        # sum_result = calcs_df[sum_cols].sum()
        cols_to_multiply = [
            col for col in calcs_df.columns if "planned" in col or "nochange" in col
        ]
        # calcs_df[cols_to_multiply] = calcs_df[cols_to_multiply].apply(
        #     pd.to_numeric, errors="coerce"
        # )
        calcs_df[cols_to_multiply] = calcs_df[cols_to_multiply] * c_to_co2

        return_data: CalculationResult = {
            "areas": calcs_df.to_crs(epsg=4326).to_json(),
            "metadata": {"timestamp": datetime.utcnow()},
        }

        return return_data

        # area_das = await self.get_area_das(self.zone, variables_ds)

        # zone = self.zone.copy()

        # columns = [
        #     "bio_carbon_sum",
        #     "ground_carbon_sum",
        #     "bio_carbon_per_area",
        #     "ground_carbon_per_area",
        # ]
        # years = ["now", "2035", "2045", "2055"]

        # all_columns = []

        # for col in columns:
        #     for year in years:
        #         for suffix in ["nochange", "planned"]:
        #             all_columns.append(f"{col}_{suffix}_{year}")

        # for col in all_columns:
        #     zone[col] = None

        # zone["area"] = zone["geometry"].area

        # for da in area_das:
        #     bio_carbon_sum = (da * variables_ds["bio_carbon"]).sum(skipna=True).item()
        #     ground_carbon_sum = (
        #         (da * variables_ds["ground_carbon"]).sum(skipna=True).item()
        #     )
        #     index = da.attrs["df_index"]
        #     area = zone.at[index, "geometry"].area

        #     zone.at[index, "bio_carbon_sum_nochange_now"] = bio_carbon_sum
        #     zone.at[index, "ground_carbon_sum_nochange_now"] = ground_carbon_sum
        #     zone.at[index, "bio_carbon_per_area_nochange_now"] = (
        #         bio_carbon_sum / area
        #     ) * ha_conversion_factor
        #     zone.at[index, "ground_carbon_per_area_nochange_now"] = (
        #         ground_carbon_sum / area
        #     ) * ha_conversion_factor

        #     zone.at[index, "bio_carbon_sum_nochange_2035"] = bio_carbon_sum * 1.1
        #     zone.at[index, "ground_carbon_sum_nochange_2035"] = ground_carbon_sum
        #     zone.at[index, "bio_carbon_per_area_nochange_2035"] = (
        #         (bio_carbon_sum * 1.1) / area
        #     ) * ha_conversion_factor
        #     zone.at[index, "ground_carbon_per_area_nochange_2035"] = (
        #         ground_carbon_sum / area
        #     ) * ha_conversion_factor

        #     zone.at[index, "bio_carbon_sum_nochange_2045"] = bio_carbon_sum * 1.2
        #     zone.at[index, "ground_carbon_sum_nochange_2045"] = ground_carbon_sum
        #     zone.at[index, "bio_carbon_per_area_nochange_2045"] = (
        #         (bio_carbon_sum * 1.2) / area
        #     ) * ha_conversion_factor
        #     zone.at[index, "ground_carbon_per_area_nochange_2045"] = (
        #         ground_carbon_sum / area
        #     ) * ha_conversion_factor

        #     zone.at[index, "bio_carbon_sum_nochange_2055"] = bio_carbon_sum * 1.3
        #     zone.at[index, "ground_carbon_sum_nochange_2055"] = ground_carbon_sum
        #     zone.at[index, "bio_carbon_per_area_nochange_2055"] = (
        #         (bio_carbon_sum * 1.3) / area
        #     ) * ha_conversion_factor
        #     zone.at[index, "ground_carbon_per_area_nochange_2055"] = (
        #         ground_carbon_sum / area
        #     ) * ha_conversion_factor

        #     # planned values
        #     zone.at[index, "bio_carbon_sum_planned_now"] = bio_carbon_sum
        #     zone.at[index, "ground_carbon_sum_planned_now"] = ground_carbon_sum
        #     zone.at[index, "bio_carbon_per_area_planned_now"] = (
        #         bio_carbon_sum / area
        #     ) * ha_conversion_factor
        #     zone.at[index, "ground_carbon_per_area_planned_now"] = (
        #         ground_carbon_sum / area
        #     ) * ha_conversion_factor

        #     zone.at[index, "bio_carbon_sum_planned_2035"] = 0
        #     zone.at[index, "ground_carbon_sum_planned_2035"] = ground_carbon_sum
        #     zone.at[index, "bio_carbon_per_area_planned_2035"] = 0
        #     zone.at[index, "ground_carbon_per_area_planned_2035"] = (
        #         ground_carbon_sum / area
        #     ) * ha_conversion_factor

        #     zone.at[index, "bio_carbon_sum_planned_2045"] = 0
        #     zone.at[index, "ground_carbon_sum_planned_2045"] = ground_carbon_sum
        #     zone.at[index, "bio_carbon_per_area_planned_2045"] = 0
        #     zone.at[index, "ground_carbon_per_area_planned_2045"] = (
        #         ground_carbon_sum / area
        #     ) * ha_conversion_factor

        #     zone.at[index, "bio_carbon_sum_planned_2055"] = 0
        #     zone.at[index, "ground_carbon_sum_planned_2055"] = ground_carbon_sum
        #     zone.at[index, "bio_carbon_per_area_planned_2055"] = 0
        #     zone.at[index, "ground_carbon_per_area_planned_2055"] = (
        #         ground_carbon_sum / area
        #     ) * ha_conversion_factor

        # sum_cols = [col for col in all_columns if "_sum" in col]
        # sum_result = zone[sum_cols].sum()

        # # 2. For all "per_area" columns
        # per_area_cols = [col for col in all_columns if "_per_area" in col]
        # weighted_averages = {}

        # total_area = zone["area"].sum()
        # for col in per_area_cols:
        #     weighted_sum = (zone[col] * zone["area"]).sum()
        #     weighted_averages[col] = weighted_sum / total_area

        # # Merge the results
        # agg_results = {**sum_result.to_dict(), **weighted_averages}
        # agg_results["geometry"] = zone.geometry.unary_union
        # summed_gdf = gpd.GeoDataFrame([agg_results], geometry="geometry")
        # summed_gdf["area"] = summed_gdf["geometry"].area

        # summed_gdf.set_crs(epsg=3067, inplace=True)

        # return_data: CalculationResult = {
        #     "areas": zone.to_crs(epsg=4326).to_json(),
        #     "totals": summed_gdf.to_crs(epsg=4326).to_json(),
        #     "metadata": {"timestamp": datetime.utcnow()},
        # }

        # return return_data


# %%
# CarbonCalculator().calculate("data/vantaa_yk.shp")
