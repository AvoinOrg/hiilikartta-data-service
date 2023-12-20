import datetime
from shapely.geometry import box
import numpy as np
import pandas as pd
import rioxarray as rxr
import rasterio as rio
import xarray as xr
from typing import List, Optional, Tuple
from numpy.typing import NDArray

variables_base_year = 2021
current_year = datetime.datetime.now().year
year_offset = current_year - variables_base_year
keys_to_use = set(
    [
        "Region",
        "Maingroup",
        "Soiltype",
        "Drainage",
        "Fertility",
        "Species",
        "Structure",
        "Regime",
    ]
)
biomass_to_carbon_multiplier = 0.5


async def get_bm_curve_values_for_years_mabp(
    rasts: List[xr.DataArray],
    years: List[str],
    bm_curve_df: pd.DataFrame,
    variables_dict: dict[int, dict[str, int]],
    rast_overlap_masks: Optional[List[xr.DataArray]] = None,
) -> Tuple[List[Optional[dict[str, float]]], List[Optional[NDArray[np.bool_]]]]:
    masks: List[Optional[NDArray[np.bool_] or None]] = []
    vals: List[Optional[dict[str, float]]] = []
    year_dict_base: dict[str, float] = {year: 0 for year in years}

    for idx, rast in enumerate(rasts):
        mask = ~np.isnan(rast.values)  # Mask for non-NaN values
        year_dict = year_dict_base.copy()
        was_found = False
        try:
            if np.any(mask):
                for (x, y), value in np.ndenumerate(rast.values):
                    if mask[x, y]:
                        variables = variables_dict.get(int(value))
                        overlap_multiplier = 1
                        if rast_overlap_masks is not None:
                            overlap_multiplier = rast_overlap_masks[idx][x][y].values.item()

                        if variables is not None:
                            filtered_variables = {k: variables[k] for k in keys_to_use}
                            condition = pd.Series([True] * len(bm_curve_df))
                            for key, value in filtered_variables.items():
                                condition = condition & (bm_curve_df[key] == value)

                            matching_row = bm_curve_df[condition]
                            # TODO: add a shortlist of matching rows to speed up the search

                            if len(matching_row) > 0:
                                was_found = True
                                for year in years:
                                    year_diff = int(year) - current_year + year_offset
                                    mabp = float(matching_row.iloc[0]["Mabp"])
                                    year_dict[year] += (
                                        overlap_multiplier * mabp * year_diff
                                    )
            else:
                print("No non-NaN values to iterate over.")
        except Exception as e:
            print(e)
            continue

        if was_found and (np.sum(list(year_dict.values())) > 0):
            vals.append(year_dict)
            masks.append(mask)
        else:
            vals.append(None)
            masks.append(None)

    return vals, masks


def create_pixel_box(affine_transform, row, col, width, height):
    """
    Create a box (rectangle) for the pixel at (row, col) with given width and height.
    """
    # Transform pixel coordinates to geographical coordinates
    geo_x, geo_y = rio.transform.xy(affine_transform, row, col, offset="center")
    return box(
        geo_x - width / 2, geo_y - height / 2, geo_x + width / 2, geo_y + height / 2
    )


def get_overlap_mask(data_array: xr.DataArray, geometry):
    # Assuming rast is a rasterio dataset
    affine_transform = rio.transform.from_bounds(
        *data_array.rio.bounds(), data_array.rio.width, data_array.rio.height
    )
    pixel_width = affine_transform.a  # Cell width
    pixel_height = (
        -affine_transform.e
    )  # Cell height (negative due to north-up convention)

    overlap_percentages = xr.DataArray(
        np.zeros_like(data_array.values),
        dims=data_array.dims,
        # coords={dim: data_array.coords[dim] for dim in data_array.dims},
        coords=data_array.coords,
    )

    for row in range(data_array.rio.height):
        for col in range(data_array.rio.width):
            # Check if the value is not NaN
            pixel_value = data_array.values[row, col]
            if not np.isnan(pixel_value):
                # Create a box for the pixel
                try:
                    pixel_box = create_pixel_box(
                        affine_transform, row, col, pixel_width, pixel_height
                    )
                    intersection = pixel_box.intersection(geometry)

                    if not intersection.is_empty:
                        # Calculate the percentage overlap
                        overlap_percentage = intersection.area / pixel_box.area
                    else:
                        overlap_percentage = 0
                except Exception as e:
                    print(f"An error occurred during intersection: {e}")
                    overlap_percentage = 0

                overlap_percentages[row, col] = overlap_percentage

    return overlap_percentages
