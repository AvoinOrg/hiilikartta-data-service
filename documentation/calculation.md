# Carbon Calculation (Per Area) – New Model (Implemented)

This document describes the **current “new model” implementation** for calculating carbon estimates for an area reservation (one polygon feature). It is implemented in `app/calculator/calculator.py` and uses PostGIS for raster/segment aggregation.

Terminology:

- `nochange` = **Ilman kaavaa** (baseline / no plan)
- `planned` = **Kaava** (with plan)

## Inputs (Feature Properties)

Each input feature must include:

- `geometry`: polygon or multipolygon (input assumed EPSG:4326; reprojected to EPSG:3067 for area math).
- `zoning_code`: the land-use / “Lyhenne” code (e.g. `AP`, `E`, `Enslrv`). Used for coefficient lookup.

### Land-cover shares (percentages)

These must sum to **100** (within a small tolerance). Endpoints accept the English property names below (and also accept the Finnish aliases listed).

Notes:

- If **any** of these columns are present on a feature, the implementation requires **all four**.
- If **none** of these columns are present, the current implementation defaults to:
  `landuse_built=0`, `landuse_new_open_vegetation=0`, `landuse_new_tree_vegetation=0`, `landuse_existing=100`.

- `landuse_built` (Finnish alias: `rakennettu`)  
  Share that becomes built / non-vegetated.
- `landuse_new_open_vegetation` (Finnish alias: `uusi_avoin_kasvipeite`)  
  Share that becomes new open vegetation.
- `landuse_new_tree_vegetation` (Finnish alias: `uusi_puustoinen_kasvipeite`)  
  Share that becomes new tree vegetation.
- `landuse_existing` (Finnish alias: `aiempi_maanpeite`)  
  Share that remains as the existing land cover.

### Soil change factor on new vegetation

- `soil_change_new_vegetation_pct` (Finnish alias: `Maaperan_muutos_uuden_kasvipeitteen_alueilla`)  
  Percent in [0,100]. If omitted, the implementation currently defaults it to `0`.

The soil retention multiplier is:

`soil_retention = 1 - (soil_change_new_vegetation_pct / 100)`

## Region (“Maakunta”) enrichment

The coefficient table is keyed by a region code. The implementation derives it from the GIS DB:

- Table: `maakunta`
- Geometry column: `geom`
- Region id column: `natcode` (two-digit strings like `"01"`, `"11"`)

For each input polygon, we pick the `maakunta` row with the **largest intersection area** and store:

- `natcode` (string, as-is)
- `maakunta_code = int(natcode)` for matching the coefficient CSV’s `Maakunta` column (which is numeric)

Implementation: `app/db/gis.py` (`fetch_natcode_for_regions`).

## Data Sources Used

### Base stocks (rasters, aggregated in PostGIS)

Per-area zonal sums are computed in PostGIS and returned as totals (not matrices):

- Vegetation stock raster: `hiilikartta_kasvillisuudenhiili_2021_tcha`
- Soil stock raster: `hiilikartta_maaperanhiili_2023_tcha`

Both rasters are treated as **tC/ha** and converted to **tCO2** using:

`c_to_co2 = 44 / 12`

Implementation: `app/db/gis.py` (`fetch_weighted_raster_sum_ha_for_regions`).

### No-change sequestration (segments + biomass curves)

For part D, the implementation uses:

- Segment-id raster: `luke_mvmisegmentit_id_kokomaa` (pixel value = `kuvio` id)
- Segment variables table: `luke_mvmisegmentit_muuttujat_kokomaa`
- Biomass curves: `data/BiomassCurves.txt`

Biomass curves are matched by:

`(Region, Maingroup, Soiltype, Drainage, Fertility, Species, Structure, Regime, Rotation)`

with a fallback match that ignores `Rotation` if needed.

The new model uses the **year1..yearN series** (not `Mabp`) to compute future changes.

### Changed-land sequestration coefficients (CSV)

Part E uses annual sequestration coefficients from:

`data/Hiilikartta_Kasvillisuuden_ja_maaperan_hiilensidonta_kayttotarkoitusluokittain.csv`

Lookup key:

- `Maakunta` = `int(natcode)`
- `Lyhenne` = `zoning_code`

Columns used (values assumed **tCO2/ha/year**):

- `Uusi_avoin_kasvillisuus_kasvillisuuden_hiilensidonta_t_CO2`
- `Uusi_puustoinen_kasvillisuus_kasvillisuuden_hiilensidonta_t_CO2`
- `Uusi_avoin_kasvillisuus_maaperan_hiilensidonta_t_CO2`
- `Uusi_puustoinen_kasvillisuus_maaperan_hiilensidonta_t_CO2`

## Outputs

For each area, the calculator produces (as flat GeoJSON properties):

- `bio_carbon_total_{nochange|planned}_{year}` (tCO2)
- `ground_carbon_total_{nochange|planned}_{year}` (tCO2)
- `bio_carbon_ha_{nochange|planned}_{year}` (tCO2/ha)
- `ground_carbon_ha_{nochange|planned}_{year}` (tCO2/ha)
- `area` (m²)
- `natcode` (string, derived from `maakunta`)

Years included:

- `current_year` (from `datetime.now().year`)
- 2030..2095 in 5-year steps

## Calculation Flow (A–E)

Let:

- `A_ha` = polygon area in hectares
- `f_existing = landuse_existing / 100`
- `f_open = landuse_new_open_vegetation / 100`
- `f_tree = landuse_new_tree_vegetation / 100`
- `f_new_veg = f_open + f_tree`

### A) Base information

1. Parse and validate geometries, compute `area` and `area_ha`.
2. Read land-use percentages and validate they sum to 100 (small drift is normalized).
3. Resolve `natcode` from `maakunta` and map to `maakunta_code=int(natcode)`.

### B) Vegetation base stock

Baseline vegetation stock (tCO2):

`veg_base = (Σ (veg_raster_tC_per_ha * intersect_area_ha)) * c_to_co2`

Scenarios:

- `veg_nochange_base = veg_base`
- `veg_planned_base = f_existing * veg_base`

### C) Soil base stock

Baseline soil stock (tCO2):

`soil_base = (Σ (soil_raster_tC_per_ha * intersect_area_ha)) * c_to_co2`

Soil retention multiplier:

`soil_retention = 1 - (soil_change_new_vegetation_pct / 100)`

Scenarios:

- `soil_nochange_base = soil_base`
- `soil_planned_base = f_existing * soil_base + f_new_veg * soil_retention * soil_base`

### D) Sequestration on “no-change land” (biomass curves, yearly series)

This part produces a **delta** `Δveg(year)` for the polygon, measured from the **vegetation raster base year (2021)**:

1. PostGIS returns a histogram of segment id areas:
   - `segment_areas_by_order[order_num][segment_id] = area_ha`
2. For each segment id, fetch its variables (including `Age`) and match a biomass curve series.
3. For each reporting year:
   - compute curve value at the segment’s `Age` (interpreted as age-at-2021) and at the requested calendar year
   - `delta_per_ha = curve(age_year) - curve(age_2021)`
   - accumulate `Δveg(year) += area_ha(segment) * delta_per_ha * c_to_co2`

Scenarios:

- `veg_nochange(year) = veg_nochange_base + Δveg(year)`
- `veg_planned(year) includes only existing-share growth from D: f_existing * Δveg(year)` (see total formula below)

Soil change in D is **not yet implemented** in the current code (no soil time series is available in the current inputs), so soil stays constant unless part E adds it.

### E) Sequestration on changed land (annual coefficients)

From the coefficient CSV, fetch annual rates (tCO2/ha/year):

- `k_veg_open`, `k_veg_tree`, `k_soil_open`, `k_soil_tree`

Convert them to **polygon annual totals** (tCO2/year):

`veg_rate = A_ha * (f_open * k_veg_open + f_tree * k_veg_tree)`

`soil_rate = A_ha * (f_open * k_soil_open + f_tree * k_soil_tree)`

For a reporting year `year`, with `Δyears = max(0, year - current_year)`:

- vegetation changed-land contribution: `veg_rate * Δyears`
- soil changed-land contribution: `soil_rate * Δyears`

## Final per-year scenario formulas (what the code currently does)

For each reporting `year`:

- `veg_nochange_total(year) = veg_base + Δveg(year)`
- `soil_nochange_total(year) = soil_base`

- `veg_planned_total(year) = veg_planned_base + f_existing * Δveg(year) + veg_rate * Δyears`
- `soil_planned_total(year) = soil_planned_base + soil_rate * Δyears`

Per-hectare outputs are derived as:

`*_ha = *_total / A_ha`

## Implementation Notes (performance + in-memory data flow)

- Raster and segment work is done in PostGIS; Python does not store raster matrices in memory.
- For small geometries (`area_sum <= 50,000 m²`) the code uses exact pixel intersection areas (fractional pixels).
- For large geometries, it switches to faster approximate aggregation (whole-cell counting) to protect performance.

Key in-memory structures in `app/calculator/calculator.py`:

- `bio_sum_by_order[order_num] -> Σ(value_per_ha * intersect_area_ha)` for vegetation raster
- `ground_sum_by_order[order_num] -> Σ(value_per_ha * intersect_area_ha)` for soil raster
- `segment_areas_by_order[order_num][segment_id] -> area_ha`
- `variables_dict[segment_id] -> {Region, Maingroup, ..., Age, Rotation, ...}`
- `veg_curve_delta_co2_by_order[order_num][year] -> Δveg(year) in tCO2`

## Known Gaps / Pending Clarifications

- Part D soil time series: spec says soil change depends only on attributes, but no soil series source is wired in yet.
- Part E “change class” (maanpeitteen muutos -luokka): current implementation uses only `(Maakunta, Lyhenne)` coefficients.
- Enslrv special rule: not implemented beyond whatever coefficients exist for the `zoning_code`.
