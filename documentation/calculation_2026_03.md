# Carbon Calculation Specification – 2026-03

Previous snapshots:

- `calculation_2025.md`: 2025 implementation snapshot
- `calculation_2024.md`: legacy pre-2025 model

This document describes the latest implemented 2026-03 calculation model for a single area reservation (one polygon feature). It supersedes the earlier 2026 draft that described separate biomass and soil files per scenario.

Terminology:

- `nochange` = `Ilman kaavaa`
- `planned` = `Kaava`

## What Changed From 2025

1. Biomass curves are now stored in a single final table: `data/Hiilikartta_Veg.csv`.
2. Soil curves are now stored in a single final table: `data/Hiilikartta_Soil.csv`.
3. The scenario is no longer derived from the filename. It is matched from the `Scen` column inside those tables.
4. Curve selection no longer uses `Structure`, `Regime`, or `Rotation`. The lookup key is now `(Scen, Region, Maingroup, Soiltype, Drainage, Fertility, Species, InitAge)`.
5. Curve rows are selected by `InitAge` bucket using the segment `Age`. The chosen bucket is the largest available `InitAge` that does not exceed the segment age.
6. Scenario 1 adds a cut-detection heuristic based on the segment `Carbon` value from `luke_mvmisegmentit_muuttujat_kokomaa`, not on the vegetation raster.
7. When that scenario-1 cut heuristic triggers, both the biomass curve and the soil curve are moved to the `InitAge = 0` row for the same categorical key.
8. Powerline-specific biomass rows are now available in the same final tables via `Maingroup = 4`.
9. Biomass and soil curves are now used to derive percentage-based change from the correct base year, and that percentage is applied to the actual carbon stock from the source data.
10. The reporting horizon now ends at 2080. `planned` and `nochange` return exactly the same values for `current_year`, and differences begin at the first milestone year, which is 2030.

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

The soil raster is treated as carbon (`tC/ha`) and converted to `tCO2` using:

`c_to_co2 = 44 / 12`

Source years:

- vegetation raster dataset year: `2021`
- soil raster stock year: `2023`

### Segment data

- Segment-id raster: `luke_mvmisegmentit_id_kokomaa`
- Segment variables table: `luke_mvmisegmentit_muuttujat_kokomaa`

The segment variables table provides the current forest attributes, including:

- `Age`, used for `InitAge` bucket selection
- `Carbon`, used in scenario-1 cut detection and as the actual biomass carbon stock for percentage scaling

Source year:

- segment variables year: `2021`

### Region enrichment

- Region table: `maakunta`
- Geometry column: `geom`
- Region code column: `natcode`

For each polygon, the calculation selects the `maakunta` row with the largest intersection area.

### Curve files

- `data/Hiilikartta_Veg.csv`
- `data/Hiilikartta_Soil.csv`

The vegetation and soil curve tables are the 2026 graph tables. That publication year is not used directly in the calculation; the tables are treated as relative curves indexed by `InitAge` and `yearN`.

Changed-land annual coefficients:

- `data/Hiilikartta_Kasvillisuuden_ja_maaperan_hiilensidonta_kayttotarkoitusluokittain.csv`

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

Examples with the current files:

- `Age = 84` uses `InitAge = 50`
- `Age = 85` uses `InitAge = 85`

### Relative-time interpretation and percentage scaling

The selected curve row is treated as a relative-time series starting at `year0`.

Let:

- `age_base` = segment `Age` from segment variables, interpreted as age in 2021
- `init_age` = selected `InitAge`
- `offset(year) = age_base + (year - 2021) - init_age`
- if scenario-1 cut detection triggers, the curve position is reset to the `InitAge = 0` row at `year0`, so `offset(year) = year - 2021`

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

If scenario-1 cut detection resets the soil curve to the `InitAge = 0` row at `year0`, the actual soil stock source is still the 2023 soil raster. In that case the soil base curve point is `year2` on the reset curve, because `2023 - 2021 = 2`.

Offsets are clamped to the available `year0..yearN` series range.

## Scenario 1 Cut Detection

Scenario 1 has an extra rule to detect stands that appear to have been cut.

For each segment inside the polygon:

1. Read the segment carbon value from `luke_mvmisegmentit_muuttujat_kokomaa.Carbon`.
2. Use that segment carbon value directly in the comparison. This is the same quantity that was earlier described informally as "segment biomass / 2".
3. Compare that segment carbon value against the initially age-matched biomass row.

The cut heuristic triggers only if both are true:

- `segment_carbon >= (2 / 3) * MaxCarbon`
- `segment_carbon >= 3 * expected_curve_value_at_current_age`

When both are true:

- the biomass calculation uses the same categorical key but switches to the `InitAge = 0` row at `year0`
- the soil calculation also switches to the `InitAge = 0` row at `year0` for the same segment

Important details:

- the comparison uses the segment-table `Carbon` value, not vegetation raster data
- there is no extra `0.5` multiplier applied during this comparison
- only scenario 1 uses this rule
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
4. For scenario 1, evaluate cut detection using the segment `Carbon` value.
5. If the cut heuristic triggers, switch both the biomass row and the soil row to `InitAge = 0`.
6. Compute the biomass curve scale factor from the 2021 curve point to the reporting year curve point, and multiply that factor by the actual segment `Carbon` value from 2021.
7. Compute the soil curve scale factor from the 2023 curve point to the reporting year curve point, and multiply that factor by the actual segment soil raster value from 2023.
8. Multiply the resulting per-hectare carbon stocks by segment area and convert them to `tCO2`.

### D) Changed-land future deltas from annual coefficients

From `Hiilikartta_Kasvillisuuden_ja_maaperan_hiilensidonta_kayttotarkoitusluokittain.csv`, fetch annual vegetation and soil rates by `(Maakunta, Lyhenne)`.

These are applied only to changed land:

- new open vegetation
- new tree vegetation

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
