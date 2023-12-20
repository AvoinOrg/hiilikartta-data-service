import pandas as pd

data_path = "data"
bm_curve_df = None
area_multipliers_df = None


def load_bm_curves():
    global bm_curve_df
    bm_curve_df = pd.read_csv(f"{data_path}/BiomassCurves.txt")


def load_area_multipliers():
    global area_multipliers_df
    area_multipliers_df = pd.read_csv(
        f"{data_path}/area_multipliers.csv", index_col="zone_id"
    )


def get_area_multipliers_df() -> pd.DataFrame:
    if (area_multipliers_df is None) or (len(area_multipliers_df) == 0):
        load_area_multipliers()
    return area_multipliers_df


def get_bm_curve_df() -> pd.DataFrame:
    if (bm_curve_df is None) or (len(bm_curve_df) == 0):
        load_bm_curves()
    return bm_curve_df


def unload_files():
    global bm_curve_df
    global area_multipliers_df
    bm_curve_df = None
    area_multipliers_df = None
