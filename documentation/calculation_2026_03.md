# Carbon Calculation Specification – 2026-03

Previous snapshots:

- `calculation_2025.md`: 2025 implementation snapshot
- `calculation_2024.md`: legacy pre-2025 model

This document describes the latest implemented 2026-03 calculation model for a single area reservation (one polygon feature). It supersedes the earlier 2026 draft that described separate biomass and soil files per scenario.

The final curve tables and the regional changed-land coefficients are dated. The calculation refers to them by generic name plus a `<date>` suffix so the document does not have to be rewritten when a newer dump lands:

- `data/Hiilikartta_Veg_<date>.csv`
- `data/Hiilikartta_Soil_<date>.csv`
- `data/Hiilikartta_Kasvillisuuden_ja_maaperan_hiilensidonta_kayttotarkoitusluokittain_<date>.csv`

At the time of writing the effective `<date>` is `20260415`.

Terminology:

- `nochange` = `Ilman kaavaa`
- `planned` = `Kaava`

## What Changed From 2025

1. Biomass curves are now stored in a single final table: `data/Hiilikartta_Veg_<date>.csv`.
2. Soil curves are now stored in a single final table: `data/Hiilikartta_Soil_<date>.csv`.
3. The scenario is no longer derived from the filename. It is matched from the `Scen` column inside those tables.
4. Curve selection no longer uses `Structure`, `Regime`, or `Rotation`. The lookup key is now `(Scen, Region, Maingroup, Soiltype, Drainage, Fertility, Species, InitAge)`.
5. Curve rows are selected by `InitAge` bucket using the segment `Age`. The chosen bucket is the largest available `InitAge` that does not exceed the segment age. The starting position on the selected row is `year(Age - InitAge)` — for example, `Age = 47` uses the `InitAge = 30` row starting at `year17`, and `Age = 53` uses the `InitAge = 50` row starting at `year3`.
6. Every forestry scenario uses the same cut-reset rule. The rule fires on a segment when both `segment_carbon >= (2/3) * MaxCarbon` and `segment_carbon >= 3 * curve_value_at_current_position`. When it fires, both the biomass curve and the soil curve jump to the `InitAge = 0` row of the same categorical key at `year0`. Otherwise the initially matched curve is kept.
7. `MaxCarbon` is taken from the `Hiilikartta_Veg_<date>.csv` row for the forestry scenario used for cut detection (in current data that is scenario `1`). For every other scenario, `MaxCarbon` is treated as `999`, which effectively disables the cut reset for those scenarios.
8. Powerline-specific biomass rows are now available in the same final tables via `Maingroup = 4`.
9. Biomass and soil curves are used as relative curves. The percentage change between the base-year curve value and the reporting-year curve value is applied to the actual carbon stock of the segment.
10. The changed-land coefficient table was renamed: column `Luokka_jarjnro` became `Luokka_jarjestys`, and the four sequestration columns are now stored in `t_C` instead of `t_CO2` (they are converted to `tCO2` with the usual `44/12` factor). The per-row lookup key `(Maakunta, Lyhenne)` is unchanged and is still resolved from the plan's polygon region.
11. The reporting horizon now ends at 2080. `planned` and `nochange` return exactly the same values for `current_year`, and differences begin at the first milestone year, which is 2030.

## Inputs

### Request-level inputs

`POST /calculation` accepts:

- `id`
- `visible_id`
- `name`
- `forestry_scenario` (optional integer scenario id, default `1`)
- `file` (`multipart/form-data`, zipped vector dataset)

The selected forestry scenario is stored on the plan row and reused by the worker.

Scenario meanings:

- `1`: default forestry scenario with cut-detection fallback
- `2`: forestry scenario 2
- `3`: forestry scenario 3

The current final curve tables expose `Scen` values `1`, `2`, and `3`.

### Feature-level inputs

Each input feature must include:

- `geometry`: polygon or multipolygon (input assumed EPSG:4326, reprojected to EPSG:3067 for area math)
- `zoning_code`: land-use code used for planned-scenario sequestration coefficients

Optional feature properties:

- `landuse_built`
- `landuse_new_open_vegetation`
- `landuse_new_tree_vegetation`
- `landuse_existing`
- Finnish aliases for the four land-use fields
- `soil_change_new_vegetation_pct`
- Finnish aliases for the soil-change field

If none of the land-use share fields are provided, the implementation defaults to:

- `landuse_built = 0`
- `landuse_new_open_vegetation = 0`
- `landuse_new_tree_vegetation = 0`
- `landuse_existing = 100`

The land-use shares must sum to 100, allowing only a small normalization drift.

## GIS and File Inputs

### Raster datasets

- Vegetation carbon raster: `hiilikartta_kasvillisuudenhiili_2021_tcha`
- Soil carbon raster: `hiilikartta_maaperanhiili_2023_tcha`

The vegetation raster is a 2021 GIS source dataset, but the latest biomass calculation does not use it directly for stock scaling or cut detection. Biomass actual stock comes from the segment variables table instead.

Both rasters and the segment `Carbon` column store values in `tC/ha`. All carbon stocks are converted to `tCO2` using:

`c_to_co2 = 44 / 12`

Source years:

- vegetation raster dataset year: `2021`
- soil raster stock year: `2023`

### Segment data

- Segment-id raster: `luke_mvmisegmentit_id_kokomaa`
- Segment variables table: `luke_mvmisegmentit_muuttujat_kokomaa`

The segment variables table provides the current forest attributes, including:

- `Age`, used for `InitAge` bucket selection
- `Carbon` (`tC/ha`), used in scenario-1 cut detection and as the actual biomass carbon stock for percentage scaling (converted to `tCO2` like all other carbon stocks)

Source year:

- segment variables year: `2021`

### Region enrichment

- Region table: `maakunta`
- Geometry column: `geom`
- Region code column: `natcode`

For each polygon, the calculation selects the `maakunta` row with the largest intersection area.

### Curve files

- `data/Hiilikartta_Veg_<date>.csv`
- `data/Hiilikartta_Soil_<date>.csv`

Both files are delivered as semicolon-separated CSV. Column layout:

- `Scen, Region, Maingroup, Soiltype, Drainage, Fertility, Species, InitAge, MaxCarbon, year0..year85` for the biomass file
- `Scen, Region, Maingroup, Soiltype, Drainage, Fertility, Species, InitAge, year0..year85` for the soil file (no `MaxCarbon` column)

The vegetation and soil curve tables are treated as relative curves indexed by `InitAge` and `yearN`. The table publication date is not used directly in the calculation; it only identifies the data version.

Changed-land annual coefficients:

- `data/Hiilikartta_Kasvillisuuden_ja_maaperan_hiilensidonta_kayttotarkoitusluokittain_<date>.csv`

Column layout:

- `Maakunta, Luokka_jarjestys, Luokka, Lyhenne, Uusi_avoin_kasvillisuus_kasvillisuuden_hiilensidonta_t_C, Uusi_puustoinen_kasvillisuus_kasvillisuuden_hiilensidonta_t_C, Uusi_avoin_kasvillisuus_maaperan_hiilensidonta_t_C, Uusi_puustoinen_kasvillisuus_maaperan_hiilensidonta_t_C`

The four sequestration columns are annual rates in `t_C/ha/yr` and are converted to `tCO2/ha/yr` with `c_to_co2 = 44 / 12` before they are applied.

## Curve Matching Rules

### Biomass and soil curve key

Rows are matched using:

- `Scen`
- `Region`
- `Maingroup`
- `Soiltype`
- `Drainage`
- `Fertility`
- `Species`

For a matched categorical key, the calculation then chooses the `InitAge` bucket:

- select the largest available `InitAge` that is less than or equal to the segment `Age`
- if the segment age is smaller than every bucket, use the smallest bucket
- if the segment age is larger than every bucket, use the largest bucket

The starting position on the selected row is `year(Age - InitAge)`.

Examples with the current files:

- `Age = 47` uses the `InitAge = 30` row and starts at `year17`
- `Age = 53` uses the `InitAge = 50` row and starts at `year3`
- `Age = 84` uses the `InitAge = 50` row and starts at `year34`
- `Age = 85` uses the `InitAge = 85` row and starts at `year0`

### Relative-time interpretation and percentage scaling

The selected curve row is treated as a relative-time series. The segment is placed on the row at the starting column that corresponds to its current age, and reporting years then advance column by column from that start.

Let:

- `age_base` = segment `Age` from segment variables, interpreted as age in 2021
- `init_age` = selected `InitAge`
- `offset(year) = age_base + (year - 2021) - init_age`
- if the forestry cut-reset rule fires (see below), both the biomass and soil curves switch to the `InitAge = 0` row of the same categorical key and the starting column becomes `year0` at the 2021 segment-data year. Offsets then become `offset(year) = year - 2021`.

Biomass uses the segment-table carbon value from 2021 as the actual stock.

- biomass curve base value at 2021 is `curve(offset(2021))`
- biomass curve value at reporting year `year` is `curve(offset(year))`
- biomass scale factor is `curve(offset(year)) / curve(offset(2021))`
- actual biomass carbon stock at reporting year `year` is:

  `segment_carbon_2021 * (curve(offset(year)) / curve(offset(2021)))`

Soil uses the actual soil raster stock from 2023 as the actual stock.

- soil curve base value at 2023 is `curve(age_base + (2023 - 2021) - init_age)`
- soil curve value at reporting year `year` is `curve(offset(year))`
- soil scale factor is `curve(offset(year)) / curve(age_base + (2023 - 2021) - init_age)`
- actual soil carbon stock at reporting year `year` is:

  `soil_carbon_2023 * (curve(offset(year)) / curve(age_base + (2023 - 2021) - init_age))`

If the cut-reset rule moves the soil curve to the `InitAge = 0` row at `year0`, the actual soil stock source is still the 2023 soil raster. In that case the soil base curve point is `year2` on the reset curve, because `2023 - 2021 = 2`.

Offsets are clamped to the available `year0..yearN` series range.

The key idea is that the absolute numbers on the curves are not used as stocks — only the percentage change between the base-year column and the reporting-year column is used, and that percentage is then applied to the actual segment stock from the source data.

## Forestry Cut-Reset Rule

All forestry scenarios share the same cut-reset rule for segments inside a plan polygon. The rule is designed to detect segments where the current standing biomass looks unrealistically high for the selected curve, which usually means the stand has effectively been cut or the curve does not describe the true history of the stand well enough.

For each segment inside the polygon:

1. Read the segment carbon value from `luke_mvmisegmentit_muuttujat_kokomaa.Carbon`.
2. Match the initial biomass curve row using the full categorical key and the `InitAge` bucket chosen from the segment age.
3. Read `MaxCarbon` for that row. For the forestry scenario that owns the cut-reset rule (in current data that is scenario `1`), `MaxCarbon` is the value stored in the row. For every other scenario, `MaxCarbon` is forced to `999`, which in practice prevents the rule from firing because the `2/3 * 999 = 666 tC/ha` floor is never reached.
4. Evaluate `expected_curve_value_at_current_position = curve(offset(2021))` on the initially matched biomass row — that is, the biomass value the curve assigns to the segment at its current age.

The rule fires on a segment when both of the following are true:

- `segment_carbon >= (2 / 3) * MaxCarbon`
- `segment_carbon >= 3 * expected_curve_value_at_current_position`

When it fires:

- the biomass calculation keeps the same categorical key but switches to the `InitAge = 0` row of that key at `year0`
- the soil calculation also switches to the `InitAge = 0` row of the same key at `year0`

Important details:

- the comparison uses the segment-table `Carbon` value, not vegetation raster data
- there is no extra `0.5` multiplier applied during this comparison
- after switching, the curve offset is reset to `year0` at the 2021 segment-data year
- if either required `InitAge = 0` fallback row is missing, the implementation keeps the original matched rows instead of failing the whole plan

## Outputs

For each area, the calculator produces flat GeoJSON properties:

- `bio_carbon_total_{nochange|planned}_{year}`
- `ground_carbon_total_{nochange|planned}_{year}`
- `bio_carbon_ha_{nochange|planned}_{year}`
- `ground_carbon_ha_{nochange|planned}_{year}`
- `area`
- `natcode`

The frontend-facing API responses also include the plan-level `forestry_scenario`.

Reporting years are:

- `current_year`
- milestone years `2030..2080` in 5-year steps, filtered to years strictly greater than `current_year`

If the service runs during calendar year `2026`, the reporting years are `2026`, `2030`, `2035`, ..., `2080`.

## Calculation Flow

### A) Geometry, land-use, and region preparation

1. Parse features into a GeoDataFrame.
2. Reproject to EPSG:3067.
3. Repair invalid geometries with `buffer(0)` if possible.
4. Resolve land-use shares and `soil_change_new_vegetation_pct`.
5. Resolve `natcode` from `maakunta`.

### B) Actual carbon stocks from source data

Biomass actual stock source:

- per segment, use `luke_mvmisegmentit_muuttujat_kokomaa.Carbon` from `2021`

Soil actual stock source:

- per segment, use the weighted soil raster stock from `hiilikartta_maaperanhiili_2023_tcha`

These actual source stocks are the values to which the curve-derived scale factors are applied.

### C) Existing-land future deltas from curve tables

For each segment id inside the polygon:

1. Fetch segment variables once, including `Age` and `Carbon`.
2. Match the biomass row for the selected `Scen`.
3. Match the soil row for the selected `Scen`.
4. Evaluate the forestry cut-reset rule. Use `MaxCarbon` from the biomass row for the cut-owning forestry scenario (currently `Scen = 1`); for any other scenario treat `MaxCarbon` as `999`.
5. If the cut-reset rule fires, switch both the biomass row and the soil row to `InitAge = 0` and reset the curve position to `year0` at the 2021 data year.
6. Compute the biomass curve scale factor from the 2021 curve point to the reporting year curve point, and multiply that factor by the actual segment `Carbon` value from 2021.
7. Compute the soil curve scale factor from the 2023 curve point to the reporting year curve point, and multiply that factor by the actual segment soil raster value from 2023.
8. Multiply the resulting per-hectare carbon stocks by segment area and convert them to `tCO2`.

### D) Changed-land future deltas from annual coefficients

The changed-land coefficient lookup has two independent axes:

1. The plan polygon's region (`Maakunta`) is resolved once from the `maakunta.natcode` join described in section A. This is the "municipality/region multiplier" axis — each region has its own row set in `Hiilikartta_Kasvillisuuden_ja_maaperan_hiilensidonta_kayttotarkoitusluokittain_<date>.csv`.
2. The feature's `zoning_code` is mapped to the table's short code (`Lyhenne`).

With `(Maakunta, Lyhenne)` fixed, the four sequestration columns give annual rates for:

- new open vegetation, vegetation part
- new tree vegetation, vegetation part
- new open vegetation, soil part
- new tree vegetation, soil part

Values are stored in `t_C/ha/yr` in the new table (previously `t_CO2/ha/yr`). The calculation multiplies them by `44/12` to convert to `tCO2/ha/yr` before they are applied.

These coefficients are applied only to the changed-land share of the polygon. The land distribution itself still comes from the plan inputs (`landuse_new_open_vegetation`, `landuse_new_tree_vegetation`, `landuse_built`, `landuse_existing`). In other words, for a reporting year `year > current_year`:

```
changed_land_veg_delta(year) =
    area_ha * (landuse_new_open_vegetation/100)   * rate_new_open_vegetation_veg   * (year - current_year)
  + area_ha * (landuse_new_tree_vegetation/100)   * rate_new_tree_vegetation_veg   * (year - current_year)

changed_land_soil_delta(year) =
    area_ha * (landuse_new_open_vegetation/100)   * rate_new_open_vegetation_soil  * (year - current_year)
  + area_ha * (landuse_new_tree_vegetation/100)   * rate_new_tree_vegetation_soil  * (year - current_year)
```

where every `rate_*` is the value from the matched `(Maakunta, Lyhenne)` row after the `t_C → tCO2` conversion.

Powerline zoning codes (`ENsl`, `ENslja`) keep the special planned-tree biomass treatment. That special path now uses the selected forestry scenario and the same `InitAge` bucket logic, but with `Maingroup = 4`.

### E) Current-year parity and future planned/nochange formulas

For `current_year`:

- `veg_planned(current_year) = veg_nochange(current_year)`
- `soil_planned(current_year) = soil_nochange(current_year)`

No changed-land annual coefficients, land-use scaling, or powerline planned-tree deltas are applied at `current_year`.

For years after `current_year`:

- `veg_nochange(year) = existing_biomass_stock_from_segment_carbon(year)`
- `soil_nochange(year) = existing_soil_stock_from_2023_soil_raster(year)`
- `veg_planned(year) = landuse_existing * veg_nochange(year) + changed_land_veg_rate * Δyears (+ powerline tree term)`
- `soil_planned(year) = landuse_existing * soil_nochange(year) + new_vegetation * soil_retention * soil_2023_base + changed_land_soil_rate * Δyears`

Per-hectare values are derived by dividing totals by polygon area in hectares.

## API Metadata Returned to Frontend

The selected `forestry_scenario` is returned:

- in the `POST /calculation` response
- in `GET /calculation`
- in `GET /plan`
- in `GET /plan/external`
- inside finished-report metadata blocks

This is plan-level metadata, not per-feature GeoJSON data.
