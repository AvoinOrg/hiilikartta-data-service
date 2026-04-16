# import asyncio
# import tempfile
from bisect import bisect_right
from datetime import datetime
import math
from typing import Any, Dict, List, NamedTuple, Optional, Tuple, TypedDict
from warnings import simplefilter

import geopandas as gpd
import pandas as pd

from app.db.gis import (
    fetch_natcode_for_regions,
    fetch_segment_areas_ha_for_regions,
    fetch_variables_for_ids,
    fetch_weighted_raster_sum_ha_by_segment_for_regions,
)
from app.utils.data_loader import (
    DEFAULT_FORESTRY_SCENARIO,
    get_bm_curve_df,
    get_landuse_sequestration_df,
    get_soil_curve_df,
    validate_forestry_scenario,
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

SEQUESTRATION_COL_VEG_OPEN = "Uusi_avoin_kasvillisuus_kasvillisuuden_hiilensidonta_t_C"
SEQUESTRATION_COL_VEG_TREE = (
    "Uusi_puustoinen_kasvillisuus_kasvillisuuden_hiilensidonta_t_C"
)
SEQUESTRATION_COL_SOIL_OPEN = "Uusi_avoin_kasvillisuus_maaperan_hiilensidonta_t_C"
SEQUESTRATION_COL_SOIL_TREE = "Uusi_puustoinen_kasvillisuus_maaperan_hiilensidonta_t_C"

POWERLINE_ZONING_CODES = {"ENsl", "ENslja"}
POWERLINE_BIOMASS_MAINGROUP = 4
FORECAST_END_YEAR = 2080


class CalculationResult(TypedDict):
    areas: gpd.GeoDataFrame
    totals: gpd.GeoDataFrame
    metadata: Dict[str, Any]


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


CurveKey = Tuple[int, int, int, int, int, int, int]


class CurveInfo(NamedTuple):
    series: List[float]
    max_carbon: Optional[float]


_CURVE_TABLE_CACHE: Dict[
    Tuple[str, int],
    Tuple[int, Dict[CurveKey, Dict[int, CurveInfo]], Dict[CurveKey, List[int]]],
] = {}


def _year_col_num(col: str) -> int:
    try:
        return int(col.replace("year", ""))
    except Exception:
        return -1


def _curve_value_at_offset(series: List[float], offset_years: int) -> float:
    if not series:
        return 0.0
    clamped_offset = max(0, min(int(offset_years), len(series) - 1))
    return float(series[clamped_offset])


def _relative_curve_offset(
    age_base: int,
    init_age: int,
    year: int,
    variables_base_year: int,
    *,
    reset_to_year0: bool = False,
) -> int:
    if reset_to_year0:
        return int(year) - int(variables_base_year)
    return int(age_base) + (int(year) - int(variables_base_year)) - int(init_age)


def _curve_scale_factor(base_curve_value: float, target_curve_value: float) -> float:
    if float(base_curve_value) <= 0.0:
        return 1.0
    return float(target_curve_value) / float(base_curve_value)


def _build_reporting_years(
    current_year: int, *, forecast_end_year: int = FORECAST_END_YEAR
) -> List[int]:
    current_year_int = int(current_year)
    return [current_year_int] + [
        year
        for year in range(2030, int(forecast_end_year) + 1, 5)
        if year > current_year_int
    ]


def _get_int_var(variables: Dict[str, Any], candidates: List[str]) -> Optional[int]:
    for key in candidates:
        if key in variables and variables[key] is not None:
            try:
                return int(variables[key])
            except (TypeError, ValueError):
                continue
    return None


def _get_float_var(variables: Dict[str, Any], candidates: List[str]) -> Optional[float]:
    for key in candidates:
        if key in variables and variables[key] is not None:
            value = _coerce_float(variables[key])
            if value is not None:
                return float(value)
    return None


def _get_curve_key(
    variables: Dict[str, Any],
    forestry_scenario: int,
    *,
    maingroup_override: Optional[int] = None,
) -> Optional[CurveKey]:
    region = _get_int_var(variables, ["Region", "region"])
    maingroup = maingroup_override
    if maingroup is None:
        maingroup = _get_int_var(variables, ["Maingroup", "maingroup"])
    soiltype = _get_int_var(variables, ["Soiltype", "soiltype"])
    drainage = _get_int_var(variables, ["Drainage", "drainage"])
    fertility = _get_int_var(variables, ["Fertility", "fertility"])
    species = _get_int_var(variables, ["Species", "species"])

    if None in (region, maingroup, soiltype, drainage, fertility, species):
        return None

    return (
        int(forestry_scenario),
        int(region),
        int(maingroup),
        int(soiltype),
        int(drainage),
        int(fertility),
        int(species),
    )


def _select_init_age_bucket(age: int, init_ages: List[int]) -> Optional[int]:
    if not init_ages:
        return None

    age_int = int(age)
    idx = bisect_right(init_ages, age_int) - 1
    if idx < 0:
        return init_ages[0]
    return init_ages[idx]


def _build_curve_tables(
    curves_df: pd.DataFrame, *, cache_name: str, include_max_carbon: bool
) -> Tuple[int, Dict[CurveKey, Dict[int, CurveInfo]], Dict[CurveKey, List[int]]]:
    cache_key = (cache_name, id(curves_df))
    if cache_key in _CURVE_TABLE_CACHE:
        return _CURVE_TABLE_CACHE[cache_key]

    year_cols = [col for col in curves_df.columns if col.startswith("year")]
    year_cols.sort(key=_year_col_num)
    max_year = _year_col_num(year_cols[-1]) if year_cols else 0

    curves_by_key: Dict[CurveKey, Dict[int, CurveInfo]] = {}
    init_ages_by_key: Dict[CurveKey, List[int]] = {}

    key_cols = [
        "Scen",
        "Region",
        "Maingroup",
        "Soiltype",
        "Drainage",
        "Fertility",
        "Species",
    ]

    for _, row in curves_df.iterrows():
        try:
            key = tuple(int(row[col]) for col in key_cols)
            init_age = int(row["InitAge"])
        except Exception:
            continue

        series = []
        for col in year_cols:
            value = _coerce_float(row.get(col))
            series.append(0.0 if value is None else float(value))

        max_carbon = None
        if include_max_carbon:
            max_carbon = _coerce_float(row.get("MaxCarbon"))

        if key not in curves_by_key:
            curves_by_key[key] = {}
        if init_age not in curves_by_key[key]:
            curves_by_key[key][init_age] = CurveInfo(
                series=series, max_carbon=max_carbon
            )

    for key, info_by_age in curves_by_key.items():
        init_ages_by_key[key] = sorted(info_by_age.keys())

    _CURVE_TABLE_CACHE[cache_key] = (max_year, curves_by_key, init_ages_by_key)
    return _CURVE_TABLE_CACHE[cache_key]


def _match_curve_info(
    curves_by_key: Dict[CurveKey, Dict[int, CurveInfo]],
    init_ages_by_key: Dict[CurveKey, List[int]],
    key: CurveKey,
    age: int,
) -> Optional[Tuple[int, CurveInfo]]:
    init_age_bucket = _select_init_age_bucket(age, init_ages_by_key.get(key, []))
    if init_age_bucket is None:
        return None
    curve_info = curves_by_key.get(key, {}).get(init_age_bucket)
    if curve_info is None:
        return None
    return init_age_bucket, curve_info


def _should_use_cut_curve(
    segment_carbon: Optional[float],
    curve_info: CurveInfo,
    expected_curve_value: float,
) -> bool:
    if segment_carbon is None or curve_info.max_carbon is None:
        return False
    return float(segment_carbon) >= (2.0 / 3.0) * float(
        curve_info.max_carbon
    ) and float(segment_carbon) >= 3.0 * float(expected_curve_value)


def _switch_match_to_zero_curve(
    match: Optional[Tuple[int, CurveInfo]],
    curves_by_key: Dict[CurveKey, Dict[int, CurveInfo]],
    key: CurveKey,
    *,
    enabled: bool,
) -> Optional[Tuple[int, CurveInfo]]:
    if not enabled:
        return match

    zero_curve = curves_by_key.get(key, {}).get(0)
    if zero_curve is None:
        return match

    return (0, zero_curve)


def _switch_both_matches_to_zero_curve(
    biomass_match: Optional[Tuple[int, CurveInfo]],
    soil_match: Optional[Tuple[int, CurveInfo]],
    *,
    biomass_curves_by_key: Dict[CurveKey, Dict[int, CurveInfo]],
    soil_curves_by_key: Dict[CurveKey, Dict[int, CurveInfo]],
    key: CurveKey,
    enabled: bool,
) -> Tuple[Optional[Tuple[int, CurveInfo]], Optional[Tuple[int, CurveInfo]], bool]:
    if not enabled:
        return biomass_match, soil_match, False

    switched_biomass = _switch_match_to_zero_curve(
        biomass_match, biomass_curves_by_key, key, enabled=True
    )
    switched_soil = _switch_match_to_zero_curve(
        soil_match, soil_curves_by_key, key, enabled=True
    )

    if switched_biomass is None or switched_biomass[0] != 0:
        logger.warning(
            "Scenario-1 cut detection triggered but biomass InitAge=0 curve is missing for key=%s",
            key,
        )
        return biomass_match, soil_match, False

    if switched_soil is None or switched_soil[0] != 0:
        logger.warning(
            "Scenario-1 cut detection triggered but soil InitAge=0 curve is missing for key=%s",
            key,
        )
        return biomass_match, soil_match, False

    return switched_biomass, switched_soil, True


def _segment_carbon_to_total_co2(segment_area_ha: float, carbon_tcha: float) -> float:
    return float(segment_area_ha) * float(carbon_tcha) * c_to_co2


class CarbonCalculator:
    def __init__(
        self,
        data,
        sort_col="id",
        forestry_scenario: int = DEFAULT_FORESTRY_SCENARIO,
    ):
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

        scenario = validate_forestry_scenario(forestry_scenario)

        self.zone: gpd.GeoDataFrame = zone
        self.zone_raster = None
        self.forestry_scenario = scenario

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
            "metadata": {
                "timestamp": datetime.utcnow(),
                "forestry_scenario": self.forestry_scenario,
            },
        }

    async def calculate(self) -> CalculationResult:
        bm_curves_df = get_bm_curve_df()
        soil_curves_df = get_soil_curve_df()
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
                + [
                    alias
                    for aliases in LANDUSE_ALIAS_COLS.values()
                    for alias in aliases
                ]
            )
        )

        built_pcts: List[float] = []
        new_open_pcts: List[float] = []
        new_tree_pcts: List[float] = []
        existing_pcts: List[float] = []
        soil_change_new_veg_pcts: List[float] = []

        for _, row in self.zone.iterrows():
            if has_any_landuse_cols:
                built = _get_pct_value(
                    row, LANDUSE_BUILT_COL, LANDUSE_ALIAS_COLS[LANDUSE_BUILT_COL]
                )
                new_open = _get_pct_value(
                    row,
                    LANDUSE_NEW_OPEN_VEG_COL,
                    LANDUSE_ALIAS_COLS[LANDUSE_NEW_OPEN_VEG_COL],
                )
                new_tree = _get_pct_value(
                    row,
                    LANDUSE_NEW_TREE_VEG_COL,
                    LANDUSE_ALIAS_COLS[LANDUSE_NEW_TREE_VEG_COL],
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

        soil_segment_sum_rows = (
            await fetch_weighted_raster_sum_ha_by_segment_for_regions(
                "hiilikartta_maaperanhiili_2023_tcha",
                wkt_list,
                crs,
                simplify_calcs=self.simplify_calcs,
            )
        )
        soil_segment_sum_by_order: Dict[int, Dict[int, float]] = {
            order_num: {} for order_num in range(1, area_count + 1)
        }
        for order_num, segment_id, sum_weighted_ha in soil_segment_sum_rows:
            soil_segment_sum_by_order[int(order_num)][int(segment_id)] = float(
                sum_weighted_ha or 0.0
            )

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
        years_int = _build_reporting_years(current_year)

        existing_fracs = [pct / 100.0 for pct in existing_pcts]
        new_open_fracs = [pct / 100.0 for pct in new_open_pcts]
        new_tree_fracs = [pct / 100.0 for pct in new_tree_pcts]
        new_veg_fracs = [
            (new_open_pcts[i] + new_tree_pcts[i]) / 100.0 for i in range(area_count)
        ]
        soil_retention_fracs = [
            1.0 - (soil_change_new_veg_pcts[i] / 100.0) for i in range(area_count)
        ]

        veg_open_coeffs: List[float] = []
        veg_tree_coeffs: List[float] = []
        soil_open_coeffs: List[float] = []
        soil_tree_coeffs: List[float] = []
        zoning_codes: List[str] = []

        for idx in range(area_count):
            maakunta = maakunta_codes[idx]
            code = str(self.zone.iloc[idx][zoning_col]).strip()
            zoning_codes.append(code)
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
                    k_veg_open = _coerce_float(
                        coeff_row.get(SEQUESTRATION_COL_VEG_OPEN)
                    )
                    k_veg_tree = _coerce_float(
                        coeff_row.get(SEQUESTRATION_COL_VEG_TREE)
                    )
                    k_soil_open = _coerce_float(
                        coeff_row.get(SEQUESTRATION_COL_SOIL_OPEN)
                    )
                    k_soil_tree = _coerce_float(
                        coeff_row.get(SEQUESTRATION_COL_SOIL_TREE)
                    )

                    # Source values are t_C/ha/yr; downstream math expects tCO2/ha/yr.
                    k_veg_open = (
                        0.0 if k_veg_open is None else float(k_veg_open) * c_to_co2
                    )
                    k_veg_tree = (
                        0.0 if k_veg_tree is None else float(k_veg_tree) * c_to_co2
                    )
                    k_soil_open = (
                        0.0 if k_soil_open is None else float(k_soil_open) * c_to_co2
                    )
                    k_soil_tree = (
                        0.0 if k_soil_tree is None else float(k_soil_tree) * c_to_co2
                    )

            veg_open_coeffs.append(k_veg_open)
            veg_tree_coeffs.append(k_veg_tree)
            soil_open_coeffs.append(k_soil_open)
            soil_tree_coeffs.append(k_soil_tree)

        veg_changed_rate_co2_per_year: List[float] = []
        soil_changed_rate_co2_per_year: List[float] = []
        for idx in range(area_count):
            area_ha = float(calcs_df.iloc[idx]["area_ha"] or 0.0)
            veg_tree_share = new_tree_fracs[idx]
            if zoning_codes[idx] in POWERLINE_ZONING_CODES:
                veg_tree_share = 0.0
            veg_rate = area_ha * (
                new_open_fracs[idx] * veg_open_coeffs[idx]
                + veg_tree_share * veg_tree_coeffs[idx]
            )
            soil_rate = area_ha * (
                new_open_fracs[idx] * soil_open_coeffs[idx]
                + new_tree_fracs[idx] * soil_tree_coeffs[idx]
            )
            veg_changed_rate_co2_per_year.append(float(veg_rate))
            soil_changed_rate_co2_per_year.append(float(soil_rate))

        variables_base_year = 2021
        soil_raster_base_year = 2023
        _, biomass_curves_by_key, biomass_init_ages_by_key = _build_curve_tables(
            bm_curves_df,
            cache_name="biomass",
            include_max_carbon=True,
        )
        _, soil_curves_by_key, soil_init_ages_by_key = _build_curve_tables(
            soil_curves_df,
            cache_name="soil",
            include_max_carbon=False,
        )

        veg_existing_total_co2_by_order: Dict[int, Dict[int, float]] = {
            order_num: {year: 0.0 for year in years_int}
            for order_num in range(1, area_count + 1)
        }
        soil_existing_total_co2_by_order: Dict[int, Dict[int, float]] = {
            order_num: {year: 0.0 for year in years_int}
            for order_num in range(1, area_count + 1)
        }
        soil_base_2023_co2_by_order: Dict[int, float] = {
            order_num: 0.0 for order_num in range(1, area_count + 1)
        }

        veg_powerline_tree_delta_co2_by_order: Dict[int, Dict[int, float]] = {
            order_num: {year: 0.0 for year in years_int}
            for order_num in range(1, area_count + 1)
        }

        zoning_codes_by_order: Dict[int, str] = {
            order_num: str(self.zone.iloc[order_num - 1][zoning_col]).strip()
            for order_num in range(1, area_count + 1)
        }
        is_powerline_zone_by_order: Dict[int, bool] = {
            order_num: zoning_codes_by_order[order_num] in POWERLINE_ZONING_CODES
            for order_num in range(1, area_count + 1)
        }

        for order_num, segment_areas in segment_areas_by_order.items():
            for segment_id, segment_area_ha in segment_areas.items():
                variables = variables_dict.get(segment_id)
                if not variables:
                    continue

                age_base = _get_int_var(variables, ["Age", "age"])
                if age_base is None:
                    continue

                curve_key = _get_curve_key(variables, self.forestry_scenario)
                if curve_key is None:
                    continue

                biomass_match = _match_curve_info(
                    biomass_curves_by_key, biomass_init_ages_by_key, curve_key, age_base
                )
                soil_match = _match_curve_info(
                    soil_curves_by_key, soil_init_ages_by_key, curve_key, age_base
                )
                use_cut_zero_curve = False
                cut_curve_applied = False
                if biomass_match is not None:
                    selected_init_age, curve_info = biomass_match

                    segment_carbon = _get_float_var(variables, ["Carbon", "carbon"])

                    if self.forestry_scenario == 1 and segment_carbon is not None:
                        expected_curve_value = _curve_value_at_offset(
                            curve_info.series,
                            _relative_curve_offset(
                                age_base,
                                selected_init_age,
                                variables_base_year,
                                variables_base_year,
                            ),
                        )
                        use_cut_zero_curve = _should_use_cut_curve(
                            segment_carbon,
                            curve_info,
                            expected_curve_value,
                        )
                    biomass_match, soil_match, cut_curve_applied = (
                        _switch_both_matches_to_zero_curve(
                            biomass_match,
                            soil_match,
                            biomass_curves_by_key=biomass_curves_by_key,
                            soil_curves_by_key=soil_curves_by_key,
                            key=curve_key,
                            enabled=use_cut_zero_curve,
                        )
                    )
                    selected_init_age, curve_info = biomass_match
                    biomass_resets_to_year0 = cut_curve_applied

                    if segment_carbon is not None:
                        biomass_curve_base = _curve_value_at_offset(
                            curve_info.series,
                            _relative_curve_offset(
                                age_base,
                                selected_init_age,
                                variables_base_year,
                                variables_base_year,
                                reset_to_year0=biomass_resets_to_year0,
                            ),
                        )
                        for year in years_int:
                            year_offset = _relative_curve_offset(
                                age_base,
                                selected_init_age,
                                year,
                                variables_base_year,
                                reset_to_year0=biomass_resets_to_year0,
                            )
                            biomass_curve_year = _curve_value_at_offset(
                                curve_info.series, year_offset
                            )
                            biomass_scale = _curve_scale_factor(
                                biomass_curve_base, biomass_curve_year
                            )
                            veg_existing_total_co2_by_order[order_num][
                                year
                            ] += _segment_carbon_to_total_co2(
                                segment_area_ha,
                                segment_carbon * biomass_scale,
                            )

                if soil_match is not None:
                    soil_init_age, soil_curve_info = soil_match
                    soil_resets_to_year0 = bool(
                        cut_curve_applied and soil_init_age == 0
                    )
                    soil_segment_sum = soil_segment_sum_by_order.get(order_num, {}).get(
                        segment_id
                    )
                    if soil_segment_sum is not None and float(segment_area_ha) > 0:
                        soil_carbon_2023_tcha = float(soil_segment_sum) / float(
                            segment_area_ha
                        )
                        soil_base_2023_co2_by_order[
                            order_num
                        ] += _segment_carbon_to_total_co2(
                            segment_area_ha,
                            soil_carbon_2023_tcha,
                        )
                        soil_curve_base = _curve_value_at_offset(
                            soil_curve_info.series,
                            _relative_curve_offset(
                                age_base,
                                soil_init_age,
                                soil_raster_base_year,
                                variables_base_year,
                                reset_to_year0=soil_resets_to_year0,
                            ),
                        )
                        for year in years_int:
                            year_offset = _relative_curve_offset(
                                age_base,
                                soil_init_age,
                                year,
                                variables_base_year,
                                reset_to_year0=soil_resets_to_year0,
                            )
                            soil_curve_year = _curve_value_at_offset(
                                soil_curve_info.series, year_offset
                            )
                            soil_scale = _curve_scale_factor(
                                soil_curve_base, soil_curve_year
                            )
                            soil_existing_total_co2_by_order[order_num][
                                year
                            ] += _segment_carbon_to_total_co2(
                                segment_area_ha,
                                soil_carbon_2023_tcha * soil_scale,
                            )

                if not is_powerline_zone_by_order.get(order_num, False):
                    continue

                powerline_curve_key = _get_curve_key(
                    variables,
                    self.forestry_scenario,
                    maingroup_override=POWERLINE_BIOMASS_MAINGROUP,
                )
                if powerline_curve_key is None:
                    continue

                powerline_match = _match_curve_info(
                    biomass_curves_by_key,
                    biomass_init_ages_by_key,
                    powerline_curve_key,
                    age_base,
                )
                if powerline_match is None:
                    continue

                _, powerline_curve_info = powerline_match
                base_tree_value = _curve_value_at_offset(powerline_curve_info.series, 0)
                for year in years_int:
                    age_since_plan = max(0, int(year) - int(current_year))
                    tree_value_year = _curve_value_at_offset(
                        powerline_curve_info.series, age_since_plan
                    )
                    tree_delta_per_ha = tree_value_year - base_tree_value
                    veg_powerline_tree_delta_co2_by_order[order_num][year] += (
                        float(segment_area_ha) * float(tree_delta_per_ha) * c_to_co2
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
                veg_nochange = veg_existing_total_co2_by_order.get(order_num, {}).get(
                    year, 0.0
                )
                soil_nochange = soil_existing_total_co2_by_order.get(order_num, {}).get(
                    year, 0.0
                )
                powerline_tree_delta = veg_powerline_tree_delta_co2_by_order.get(
                    order_num, {}
                ).get(year, 0.0)

                veg_nochange_vals.append(veg_nochange)
                soil_nochange_vals.append(soil_nochange)

                if int(year) == int(current_year):
                    veg_planned_vals.append(veg_nochange)
                    soil_planned_vals.append(soil_nochange)
                    continue

                veg_planned = (
                    existing_fracs[idx] * veg_nochange
                    + veg_changed_rate_co2_per_year[idx] * delta_years
                )
                if is_powerline_zone_by_order.get(order_num, False):
                    veg_planned += new_tree_fracs[idx] * powerline_tree_delta
                veg_planned_vals.append(veg_planned)
                soil_planned_vals.append(
                    existing_fracs[idx] * soil_nochange
                    + new_veg_fracs[idx]
                    * soil_retention_fracs[idx]
                    * soil_base_2023_co2_by_order[order_num]
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
            "metadata": {
                **totals_data["metadata"],
                "forestry_scenario": self.forestry_scenario,
            },
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
# CarbonCalculator().calculate("data/legacy/vantaa_yk.shp")
