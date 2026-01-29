import pandas as pd

data_path = "data"
bm_curve_df = None
soil_curve_df = None
area_multipliers_df = None
landuse_sequestration_df = None


def load_bm_curves():
    global bm_curve_df
    bm_curve_df = pd.read_csv(f"{data_path}/BiomassCurves.txt")


def load_soil_curves():
    global soil_curve_df
    soil_curve_df = pd.read_csv(f"{data_path}/SoilCurves.txt")


def load_area_multipliers():
    global area_multipliers_df
    area_multipliers_df = pd.read_csv(
        f"{data_path}/aluekertoimet.csv", index_col="Lyhenne"
    )


def load_landuse_sequestration():
    global landuse_sequestration_df
    landuse_sequestration_df = pd.read_csv(
        f"{data_path}/Hiilikartta_Kasvillisuuden_ja_maaperan_hiilensidonta_kayttotarkoitusluokittain.csv",
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
