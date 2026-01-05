# import asyncio
# import tempfile
import pandas as pd
# import rioxarray as rxr
import geopandas as gpd
# import xarray as xr
# import numpy as np
from datetime import datetime
from typing import Any, Dict, TypedDict, List
# import json
from warnings import simplefilter

from app.db.gis import (
    # fetch_bio_carbon_for_regions,
    # fetch_ground_carbon_for_regions,
    # fetch_rasters_for_regions,
    fetch_segment_areas_ha_for_regions,
    fetch_variables_for_ids,
    fetch_weighted_raster_sum_ha_for_regions,
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
c_to_co2 = 44 / 12


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

        # Simplify calculations for large areas
        if zone.area.sum() > 5000:
            self.simplify_calcs = True

        self.simplify_calcs = False
        # if not self.simplify_calcs:
        #     zone["buffered_geometry"] = zone.geometry.buffer(16)

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

    # Unused: legacy codepath for fetching full rasters and processing client-side.
    # async def get_rasts(
    #     self, wkt_list: List[str], crs: str
    # ) -> List[xr.DataArray]:
    #     rasts = await fetch_rasters_for_regions(
    #         wkt_list,
    #         crs,
    #         simplify_calcs=self.simplify_calcs,
    #     )
    #     sorted_rasts = sorted(rasts, key=lambda x: x[1])
    #
    #     rast_das = []
    #     for rast in sorted_rasts:
    #         try:
    #             with tempfile.NamedTemporaryFile(
    #                 suffix=".tiff", delete=True
    #             ) as tmpfile:
    #                 await asyncio.to_thread(tmpfile.write, rast[0])
    #                 tmpfile.flush()
    #
    #                 # Use rioxarray to directly open the temporary raster file
    #                 rast_da: xr.DataArray = rxr.open_rasterio(
    #                     tmpfile.name, masked=True
    #                 ).isel(band=0)
    #
    #                 rast_das.append(rast_da)
    #         except Exception as e:
    #             print(e)
    #
    #     return rast_das

    async def get_variables(self, ids: List[str]):
        variable_rows, col_names = await fetch_variables_for_ids(ids)

        variables_dict = {}
        for row in variable_rows:
            variable_dict = dict(zip(col_names, row))
            variables_dict[variable_dict["kuvio"]] = variable_dict

        return variables_dict

    # Unused: legacy codepath for fetching full rasters and processing client-side.
    # async def get_bio_carbon(
    #     self, wkts: List[str], crs: str
    # ) -> List[xr.DataArray]:
    #     rasts = await fetch_bio_carbon_for_regions(
    #         wkts,
    #         crs,
    #         simplify_calcs=self.simplify_calcs,
    #     )
    #     sorted_rasts = sorted(rasts, key=lambda x: x[1])
    #
    #     rast_das = []
    #     for rast in sorted_rasts:
    #         try:
    #             with tempfile.NamedTemporaryFile(
    #                 suffix=".tiff", delete=True
    #             ) as tmpfile:
    #                 await asyncio.to_thread(tmpfile.write, rast[0])
    #                 tmpfile.flush()
    #
    #                 # Use rioxarray to directly open the temporary raster file
    #                 rast_da = rxr.open_rasterio(tmpfile.name, masked=True).isel(band=0)
    #                 rast_da.where(rast_da < 32766)
    #
    #                 rast_das.append(rast_da)
    #         except Exception as e:
    #             print(e)
    #
    #     return rast_das

    # Unused: legacy codepath for fetching full rasters and processing client-side.
    # async def get_ground_carbon(
    #     self, wkts: List[str], crs: str
    # ) -> List[xr.DataArray]:
    #     rasts = await fetch_ground_carbon_for_regions(
    #         wkts,
    #         crs,
    #         simplify_calcs=self.simplify_calcs,
    #     )
    #     sorted_rasts = sorted(rasts, key=lambda x: x[1])
    #
    #     rast_das = []
    #     for rast in sorted_rasts:
    #         try:
    #             with tempfile.NamedTemporaryFile(
    #                 suffix=".tiff", delete=True
    #             ) as tmpfile:
    #                 await asyncio.to_thread(tmpfile.write, rast[0])
    #                 tmpfile.flush()
    #
    #                 # Use rioxarray to directly open the temporary raster file
    #                 rast_da = rxr.open_rasterio(tmpfile.name, masked=True).isel(band=0)
    #                 rast_da.where(rast_da < 32766)
    #
    #                 rast_das.append(rast_da)
    #         except Exception as e:
    #             print(e)
    #
    #     return rast_das

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

    async def calculate(self) -> CalculationResult:
        bm_curves_df = get_bm_curve_df()
        area_multipliers_df = get_area_multipliers_df()
        area_multipliers_bio = []
        area_multipliers_ground = []

        for index, row in self.zone.iterrows():
            code = row[zoning_col]

            multiplier_bio = 0
            multiplier_ground = 0
            if code in area_multipliers_df.index:
                multiplier_bio = area_multipliers_df.loc[code][
                    "Kasvillisuuden hiiltä säästyy"
                ]
                if isinstance(multiplier_bio, pd.Series):
                    multiplier_bio = multiplier_bio.iloc[0]

                multiplier_ground = area_multipliers_df.loc[code][
                    "Maaperän hiiltä säästyy"
                ]
                if isinstance(multiplier_ground, pd.Series):
                    multiplier_ground = multiplier_ground.iloc[0]

            area_multipliers_bio.append(multiplier_bio)
            area_multipliers_ground.append(multiplier_ground)

        wkt_list = self.zone.geometry.to_wkt().tolist()

        segment_area_rows = await fetch_segment_areas_ha_for_regions(
            wkt_list,
            crs,
            simplify_calcs=self.simplify_calcs,
        )
        bio_sum_rows = await fetch_weighted_raster_sum_ha_for_regions(
            "hiilikartta_kasvillisuudenhiili_2021_tcha",
            wkt_list,
            crs,
            simplify_calcs=self.simplify_calcs,
        )
        ground_sum_rows = await fetch_weighted_raster_sum_ha_for_regions(
            "hiilikartta_maaperanhiili_2023_tcha",
            wkt_list,
            crs,
            simplify_calcs=self.simplify_calcs,
        )

        segment_areas_by_order: Dict[int, Dict[int, float]] = {
            order_num: {} for order_num in range(1, len(wkt_list) + 1)
        }
        for order_num, segment_id, area_ha in segment_area_rows:
            segment_areas_by_order[int(order_num)][int(segment_id)] = float(area_ha)

        bio_sum_by_order: Dict[int, float] = {}
        for order_num, sum_weighted_ha in bio_sum_rows:
            bio_sum_by_order[int(order_num)] = float(sum_weighted_ha or 0.0)

        ground_sum_by_order: Dict[int, float] = {}
        for order_num, sum_weighted_ha in ground_sum_rows:
            ground_sum_by_order[int(order_num)] = float(sum_weighted_ha or 0.0)

        uniq_ids_set = set()
        for segment_areas in segment_areas_by_order.values():
            uniq_ids_set.update(segment_areas.keys())

        uniq_ids_list = sorted(uniq_ids_set)
        variables_dict = {}
        if uniq_ids_list:
            variables_dict = await self.get_variables(uniq_ids_list)

        calcs_df = self.zone[["id", "geometry", zoning_col]].copy()
        calcs_df["area"] = self.zone.geometry.area
        calcs_df.set_crs(epsg=3067, inplace=True)
        calcs_df.set_geometry("geometry", inplace=True)

        sum_cols = []
        current_year = datetime.now().year
        years_int = [current_year] + list(range(2030, 2100, 5))
        years = [str(year) for year in years_int]

        bm_curve_keys = [
            "Region",
            "Maingroup",
            "Soiltype",
            "Drainage",
            "Fertility",
            "Species",
            "Structure",
            "Regime",
        ]
        mabp_lookup: Dict[tuple[int, ...], float] = {}
        for _, row in bm_curves_df.iterrows():
            key = tuple(int(row[col]) for col in bm_curve_keys)
            if key not in mabp_lookup:
                mabp_lookup[key] = float(row["Mabp"])

        segment_mabp: Dict[int, float] = {}
        for segment_id in uniq_ids_list:
            variables = variables_dict.get(segment_id)
            if not variables:
                continue
            try:
                key = tuple(int(variables[col]) for col in bm_curve_keys)
            except Exception:
                continue
            mabp = mabp_lookup.get(key)
            if mabp is not None:
                segment_mabp[segment_id] = mabp

        bm_curve_contrib_by_order: Dict[int, Dict[str, float]] = {}
        for order_num, segment_areas in segment_areas_by_order.items():
            year_totals = {year: 0.0 for year in years}
            was_found = False
            for segment_id, area_ha in segment_areas.items():
                mabp = segment_mabp.get(segment_id)
                if mabp is None:
                    continue
                was_found = True
                for year in years:
                    year_diff = int(year) - 2021
                    year_totals[year] += area_ha * mabp * year_diff
            if was_found:
                bm_curve_contrib_by_order[order_num] = year_totals

        # generate bio carbon values
        base_col = "bio_carbon_total"
        base_vals = [
            bio_sum_by_order.get(order_num, 0.0) * c_to_co2
            for order_num in range(1, len(wkt_list) + 1)
        ]

        for suffix in ["nochange", "planned"]:
            use_multiplier = False
            if suffix == "planned":
                use_multiplier = True

            for year in years:
                vals = []

                for idx, base_val in enumerate(base_vals):
                    val = base_val
                    contrib = bm_curve_contrib_by_order.get(idx + 1)
                    if contrib is not None:
                        val += contrib[year]
                    if use_multiplier and year != str(current_year):
                        vals.append(val * area_multipliers_bio[idx])
                    else:
                        vals.append(val)

                col = f"{base_col}_{suffix}_{year}"
                sum_cols.append(col)
                calcs_df[col] = vals

        # generate ground carbon values
        base_col = "ground_carbon_total"
        base_vals = [
            ground_sum_by_order.get(order_num, 0.0) * c_to_co2
            for order_num in range(1, len(wkt_list) + 1)
        ]

        for suffix in ["nochange", "planned"]:
            use_multiplier = False
            if suffix == "planned":
                use_multiplier = True

            for year in years:
                vals = []

                for idx, base_val in enumerate(base_vals):
                    val = base_val
                    if use_multiplier and year != str(current_year):
                        vals.append(val * area_multipliers_ground[idx])
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
        # cols_to_process = [
        #     col for col in calcs_df.columns if "planned" in col or "nochange" in col
        # ]
        # calcs_df[cols_to_process] = calcs_df[cols_to_process].apply(
        #     pd.to_numeric, errors="coerce"
        # )

        # calcs_df[cols_to_multiply] = calcs_df[cols_to_multiply] * c_to_co2
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
