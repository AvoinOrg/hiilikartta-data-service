# hiilikartta-data-service

Backend service for **Hiilikartta / climate-map** that calculates vegetation + soil carbon estimates for zoning-plan areas.

The service exposes a FastAPI HTTP API that accepts a zipped vector dataset (polygons), runs a PostGIS-backed calculation asynchronously, and stores results to a “state” Postgres database. The heavy spatial work (rasters, segment aggregation) is done against a separate PostGIS **GIS database** that already contains the required datasets.

## Architecture (runtime)

- **API** (`app/main.py`, FastAPI): HTTP endpoints, plan persistence, serves results.
- **Worker** (`app/saq_worker.py`, SAQ): background jobs; calculates per-feature results and updates the state DB.
- **Redis**: SAQ job queue + distributed GIS throttling semaphore.
- **State DB** (Postgres): stores uploaded plans + calculation outputs (JSONB).
- **GIS DB** (external PostGIS): provides rasters/segments/regions needed by the calculation.

`docker-compose.*.yml` spins up everything except the GIS DB.

## Tech stack (selected)

- **Web/API**: FastAPI, Uvicorn (dev) / Gunicorn+UvicornWorker (prod)
- **Async DB**: SQLAlchemy 2.x (async) + `asyncpg`
- **Migrations**: Alembic (+ `alembic-postgresql-enum`)
- **Geo**: GeoPandas + Shapely + Rasterio stack
- **Async jobs**: SAQ + Redis
- **Auth**: Zitadel token introspection via Authlib + Requests

## API (service usage)

### Endpoints

Large payload endpoints (`GET /calculation`, `GET /plan`, `GET /plan/external`) return **gzip-compressed** bodies (`Content-Encoding: gzip`). Many HTTP clients handle this automatically; with `curl` use `--compressed`.

- `POST /calculation?id=<uuid>&visible_id=<string>&name=<string>`
  - `multipart/form-data` with field `file` (a zipped dataset readable by GeoPandas).
  - Creates/updates a plan and enqueues a background job (`calculate_piece`).
  - Auth is optional; if a valid token is provided, the plan is associated with that user.
- `GET /calculation?id=<uuid>`
  - `202` while processing, `200` when finished, `206` if the plan ended in an error state.
  - When finished: returns `data.totals` and `data.areas` (GeoJSON stored in DB).
- `GET /plan/external?id=<uuid>`
  - Public “share” endpoint: returns `{id, name, report_data?}`.

Authenticated (Zitadel) endpoints:

- `PUT /plan?id=<uuid>&visible_id=<string>&name=<string>` (upload/replace plan data)
- `GET /plan?id=<uuid>` (fetch a user’s plan)
- `DELETE /plan?id=<uuid>`
- `GET /user/plans`

FastAPI docs: `GET /docs`

### Typical client flow

1. Upload a plan to `POST /calculation ...`
2. Poll `GET /calculation?id=...` until `calculation_status == FINISHED`
3. Parse `data.totals` + `data.areas`

Example (dev):

```bash
PLAN_ID=$(python -c 'import uuid; print(uuid.uuid4())')

curl --compressed -F "file=@tests/data/test-data-small-polygon.zip" \
  "http://localhost:8000/calculation?id=$PLAN_ID&visible_id=demo&name=Demo"

curl --compressed "http://localhost:8000/calculation?id=$PLAN_ID"
```

## Calculation model

The current implementation is documented in `documentation/calculation.md` (“new model”). In short, for each polygon the calculator produces:

- vegetation + soil **base stocks** from PostGIS rasters (converted from tC/ha to tCO2),
- future **deltas on existing land** from segment variables + curve series,
- future **deltas on changed land** from annual sequestration coefficients (CSV),
- outputs for `nochange` vs `planned` scenarios for `current_year` and 2030..2095 (5y steps).

### Input expectations (high level)

From `documentation/calculation.md`:

- `geometry`: polygon/multipolygon (input assumed EPSG:4326; reprojected for area math)
- `zoning_code`: land-use code used for coefficient lookup
- optional land-cover shares (percentages) and soil-change factor; see the doc for defaults and accepted aliases

### Data inputs

GIS DB (PostGIS) tables/rasters (see `documentation/calculation.md` for details):

- `hiilikartta_kasvillisuudenhiili_2021_tcha`
- `hiilikartta_maaperanhiili_2023_tcha`
- `luke_mvmisegmentit_id_kokomaa`
- `luke_mvmisegmentit_muuttujat_kokomaa`
- `maakunta` (`geom`, `natcode`)

Repo data files (loaded on API startup via `app/utils/data_loader.py`):

- `data/BiomassCurves.txt`
- `data/SoilCurves.txt`
- `data/aluekertoimet.csv`
- `data/Hiilikartta_Kasvillisuuden_ja_maaperan_hiilensidonta_kayttotarkoitusluokittain.csv`

### Operational behavior (GIS throttling)

GIS operations are intentionally throttled to protect the GIS DB:

- local (per-process) semaphore: `GIS_LOCAL_MAX_CONCURRENT`
- distributed (cross-process) semaphore via Redis: `GIS_DISTRIBUTED_MAX_CONCURRENT`, `GIS_SLOT_TTL`
- Postgres `statement_timeout`: `GIS_STATEMENT_TIMEOUT_SECONDS`

When the GIS DB is at capacity, jobs are re-enqueued later (`GisRetryLaterError`). If a single feature times out, the worker skips that feature and continues.

## Running locally (Docker Compose)

### Prerequisites

- Docker Engine / Docker Desktop + Compose v2 (`docker compose`)
- Access to a PostGIS **GIS DB** with the required datasets

One-time: create the external Docker network used by `docker-compose.dev.yml`:

```bash
docker network create climate-map-network
```

### Configure env

Create your local env file:

```bash
cp .env.template .env
```

At minimum you must set the GIS connection values:

- `GIS_PG_USER`, `GIS_PG_PASSWORD`, `GIS_PG_DB`, `GIS_PG_HOST`, `GIS_PG_PORT`

Optional tuning:

- `SAQ_WORKERS_COUNT` (worker process count; dev default is 3)

Safety rails:

- Dev containers refuse to start unless `STATE_PG_DB` contains `dev`.
- Tests refuse to run unless `STATE_PG_TEST_DB` contains `test` (tests run Alembic downgrade/upgrade against the test DB).

For authenticated endpoints, also set:

- `ZITADEL_DOMAIN`, `ZITADEL_CLIENT_ID`, `ZITADEL_CLIENT_SECRET`

### Start services

```bash
docker compose up --build
```

Default URLs (from `.env.template`):

- API: `http://localhost:${DEV_PORT}` (docs at `/docs`)
- Jupyter: `http://localhost:${NOTEBOOK_PORT}` (token: `NOTEBOOK_TOKEN`)
- SAQ Web UI: `http://localhost:${SAQ_WEB_PORT}`

### State DB migrations (Alembic)

The state DB schema is managed via Alembic (`alembic/`). On a fresh state DB you need:

1. `pgcrypto` (for `gen_random_uuid()`):

```bash
docker compose exec state-db-dev sh -lc \
  'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "CREATE EXTENSION IF NOT EXISTS \"pgcrypto\";"'
```

2. Run migrations:

```bash
docker compose exec app-dev poetry run alembic upgrade head
```

## Production-ish Compose

`docker-compose.prod.yml` runs the API + worker + Redis and is designed to be attached to an existing reverse-proxy network (`proxy-net`) with Traefik.

Key env vars:

- `DOMAIN` (Traefik host rule)
- `REDIS_DATA_PATH` (Redis persistence path for prod)

## Project structure

- `app/`: application code
  - `app/main.py`: FastAPI app + routes
  - `app/saq_worker.py`: SAQ queue + worker functions
  - `app/calculator/`: calculation implementation
  - `app/db/`: async DB engines, GIS queries, state DB access, throttling
  - `app/auth/`: Zitadel token introspection
- `alembic/`: Alembic migrations for the state DB
- `data/`: lookup tables + curve inputs used by the calculator
- `documentation/calculation.md`: authoritative calculation spec
- `docker-compose.*.yml`, `docker-entrypoint*.sh`: local/prod wiring
- `tests/`: integration/smoke tests
- `sql/`: reference SQL snippets (not the migration source of truth)

## Development conventions

- **Python**: 3.11 + Poetry (`pyproject.toml`)
- **Formatting**: `poetry run black .`
- **Types**: keep/extend existing type hints; avoid introducing untyped public APIs where practical
- **GIS DB safety**: use the throttled helpers in `app/db/gis.py` (don’t open raw GIS sessions without a good reason)

### Devcontainer

`.devcontainer/devcontainer.json` uses `docker-compose.dev.yml` and starts:
`app-dev`, `worker-dev`, `redis-dev`, `state-db-dev`, `state-db-test`.

It also configures common VS Code extensions for Python, Jupyter, Docker, and formatting.

## Tests

Tests are few but cover the most important API flows:

- `tests/api/main_test.py`: calculation lifecycle + output checks + retry/timeout behaviors
- `tests/modules/db/test_gis.py`: smoke tests for GIS query helpers

Running tests requires:

- a running `state-db-test` (started by `docker-compose.dev.yml`), and
- a reachable GIS DB containing the required datasets (tests execute real GIS queries).

Run:

```bash
docker compose exec app-dev poetry run pytest
```
