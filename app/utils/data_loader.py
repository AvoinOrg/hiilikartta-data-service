from pathlib import Path
from typing import Iterable

import pandas as pd

from app.utils.logger import get_logger

logger = get_logger(__name__)

data_path = Path("data")

# Bump this when a new dump of the Hiilikartta curve / coefficient tables is
# dropped into `data/`. All dated filenames are derived from this single value.
HIILIKARTTA_DATA_VERSION = "20260415"

DEFAULT_FORESTRY_SCENARIO = 1
BIOMASS_CURVE_FILE = data_path / f"Hiilikartta_Veg_{HIILIKARTTA_DATA_VERSION}.csv"
SOIL_CURVE_FILE = data_path / f"Hiilikartta_Soil_{HIILIKARTTA_DATA_VERSION}.csv"
LANDUSE_SEQUESTRATION_FILE = (
    data_path
    / f"Hiilikartta_Kasvillisuuden_ja_maaperan_hiilensidonta_kayttotarkoitusluokittain_{HIILIKARTTA_DATA_VERSION}.csv"
)
CURVE_KEY_COLUMNS = [
    "Scen",
    "Region",
    "Maingroup",
    "Soiltype",
    "Drainage",
    "Fertility",
    "Species",
    "InitAge",
]

bm_curve_df: pd.DataFrame | None = None
soil_curve_df: pd.DataFrame | None = None
area_multipliers_df = None
landuse_sequestration_df = None


def _load_curve_file(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path, sep=";", decimal=",", encoding="utf-8-sig")
    df.columns = [str(col).strip() for col in df.columns]
    df = df.drop_duplicates().copy()

    missing_cols = [col for col in CURVE_KEY_COLUMNS if col not in df.columns]
    if missing_cols:
        raise ValueError(f"{path} is missing required columns: {missing_cols}")

    duplicate_mask = df.duplicated(subset=CURVE_KEY_COLUMNS, keep=False)
    if duplicate_mask.any():
        sample_keys = (
            df.loc[duplicate_mask, CURVE_KEY_COLUMNS]
            .drop_duplicates()
            .head(5)
            .to_dict("records")
        )
        logger.warning(
            f"{path} contains duplicate curve rows for keys (keeping first): {sample_keys}"
        )
        df = df.drop_duplicates(subset=CURVE_KEY_COLUMNS, keep="first")

    return df


def _curve_scenarios(df: pd.DataFrame) -> tuple[int, ...]:
    return tuple(sorted(df["Scen"].dropna().astype(int).unique().tolist()))


def _scenario_label(scenarios: Iterable[int]) -> str:
    return "(" + ", ".join(str(item) for item in scenarios) + ")"


def validate_forestry_scenario(forestry_scenario: int) -> int:
    scenario = int(forestry_scenario)
    valid_scenarios = get_available_forestry_scenarios()
    if scenario not in valid_scenarios:
        raise ValueError(
            "forestry_scenario must be one of "
            f"{_scenario_label(valid_scenarios)}, got {scenario}"
        )
    return scenario


def load_bm_curves() -> None:
    global bm_curve_df
    bm_curve_df = _load_curve_file(BIOMASS_CURVE_FILE)


def load_soil_curves() -> None:
    global soil_curve_df
    soil_curve_df = _load_curve_file(SOIL_CURVE_FILE)


def load_area_multipliers():
    global area_multipliers_df
    area_multipliers_df = pd.read_csv(
        f"{data_path}/aluekertoimet.csv", index_col="Lyhenne"
    )


def load_landuse_sequestration():
    global landuse_sequestration_df
    landuse_sequestration_df = pd.read_csv(
        LANDUSE_SEQUESTRATION_FILE,
        sep=";",
        encoding="utf-8-sig",
    )
    landuse_sequestration_df["Maakunta"] = landuse_sequestration_df["Maakunta"].astype(
        int
    )
    landuse_sequestration_df["Lyhenne"] = landuse_sequestration_df["Lyhenne"].astype(
        str
    )
    landuse_sequestration_df.set_index(["Maakunta", "Lyhenne"], inplace=True)


def get_area_multipliers_df() -> pd.DataFrame:
    if (area_multipliers_df is None) or (len(area_multipliers_df) == 0):
        load_area_multipliers()
    return area_multipliers_df


def get_bm_curve_df() -> pd.DataFrame:
    if (bm_curve_df is None) or (len(bm_curve_df) == 0):
        load_bm_curves()
    return bm_curve_df


def get_soil_curve_df() -> pd.DataFrame:
    if (soil_curve_df is None) or (len(soil_curve_df) == 0):
        load_soil_curves()
    return soil_curve_df


def get_available_forestry_scenarios() -> tuple[int, ...]:
    biomass_scenarios = _curve_scenarios(get_bm_curve_df())
    soil_scenarios = _curve_scenarios(get_soil_curve_df())
    if biomass_scenarios != soil_scenarios:
        raise ValueError(
            "Biomass and soil curve files expose different Scen values: "
            f"biomass={_scenario_label(biomass_scenarios)}, "
            f"soil={_scenario_label(soil_scenarios)}"
        )
    return biomass_scenarios


def get_landuse_sequestration_df() -> pd.DataFrame:
    if (landuse_sequestration_df is None) or (len(landuse_sequestration_df) == 0):
        load_landuse_sequestration()
    return landuse_sequestration_df


def unload_files():
    global bm_curve_df
    global soil_curve_df
    global area_multipliers_df
    global landuse_sequestration_df
    bm_curve_df = None
    soil_curve_df = None
    area_multipliers_df = None
    landuse_sequestration_df = None
