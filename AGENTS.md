# Agent Notes (hiilikartta-data-service)

This repository is a **FastAPI + background worker** service used by Hiilikartta / climate-map for carbon calculations on zoning-plan polygons.

If you change anything that affects **runtime behavior, configuration, APIs, database schema, or calculation logic**, update **both** `README.md` and `AGENTS.md` (and `documentation/calculation_2026_03.md` when the latest calculation changes).

## What the service does

- Accepts a zipped vector dataset (polygons) via `POST /calculation`
- Stores the uploaded plan, selected forestry scenario, and calculation state in a Postgres “state DB”
- Enqueues background jobs (SAQ on Redis) to compute results feature-by-feature
- Queries an external PostGIS “GIS DB” for rasters/segments/regions and combines those with curve/coefficient files under `data/`

## Runtime architecture (containers/services)

- **API**: FastAPI app (`app/main.py`)
- **Worker**: SAQ worker (`app/saq_worker.py`) running `calculate_piece`
- **Redis**: SAQ queue + distributed GIS semaphore
- **State DB**: Postgres for plans/results (schema via Alembic)
- **GIS DB**: external PostGIS with required datasets (not started by docker-compose)

Compose files:

- `docker-compose.dev.yml`: dev stack (requires external docker network `climate-map-network`)
- `docker-compose.prod.yml`: prod-ish stack (expects external `proxy-net` and Traefik labels; Redis/worker run on an internal per-stack network to avoid cross-talk)

## Quick start (dev)

1. One-time network:

```bash
docker network create climate-map-network
```

2. Configure env:

```bash
cp .env.template .env
```

You must set GIS connection values (`GIS_PG_*`). Without a working GIS DB, calculation endpoints will fail.

Safety rails:

- Dev containers refuse to start unless `STATE_PG_DB` contains `dev`.
- Tests refuse to run unless `STATE_PG_TEST_DB` contains `test`.

3. Start:

```bash
docker compose up --build
```

Default URLs:

- API: `http://localhost:${APP_PORT}` (docs at `/docs`)
- Jupyter: `http://localhost:${NOTEBOOK_PORT}` (token: `NOTEBOOK_TOKEN`)
- SAQ Web UI: `http://localhost:${SAQ_WEB_PORT}`

## Key commands (inside containers)

- Format: `docker compose exec app-dev poetry run black .`
- Tests: `docker compose exec app-dev poetry run pytest`
- Migrations: `docker compose exec app-dev poetry run alembic upgrade head`
- State DB pgcrypto (if migrations can't create it):

```bash
docker compose exec state-db-dev sh -lc \
  'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "CREATE EXTENSION IF NOT EXISTS \"pgcrypto\";"'
```

## Configuration (env vars)

Authoritative list: `.env.template`. Highlights:

- **Ports (compose)**: `APP_PORT`, `NOTEBOOK_PORT`, `SAQ_WEB_PORT`
- **SAQ workers**: `SAQ_WORKERS_COUNT`
- **Redis**: `REDIS_HOST` (set by compose; Redis listens on `6379` inside the Docker network)
- **State DB**: `STATE_PG_*` (dev), `STATE_PG_TEST_*` (tests)
- **GIS DB**: `GIS_PG_*` (must point to a PostGIS DB with required datasets)
- **Auth (Zitadel)**: `ZITADEL_DOMAIN`, `ZITADEL_CLIENT_ID`, `ZITADEL_CLIENT_SECRET`
- **GIS throttling**: `GIS_LOCAL_MAX_CONCURRENT`, `GIS_DISTRIBUTED_MAX_CONCURRENT`, `GIS_SLOT_TTL`, `GIS_STATEMENT_TIMEOUT_SECONDS`
- **Analytics (optional, prod)**: `UMAMI_ENABLED`, `UMAMI_HOST_URL`, `UMAMI_WEBSITE_ID`

## Data dependencies (don’t “simplify” away)

### GIS DB tables/rasters

Used by `app/db/gis.py` and described in `documentation/calculation_2026_03.md`:

- `hiilikartta_kasvillisuudenhiili_2021_tcha`
- `hiilikartta_maaperanhiili_2023_tcha`
- `luke_mvmisegmentit_id_kokomaa`
- `luke_mvmisegmentit_muuttujat_kokomaa`
- `maakunta` (`geom`, `natcode`)

### Repo files under `data/`

Loaded from `app/utils/data_loader.py` and warmed into curve caches on API + worker startup:

- `data/Hiilikartta_Veg_20260415.csv`
- `data/Hiilikartta_Soil_20260415.csv`
- `data/Hiilikartta_Kasvillisuuden_ja_maaperan_hiilensidonta_kayttotarkoitusluokittain_20260415.csv`

## Gotchas / agent tips

- **Worker is required**: the API only enqueues jobs; without `worker-*` nothing completes.
- **Responses are gzip**: read endpoints set `Content-Encoding: gzip`; use `curl --compressed` in examples.
- **Frontend responses include plan metadata**: `forestry_scenario` is returned at the response level and in finished-report metadata blocks.
- **Final curve tables are single files**: the selected scenario comes from the `Scen` column in `Hiilikartta_Veg_20260415.csv` / `Hiilikartta_Soil_20260415.csv`; current shipped data exposes scenarios `1..3`.
- **Biomass actual stock comes from segment data**: the latest calculation uses `luke_mvmisegmentit_muuttujat_kokomaa.Carbon` as the actual biomass stock source and for scenario-1 cut detection, not the vegetation raster.
- **Soil actual stock comes from the 2023 raster**: the latest calculation scales per-segment weighted values from `hiilikartta_maaperanhiili_2023_tcha`.
- **Forecast years are capped at 2080**: the output years are `current_year` plus milestone years `2030..2080` that are strictly greater than `current_year`.
- **GIS is the bottleneck**: prefer the throttled helpers in `app/db/gis.py` (they apply local + Redis semaphores and `statement_timeout`).
- **Keep the soil raster bbox predicate**: the soil-by-segment lookup in `app/db/gis.py` relies on `ST_ConvexHull(r.rast) && sample_point` so PostgreSQL can use the existing raster GiST index.
- **Simplified GIS SQL must use CAST binds**: when editing raw `text()` SQL for large-area/simplified queries, use `CAST(:param AS type)` instead of `:param::type` so SQLAlchemy + asyncpg bind correctly.
- **Timeouts aren’t fatal to the whole plan**: single-feature GIS timeouts are skipped and calculation continues.
- **Calculations log phase timings**: `CarbonCalculator.calculate()` now emits one summary line per calculation with natcode/segment/soil/curve/assembly timings; keep it aggregate-only, never per segment.
- **Keep module loggers non-propagating**: `app/utils/logger.py` should attach one `RichHandler` and set `propagate = False`, otherwise API/worker logs get duplicated by root logging.
- **State DB schema is Alembic-managed**: update `app/db/models/*` + create migrations; don’t hand-edit `sql/state/create.sql` and expect it to be authoritative.
- **Prod startup enforces migrations**: by default, prod containers refuse to start if the state DB is not at Alembic head (`STATE_DB_MIGRATION_MODE=check`). Set `STATE_DB_MIGRATION_MODE=upgrade` for the API container to run `alembic upgrade head` on startup.

## Where to change things

- HTTP API / auth: `app/main.py`, `app/auth/validator.py`
- Worker behavior / retries: `app/saq_worker.py`, `app/db/errors.py`
- Calculation logic: `app/calculator/calculator.py` (keep `documentation/calculation_2026_03.md` in sync)
- GIS SQL: `app/db/gis.py` (be careful: performance + PostGIS semantics)
- State DB access/model: `app/db/plan.py`, `app/db/models/plan.py`, `alembic/`
- Devcontainer: `.devcontainer/devcontainer.json`
