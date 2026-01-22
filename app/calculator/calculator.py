# import asyncio
# import tempfile
import pandas as pd
# import rioxarray as rxr
import geopandas as gpd
# import xarray as xr
# import numpy as np
import math
from datetime import datetime
from typing import Any, Dict, TypedDict, List, Optional, Tuple
# import json
from warnings import simplefilter

from app.db.gis import (
    fetch_natcode_for_regions,
    fetch_segment_areas_ha_for_regions,
    fetch_variables_for_ids,
    fetch_weighted_raster_sum_ha_for_regions,
)
from app.utils.data_loader import get_bm_curve_df, get_landuse_sequestration_df
from app.utils.logger import get_logger

logger = get_logger(__name__)
simplefilter(action="ignore", category=pd.errors.PerformanceWarning)

grid_to_ha = 16 * 16 / 10_000
ha_to_grid = 1 / grid_to_ha
sqm_to_ha = 1 / 10_000  # 1 hectare is 10,000 square meters
crs = "3067"
zoning_col = "zoning_code"
c_to_co2 = 44 / 12

LANDUSE_BUILT_COL = "landuse_built"
LANDUSE_NEW_OPEN_VEG_COL = "landuse_new_open_vegetation"
LANDUSE_NEW_TREE_VEG_COL = "landuse_new_tree_vegetation"
LANDUSE_EXISTING_COL = "landuse_existing"

LANDUSE_ALIAS_COLS: Dict[str, List[str]] = {
    LANDUSE_BUILT_COL: ["rakennettu", "Rakennettu"],
    LANDUSE_NEW_OPEN_VEG_COL: ["uusi_avoin_kasvipeite", "Uusi_avoin_kasvipeite"],
    LANDUSE_NEW_TREE_VEG_COL: [
        "uusi_puustoinen_kasvipeite",
        "Uusi_puustoinen_kasvipeite",
    ],
    LANDUSE_EXISTING_COL: ["aiempi_maanpeite", "Aiempi_maanpeite"],
}

SOIL_CHANGE_NEW_VEG_PCT_COL = "soil_change_new_vegetation_pct"
SOIL_CHANGE_NEW_VEG_PCT_ALIASES = [
    "Maaperan_muutos_uuden_kasvipeitteen_alueilla",
    "maaperan_muutos_uuden_kasvipeitteen_alueilla",
]

SEQUESTRATION_COL_VEG_OPEN = (
    "Uusi_avoin_kasvillisuus_kasvillisuuden_hiilensidonta_t_CO2"
)
SEQUESTRATION_COL_VEG_TREE = (
    "Uusi_puustoinen_kasvillisuus_kasvillisuuden_hiilensidonta_t_CO2"
)
SEQUESTRATION_COL_SOIL_OPEN = "Uusi_avoin_kasvillisuus_maaperan_hiilensidonta_t_CO2"
SEQUESTRATION_COL_SOIL_TREE = (
    "Uusi_puustoinen_kasvillisuus_maaperan_hiilensidonta_t_CO2"
)


class CalculationResult(TypedDict):
    areas: gpd.GeoDataFrame
    totals: gpd.GeoDataFrame
    metadata: Dict[str, str]


def _coerce_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, str) and not value.strip():
        return None
    try:
        cast_value = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(cast_value):
        return None
    return cast_value


def _get_pct_value(row: pd.Series, key: str, aliases: List[str]) -> Optional[float]:
    if key in row and row[key] is not None:
        value = _coerce_float(row[key])
        if value is not None:
            return value
    for alias in aliases:
        if alias in row and row[alias] is not None:
            value = _coerce_float(row[alias])
            if value is not None:
                return value
    return None


def _validate_landuse_percentages(
    built_pct: float,
    new_open_pct: float,
    new_tree_pct: float,
    existing_pct: float,
    *,
    tolerance: float = 1e-6,
) -> Tuple[float, float, float, float]:
    for name, value in [
        (LANDUSE_BUILT_COL, built_pct),
        (LANDUSE_NEW_OPEN_VEG_COL, new_open_pct),
        (LANDUSE_NEW_TREE_VEG_COL, new_tree_pct),
        (LANDUSE_EXISTING_COL, existing_pct),
    ]:
        if value < -tolerance or value > 100 + tolerance:
            raise ValueError(f"{name} must be between 0 and 100, got {value}")

    total = built_pct + new_open_pct + new_tree_pct + existing_pct
    if not math.isfinite(total) or total <= 0:
        raise ValueError(
            "Landuse percentages must sum to 100; got a non-positive total."
        )

    if abs(total - 100.0) <= tolerance:
        return built_pct, new_open_pct, new_tree_pct, existing_pct

    # Allow small drift and normalize; reject large mismatches to avoid hiding input errors.
    if abs(total - 100.0) <= 1e-2:
        scale = 100.0 / total
        return (
            built_pct * scale,
            new_open_pct * scale,
            new_tree_pct * scale,
            existing_pct * scale,
        )

    raise ValueError(
        f"Landuse percentages must sum to 100; got {total} (built={built_pct}, "
        f"new_open={new_open_pct}, new_tree={new_tree_pct}, existing={existing_pct})."
    )


_BM_CURVE_SERIES_CACHE: Optional[
    Tuple[
        int,
        Dict[Tuple[int, ...], List[float]],
        Dict[Tuple[int, ...], List[float]],
    ]
] = None


def _get_bm_curve_series_by_key(
    bm_curves_df: pd.DataFrame,
) -> Tuple[int, Dict[Tuple[int, ...], List[float]], Dict[Tuple[int, ...], List[float]]]:
    """
    Build (and cache) a mapping from categorical variables to the biomass curve's
    yearly series (year1..yearN).

    Returns:
      - max_year: maximum year index available (N)
      - by_key_rotation: key includes Rotation
      - by_key_no_rotation: key excludes Rotation (fallback)
    """
    global _BM_CURVE_SERIES_CACHE
    if _BM_CURVE_SERIES_CACHE is not None:
        return _BM_CURVE_SERIES_CACHE

    key_cols_with_rotation = [
        "Region",
        "Maingroup",
        "Soiltype",
        "Drainage",
        "Fertility",
        "Species",
        "Structure",
        "Regime",
        "Rotation",
    ]
    key_cols_no_rotation = key_cols_with_rotation[:-1]

    def _year_col_num(col: str) -> int:
        try:
            return int(col.replace("year", ""))
        except Exception:
            return -1

    year_cols = [col for col in bm_curves_df.columns if col.startswith("year")]
    year_cols.sort(key=_year_col_num)
    max_year = _year_col_num(year_cols[-1]) if year_cols else 0

    by_key_rotation: Dict[Tuple[int, ...], List[float]] = {}
    by_key_no_rotation: Dict[Tuple[int, ...], List[float]] = {}

    for _, row in bm_curves_df.iterrows():
        try:
            key_rot = tuple(int(row[col]) for col in key_cols_with_rotation)
            key_no_rot = tuple(int(row[col]) for col in key_cols_no_rotation)
        except Exception:
            continue

        series = []
        for col in year_cols:
            value = _coerce_float(row[col])
            series.append(0.0 if value is None else float(value))

        if key_rot not in by_key_rotation:
            by_key_rotation[key_rot] = series
        if key_no_rot not in by_key_no_rotation:
            by_key_no_rotation[key_no_rot] = series

    _BM_CURVE_SERIES_CACHE = (max_year, by_key_rotation, by_key_no_rotation)
    return _BM_CURVE_SERIES_CACHE


def _bm_curve_value_at_age(series: List[float], age_years: int) -> float:
    if not series:
        return 0.0
    if age_years <= 0:
        return series[0]
    if age_years > len(series):
        return series[-1]
    return series[age_years - 1]


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
            col
            for col in self.zone.columns
            if "_total_" in col and ("nochange" in col or "planned" in col)
        ]

        sum_result = self.zone[sum_cols].sum(numeric_only=True)

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
        sequestration_df = get_landuse_sequestration_df()

        if zoning_col not in self.zone.columns:
            raise ValueError(f"Missing required column: {zoning_col}")

        wkt_list = self.zone.geometry.to_wkt().tolist()
        area_count = len(wkt_list)

        has_any_landuse_cols = any(
            col in self.zone.columns
            for col in (
                [
                    LANDUSE_BUILT_COL,
                    LANDUSE_NEW_OPEN_VEG_COL,
                    LANDUSE_NEW_TREE_VEG_COL,
                    LANDUSE_EXISTING_COL,
                ]
                + [alias for aliases in LANDUSE_ALIAS_COLS.values() for alias in aliases]
            )
        )

        built_pcts: List[float] = []
        new_open_pcts: List[float] = []
        new_tree_pcts: List[float] = []
        existing_pcts: List[float] = []
        soil_change_new_veg_pcts: List[float] = []

        for _, row in self.zone.iterrows():
            if has_any_landuse_cols:
                built = _get_pct_value(row, LANDUSE_BUILT_COL, LANDUSE_ALIAS_COLS[LANDUSE_BUILT_COL])
                new_open = _get_pct_value(
                    row, LANDUSE_NEW_OPEN_VEG_COL, LANDUSE_ALIAS_COLS[LANDUSE_NEW_OPEN_VEG_COL]
                )
                new_tree = _get_pct_value(
                    row, LANDUSE_NEW_TREE_VEG_COL, LANDUSE_ALIAS_COLS[LANDUSE_NEW_TREE_VEG_COL]
                )
                existing = _get_pct_value(
                    row, LANDUSE_EXISTING_COL, LANDUSE_ALIAS_COLS[LANDUSE_EXISTING_COL]
                )
                if None in (built, new_open, new_tree, existing):
                    raise ValueError(
                        "Missing one or more required landuse percentage columns: "
                        f"{LANDUSE_BUILT_COL}, {LANDUSE_NEW_OPEN_VEG_COL}, "
                        f"{LANDUSE_NEW_TREE_VEG_COL}, {LANDUSE_EXISTING_COL}"
                    )
                built, new_open, new_tree, existing = _validate_landuse_percentages(
                    float(built),
                    float(new_open),
                    float(new_tree),
                    float(existing),
                )
            else:
                built, new_open, new_tree, existing = 0.0, 0.0, 0.0, 100.0

            soil_change_pct = _get_pct_value(
                row, SOIL_CHANGE_NEW_VEG_PCT_COL, SOIL_CHANGE_NEW_VEG_PCT_ALIASES
            )
            if soil_change_pct is None:
                soil_change_pct = 0.0
            if soil_change_pct < 0 or soil_change_pct > 100:
                raise ValueError(
                    f"{SOIL_CHANGE_NEW_VEG_PCT_COL} must be between 0 and 100, got {soil_change_pct}"
                )

            built_pcts.append(float(built))
            new_open_pcts.append(float(new_open))
            new_tree_pcts.append(float(new_tree))
            existing_pcts.append(float(existing))
            soil_change_new_veg_pcts.append(float(soil_change_pct))

        natcode_rows = await fetch_natcode_for_regions(wkt_list, crs)
        natcode_by_order: Dict[int, str] = {
            int(order_num): str(natcode) for order_num, natcode in natcode_rows
        }
        natcodes: List[Optional[str]] = [
            natcode_by_order.get(order_num) for order_num in range(1, area_count + 1)
        ]
        maakunta_codes: List[Optional[int]] = []
        for natcode in natcodes:
            if natcode is None:
                maakunta_codes.append(None)
                continue
            try:
                maakunta_codes.append(int(str(natcode)))
            except ValueError:
                maakunta_codes.append(None)

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

        bio_sum_by_order: Dict[int, float] = {
            int(order_num): float(sum_weighted_ha or 0.0)
            for order_num, sum_weighted_ha in bio_sum_rows
        }
        ground_sum_by_order: Dict[int, float] = {
            int(order_num): float(sum_weighted_ha or 0.0)
            for order_num, sum_weighted_ha in ground_sum_rows
        }

        segment_area_rows = await fetch_segment_areas_ha_for_regions(
            wkt_list,
            crs,
            simplify_calcs=self.simplify_calcs,
        )
        segment_areas_by_order: Dict[int, Dict[int, float]] = {
            order_num: {} for order_num in range(1, area_count + 1)
        }
        for order_num, segment_id, area_ha in segment_area_rows:
            segment_areas_by_order[int(order_num)][int(segment_id)] = float(area_ha)

        uniq_segment_ids: set[int] = set()
        for segment_areas in segment_areas_by_order.values():
            uniq_segment_ids.update(segment_areas.keys())

        variables_dict: Dict[int, Dict[str, Any]] = {}
        if uniq_segment_ids:
            variables_dict = await self.get_variables(sorted(uniq_segment_ids))

        base_cols = ["geometry", zoning_col]
        if "id" in self.zone.columns:
            base_cols.insert(0, "id")
        calcs_df = self.zone[base_cols].copy()
        calcs_df["area"] = self.zone.geometry.area
        calcs_df["area_ha"] = calcs_df["area"] * sqm_to_ha
        calcs_df[LANDUSE_BUILT_COL] = built_pcts
        calcs_df[LANDUSE_NEW_OPEN_VEG_COL] = new_open_pcts
        calcs_df[LANDUSE_NEW_TREE_VEG_COL] = new_tree_pcts
        calcs_df[LANDUSE_EXISTING_COL] = existing_pcts
        calcs_df[SOIL_CHANGE_NEW_VEG_PCT_COL] = soil_change_new_veg_pcts
        calcs_df["natcode"] = natcodes
        calcs_df.set_crs(epsg=3067, inplace=True)
        calcs_df.set_geometry("geometry", inplace=True)

        current_year = datetime.now().year
        years_int = [current_year] + list(range(2030, 2100, 5))

        base_bio_co2 = [
            bio_sum_by_order.get(order_num, 0.0) * c_to_co2
            for order_num in range(1, area_count + 1)
        ]
        base_soil_co2 = [
            ground_sum_by_order.get(order_num, 0.0) * c_to_co2
            for order_num in range(1, area_count + 1)
        ]

        existing_fracs = [pct / 100.0 for pct in existing_pcts]
        new_open_fracs = [pct / 100.0 for pct in new_open_pcts]
        new_tree_fracs = [pct / 100.0 for pct in new_tree_pcts]
        new_veg_fracs = [
            (new_open_pcts[i] + new_tree_pcts[i]) / 100.0 for i in range(area_count)
        ]
        soil_retention_fracs = [
            1.0 - (soil_change_new_veg_pcts[i] / 100.0) for i in range(area_count)
        ]

        planned_bio_base_co2 = [
            existing_fracs[i] * base_bio_co2[i] for i in range(area_count)
        ]
        planned_soil_base_co2 = [
            existing_fracs[i] * base_soil_co2[i]
            + new_veg_fracs[i] * soil_retention_fracs[i] * base_soil_co2[i]
            for i in range(area_count)
        ]

        veg_open_coeffs: List[float] = []
        veg_tree_coeffs: List[float] = []
        soil_open_coeffs: List[float] = []
        soil_tree_coeffs: List[float] = []

        for idx in range(area_count):
            maakunta = maakunta_codes[idx]
            code = str(self.zone.iloc[idx][zoning_col]).strip()
            k_veg_open = 0.0
            k_veg_tree = 0.0
            k_soil_open = 0.0
            k_soil_tree = 0.0
            if maakunta is not None:
                key = (maakunta, code)
                if key in sequestration_df.index:
                    coeff_row = sequestration_df.loc[key]
                    if isinstance(coeff_row, pd.DataFrame):
                        coeff_row = coeff_row.iloc[0]
                    k_veg_open = _coerce_float(coeff_row.get(SEQUESTRATION_COL_VEG_OPEN))
                    k_veg_tree = _coerce_float(coeff_row.get(SEQUESTRATION_COL_VEG_TREE))
                    k_soil_open = _coerce_float(coeff_row.get(SEQUESTRATION_COL_SOIL_OPEN))
                    k_soil_tree = _coerce_float(coeff_row.get(SEQUESTRATION_COL_SOIL_TREE))

                    k_veg_open = 0.0 if k_veg_open is None else float(k_veg_open)
                    k_veg_tree = 0.0 if k_veg_tree is None else float(k_veg_tree)
                    k_soil_open = 0.0 if k_soil_open is None else float(k_soil_open)
                    k_soil_tree = 0.0 if k_soil_tree is None else float(k_soil_tree)

            veg_open_coeffs.append(k_veg_open)
            veg_tree_coeffs.append(k_veg_tree)
            soil_open_coeffs.append(k_soil_open)
            soil_tree_coeffs.append(k_soil_tree)

        veg_changed_rate_co2_per_year: List[float] = []
        soil_changed_rate_co2_per_year: List[float] = []
        for idx in range(area_count):
            area_ha = float(calcs_df.iloc[idx]["area_ha"] or 0.0)
            veg_rate = area_ha * (
                new_open_fracs[idx] * veg_open_coeffs[idx]
                + new_tree_fracs[idx] * veg_tree_coeffs[idx]
            )
            soil_rate = area_ha * (
                new_open_fracs[idx] * soil_open_coeffs[idx]
                + new_tree_fracs[idx] * soil_tree_coeffs[idx]
            )
            veg_changed_rate_co2_per_year.append(float(veg_rate))
            soil_changed_rate_co2_per_year.append(float(soil_rate))

        variables_base_year = 2021
        max_year, curve_by_key_rot, curve_by_key_no_rot = _get_bm_curve_series_by_key(
            bm_curves_df
        )

        veg_curve_delta_co2_by_order: Dict[int, Dict[int, float]] = {
            order_num: {year: 0.0 for year in years_int}
            for order_num in range(1, area_count + 1)
        }

        curve_key_cols_no_rotation = [
            "Region",
            "Maingroup",
            "Soiltype",
            "Drainage",
            "Fertility",
            "Species",
            "Structure",
            "Regime",
        ]
        curve_key_cols_with_rotation = curve_key_cols_no_rotation + ["Rotation"]

        def _get_int_var(variables: Dict[str, Any], candidates: List[str]) -> Optional[int]:
            for key in candidates:
                if key in variables and variables[key] is not None:
                    try:
                        return int(variables[key])
                    except (TypeError, ValueError):
                        continue
            return None

        for order_num, segment_areas in segment_areas_by_order.items():
            for segment_id, segment_area_ha in segment_areas.items():
                variables = variables_dict.get(segment_id)
                if not variables:
                    continue

                age_base = _get_int_var(variables, ["Age", "age"])
                if age_base is None:
                    continue

                key_no_rot: Optional[Tuple[int, ...]] = None
                key_rot: Optional[Tuple[int, ...]] = None

                try:
                    key_no_rot = tuple(int(variables[col]) for col in curve_key_cols_no_rotation)
                except Exception:
                    key_no_rot = None

                rotation = _get_int_var(variables, ["Rotation", "rotation"])
                if key_no_rot is not None and rotation is not None:
                    key_rot = tuple(list(key_no_rot) + [int(rotation)])

                series = None
                if key_rot is not None:
                    series = curve_by_key_rot.get(key_rot)
                if series is None and key_no_rot is not None:
                    series = curve_by_key_no_rot.get(key_no_rot)
                if series is None:
                    continue

                age_base_at_raster_year = max(0, min(age_base, max_year))
                value_base = _bm_curve_value_at_age(series, age_base_at_raster_year)

                for year in years_int:
                    age_year = age_base + (year - variables_base_year)
                    age_year = max(0, min(age_year, max_year))
                    value_year = _bm_curve_value_at_age(series, age_year)
                    delta_per_ha = value_year - value_base
                    veg_curve_delta_co2_by_order[order_num][year] += (
                        float(segment_area_ha) * float(delta_per_ha) * c_to_co2
                    )

        sum_cols: List[str] = []

        veg_base_col = "bio_carbon_total"
        soil_base_col = "ground_carbon_total"

        for year in years_int:
            delta_years = max(0, int(year) - int(current_year))
            veg_nochange_vals: List[float] = []
            veg_planned_vals: List[float] = []
            soil_nochange_vals: List[float] = []
            soil_planned_vals: List[float] = []

            for idx in range(area_count):
                order_num = idx + 1
                veg_delta = veg_curve_delta_co2_by_order.get(order_num, {}).get(year, 0.0)

                veg_nochange_vals.append(base_bio_co2[idx] + veg_delta)
                soil_nochange_vals.append(base_soil_co2[idx])

                veg_planned_vals.append(
                    planned_bio_base_co2[idx]
                    + existing_fracs[idx] * veg_delta
                    + veg_changed_rate_co2_per_year[idx] * delta_years
                )
                soil_planned_vals.append(
                    planned_soil_base_co2[idx]
                    + soil_changed_rate_co2_per_year[idx] * delta_years
                )

            veg_nochange_col = f"{veg_base_col}_nochange_{year}"
            veg_planned_col = f"{veg_base_col}_planned_{year}"
            soil_nochange_col = f"{soil_base_col}_nochange_{year}"
            soil_planned_col = f"{soil_base_col}_planned_{year}"

            calcs_df[veg_nochange_col] = veg_nochange_vals
            calcs_df[veg_planned_col] = veg_planned_vals
            calcs_df[soil_nochange_col] = soil_nochange_vals
            calcs_df[soil_planned_col] = soil_planned_vals

            sum_cols.extend(
                [veg_nochange_col, veg_planned_col, soil_nochange_col, soil_planned_col]
            )

        for col in sum_cols:
            new_col = col.replace("_total_", "_ha_")
            calcs_df[new_col] = calcs_df[col] / calcs_df["area_ha"]

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
        self.zone = calcs_df
        totals_data = await self.calculate_totals()

        return_data: CalculationResult = {
            "areas": calcs_df.to_crs(epsg=4326).to_json(),
            "totals": totals_data["totals"],
            "metadata": totals_data["metadata"],
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
