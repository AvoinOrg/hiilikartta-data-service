# Carbon Calculation (Per Area) – Legacy Implementation

This document describes the previous calculation approach (zoning multipliers + `Mabp`). The newer implementation is documented in `calculation_2025.md`, and the latest implementation is documented in `calculation_2026_03.md`.

This document explains what happens when the backend calculates carbon estimates for a single “area” (one polygon feature), what data is required, what is computed first, and where each step happens in the codebase.

The description below matches the legacy “PostGIS-aggregated” implementation: rasters are not downloaded to Python for per-pixel processing; instead, PostGIS computes the relevant aggregates and Python combines them into the final per-area outputs.

## What an “Area” Must Contain

The calculator consumes a GeoJSON-like FeatureCollection (a Python `dict`) with a `features` array. Each feature must contain:

- `geometry`: Polygon or MultiPolygon geometry (input CRS is assumed EPSG:4326).
- `properties.zoning_code`: Used to look up planned-scenario multipliers (from `data/aluekertoimet.csv`).
- `properties.id` (recommended): Used for stable sorting/output identity (`CarbonCalculator.__init__` sorts by `id` if present).

If the geometry is invalid, the calculator attempts to fix it by applying `buffer(0)`; if it is still invalid after that, calculation fails.

Relevant code: `app/calculator/calculator.py` (`CarbonCalculator.__init__`).

## Data Sources Used

### GIS/PostGIS database (rasters + segment variables)

The calculator reads these sources via `app/db/gis.py`:

- Segment-id raster: `luke_mvmisegmentit_id_kokomaa` (pixel value = segment id / “kuvio”).
- Segment variables table: `luke_mvmisegmentit_muuttujat_kokomaa` (row per `kuvio` with categorical variables used to select a biomass curve).
- Vegetation carbon raster: `hiilikartta_kasvillisuudenhiili_2021_tcha` (values are “per-hectare”, i.e. tC/ha style).
- Soil carbon raster: `hiilikartta_maaperanhiili_2023_tcha` (values are “per-hectare”).

### Local CSVs (packaged with the service)

- `data/legacy/BiomassCurves.txt`: Lookup table for biomass growth curves; the calculator currently uses the `Mabp` value for the matched curve row.
- `data/aluekertoimet.csv`: Zoning-code multipliers for “planned” scenario.

Loaded via `app/utils/data_loader.py`.

## Outputs Produced (Per Area)

For each area, the calculator outputs (as GeoJSON) a feature with properties including:

- `area` (m²): From the feature geometry (after reprojecting to EPSG:3067).
- `bio_carbon_total_{scenario}_{year}` and `ground_carbon_total_{scenario}_{year}`: Absolute totals for the polygon in CO₂-equivalent units.
- `bio_carbon_ha_{scenario}_{year}` and `ground_carbon_ha_{scenario}_{year}`: Per-hectare versions, derived by dividing totals by polygon area (ha).

`scenario` is `nochange` or `planned`.
`year` includes `current_year` and then 2030..2095 in 5-year steps.

Relevant code: `app/calculator/calculator.py` (`CarbonCalculator.calculate`).

## Overall Flow (API → Worker → Calculator)

At runtime, the typical flow is:

1. Client uploads a dataset to `POST /calculation` (`app/main.py`).
2. A “plan” record is stored; a background job is queued (`app/main.py`, `app/saq_worker.py`).
3. The worker processes features one-by-one with `calculate_piece`:
   - It instantiates `CarbonCalculator` with a FeatureCollection containing a single feature.
   - It stores the resulting per-area feature into `plan.report_areas`.
4. Once all features are processed, totals are computed by summing the per-area results (`CarbonCalculator.calculate_totals`) and saved to `plan.report_totals`.

This doc focuses on step (3): per-area calculation.

## Per-Area Calculation, Step by Step

### 1) Geometry preparation and CRS handling

`CarbonCalculator.__init__`:

1. Parses the incoming FeatureCollection into a GeoPandas `GeoDataFrame`.
2. Sets CRS to EPSG:4326, then reprojects to EPSG:3067 (meters) for area math and GIS queries.
3. Validates geometries and tries to fix invalid ones with `buffer(0)`.
4. Sets `self.simplify_calcs` depending on polygon size:
   - If total area > 50,000 m² (~5 ha): `simplify_calcs=True` (faster, approximate).
   - Otherwise: `simplify_calcs=False` (slower, fractional/edge-aware).

### 2) Zoning multipliers (“planned” scenario)

`CarbonCalculator.calculate` loads `data/aluekertoimet.csv` and, for each polygon, looks up:

- “Kasvillisuuden hiiltä säästyy” → `area_multipliers_bio[i]`
- “Maaperän hiiltä säästyy” → `area_multipliers_ground[i]`

Important behavior: if the zoning code is missing from the CSV index, the multiplier remains `0`, which means planned-scenario future years become `0` for that area.

### 3) PostGIS aggregation (no TIFFs in Python)

Instead of fetching clipped TIFF rasters and iterating pixels in Python, the calculator asks PostGIS for:

#### 3a) Segment-area histogram (hectares per segment id)

`fetch_segment_areas_ha_for_regions(wkts, crs, simplify_calcs=...)` returns rows:

- `order_num` (1-based index matching input `wkts` order)
- `segment_id` (pixel value in `luke_mvmisegmentit_id_kokomaa`)
- `area_ha` (how many hectares of that segment lie inside the polygon)

This is computed two ways:

- Accurate (`simplify_calcs=False`): `ST_PixelAsPolygons(ST_Clip(..., touched=>TRUE))` and sum `Area(Intersection(pixel_geom, polygon))/10000`.
- Fast (`simplify_calcs=True`): `ST_ValueCount(ST_Clip(..., touched=>FALSE))` and multiply pixel counts by a fixed `grid_to_ha = 16*16/10000`.

#### 3b) Vegetation and soil base totals (area-weighted raster sums)

`fetch_weighted_raster_sum_ha_for_regions(table, wkts, crs, simplify_calcs=...)` returns rows:

- `order_num`
- `sum_weighted_ha` = Σ (pixel_value_per_ha * intersect_area_ha)

For `simplify_calcs=False`, it again uses `ST_PixelAsPolygons` and exact intersection areas.
For `simplify_calcs=True`, it uses `ST_SummaryStatsAgg(ST_Clip(...)).sum` and multiplies by `grid_to_ha`.

#### 3c) What Python keeps in memory

After these queries, Python holds compact dictionaries, not raster matrices:

- `segment_areas_by_order[order_num][segment_id] = area_ha`
- `bio_sum_by_order[order_num] = sum_weighted_ha`
- `ground_sum_by_order[order_num] = sum_weighted_ha`

### 4) Biomass curve contribution (without pixel-by-pixel iteration)

The biomass curve logic needs to pick the correct curve “type” for each segment id and apply its growth rate across years.

The current implementation avoids pixel loops by leveraging the segment-area histogram:

1. Determine which segment ids appear in the polygon:
   - `uniq_ids_list = sorted(all segment_id keys from segment_areas_by_order[order_num])`
2. Fetch segment variables once per id:
   - `fetch_variables_for_ids(uniq_ids_list)` returns rows from `luke_mvmisegmentit_muuttujat_kokomaa`.
3. Map each segment id to a `Mabp` value:
   - Build a lookup from `data/legacy/BiomassCurves.txt` keyed by the categorical variables:
     `("Region","Maingroup","Soiltype","Drainage","Fertility","Species","Structure","Regime") -> Mabp`
   - For each segment id, read its variables from the DB row, build the key, and pick `Mabp`.
4. Aggregate the biomass growth per year, per polygon:
   - For each segment id inside the polygon:
     - contribution(year) += `area_ha(segment) * Mabp(segment) * (year - 2021)`

This is equivalent to the old “per-pixel overlap fraction” approach because:

- Old approach: Σ (overlap_fraction(pixel) * Mabp(segment(pixel)) * year_diff) * grid_cell_area_ha
- New approach: Σ (area_ha_of_segment_in_polygon * Mabp(segment) * year_diff)

They match because `area_ha_of_segment_in_polygon` is already the sum of all fractional pixel areas for that segment.

### 5) Unit conversions and scenarios

Constants in `app/calculator/calculator.py`:

- `c_to_co2 = 44/12` converts carbon mass to CO₂ mass.
- `sqm_to_ha = 1/10000` converts m² to hectares.

Base raster sums (`sum_weighted_ha`) are treated as carbon mass totals and converted to CO₂ by multiplying by `c_to_co2`.

For each year and scenario:

- **nochange**:
  - `bio_total = base_bio * c_to_co2 + bm_curve_contribution(year)`
  - `ground_total = base_ground * c_to_co2`
- **planned**:
  - For the current year: same as `nochange` (no multiplier applied).
  - For future years: multiply totals by the zoning multipliers (`area_multipliers_*`).

Finally, per-hectare columns are derived by dividing totals by the polygon’s area in hectares.

## Totals Across Multiple Areas

Totals are not a raster recomputation. They’re calculated by summing the already-computed per-area columns:

- `CarbonCalculator.calculate_totals` finds all columns containing `nochange` or `planned`, sums them, and also computes per-hectare totals using the unioned geometry area.

Relevant code: `app/calculator/calculator.py` (`calculate_totals`) and `app/saq_worker.py` (finalization path).

## Notes / Tradeoffs

- **Accuracy vs speed (`simplify_calcs`)**:
  - Accurate mode uses exact pixel intersection areas (fractional pixels), which is better for small polygons and edges but heavier in PostGIS.
  - Simplified mode uses center-in-polygon pixel inclusion and a fixed cell area; it is faster but ignores fractional pixels.
- **Performance throttling**:
  - GIS queries are throttled with both a local semaphore and an optional distributed semaphore via Redis to prevent exhausting GIS DB connections (`app/db/gis.py`, `app/db/gis_semaphore.py`, `app/db/redis_semaphore.py`).
- **Grid resolution assumption**:
  - Simplified mode assumes 16×16m cell size via `grid_to_ha`. If the underlying raster resolution changes, the simplified-mode conversions must be updated accordingly.
