import pandas as pd

from app.calculator.calculator import (
    CurveInfo,
    _build_curve_tables,
    _curve_value_at_offset,
    _match_curve_info,
    _relative_curve_offset,
    _select_init_age_bucket,
    _should_use_cut_curve,
    _switch_match_to_zero_curve,
)


def _curve_rows(include_max_carbon: bool = True) -> pd.DataFrame:
    rows = [
        {
            "Scen": 1,
            "Region": 1,
            "Maingroup": 2,
            "Soiltype": 3,
            "Drainage": 4,
            "Fertility": 5,
            "Species": 6,
            "InitAge": 50,
            "year0": 10.0,
            "year1": 12.0,
            "year2": 14.0,
            "year3": 16.0,
        },
        {
            "Scen": 1,
            "Region": 1,
            "Maingroup": 2,
            "Soiltype": 3,
            "Drainage": 4,
            "Fertility": 5,
            "Species": 6,
            "InitAge": 85,
            "year0": 20.0,
            "year1": 21.0,
            "year2": 22.0,
            "year3": 23.0,
        },
    ]
    if include_max_carbon:
        rows[0]["MaxCarbon"] = 90.0
        rows[1]["MaxCarbon"] = 120.0
    return pd.DataFrame(rows)


def test_select_init_age_bucket_uses_largest_not_exceeding_age():
    init_ages = [0, 30, 50, 85]

    assert _select_init_age_bucket(84, init_ages) == 50
    assert _select_init_age_bucket(85, init_ages) == 85
    assert _select_init_age_bucket(-1, init_ages) == 0
    assert _select_init_age_bucket(120, init_ages) == 85


def test_curve_value_at_offset_clamps_series_bounds():
    series = [10.0, 12.0, 14.0]

    assert _curve_value_at_offset(series, -5) == 10.0
    assert _curve_value_at_offset(series, 0) == 10.0
    assert _curve_value_at_offset(series, 1) == 12.0
    assert _curve_value_at_offset(series, 99) == 14.0


def test_relative_curve_offset_uses_selected_init_age():
    assert _relative_curve_offset(84, 50, 2021, 2021) == 34
    assert _relative_curve_offset(84, 50, 2025, 2021) == 38
    assert _relative_curve_offset(85, 85, 2021, 2021) == 0


def test_match_curve_info_uses_init_age_bucket():
    df = _curve_rows(include_max_carbon=True)
    _, curves_by_key, init_ages_by_key = _build_curve_tables(
        df, cache_name="test-biomass", include_max_carbon=True
    )
    key = (1, 1, 2, 3, 4, 5, 6)

    match_84 = _match_curve_info(curves_by_key, init_ages_by_key, key, 84)
    match_85 = _match_curve_info(curves_by_key, init_ages_by_key, key, 85)

    assert match_84 is not None
    assert match_84[0] == 50
    assert match_84[1].series[0] == 10.0

    assert match_85 is not None
    assert match_85[0] == 85
    assert match_85[1].series[0] == 20.0


def test_should_use_cut_curve_requires_both_thresholds():
    curve_info = CurveInfo(series=[10.0, 11.0], max_carbon=90.0)

    assert _should_use_cut_curve(70.0, curve_info, expected_curve_value=20.0)
    assert not _should_use_cut_curve(65.0, curve_info, expected_curve_value=30.0)
    assert not _should_use_cut_curve(50.0, curve_info, expected_curve_value=10.0)


def test_switch_match_to_zero_curve_uses_zero_curve_when_enabled():
    key = (1, 1, 2, 3, 4, 5, 6)
    original_match = (50, CurveInfo(series=[10.0, 11.0], max_carbon=90.0))
    zero_curve = CurveInfo(series=[1.0, 2.0], max_carbon=90.0)
    curves_by_key = {key: {0: zero_curve, 50: original_match[1]}}

    switched = _switch_match_to_zero_curve(
        original_match,
        curves_by_key,
        key,
        enabled=True,
    )

    assert switched == (0, zero_curve)


def test_switch_match_to_zero_curve_can_create_match_for_soil():
    key = (1, 1, 2, 3, 4, 5, 6)
    zero_curve = CurveInfo(series=[3.0, 4.0], max_carbon=None)
    curves_by_key = {key: {0: zero_curve}}

    switched = _switch_match_to_zero_curve(
        None,
        curves_by_key,
        key,
        enabled=True,
    )

    assert switched == (0, zero_curve)


def test_soil_curve_matching_uses_same_init_age_logic_without_cut_logic():
    df = _curve_rows(include_max_carbon=False)
    _, curves_by_key, init_ages_by_key = _build_curve_tables(
        df, cache_name="test-soil", include_max_carbon=False
    )
    key = (1, 1, 2, 3, 4, 5, 6)

    soil_match = _match_curve_info(curves_by_key, init_ages_by_key, key, 84)

    assert soil_match is not None
    assert soil_match[0] == 50
    assert soil_match[1].max_carbon is None
