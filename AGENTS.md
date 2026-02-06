# Agent Notes (hiilikartta-data-service)

This repository is a **FastAPI + background worker** service used by Hiilikartta / climate-map for carbon calculations on zoning-plan polygons.

If you change anything that affects **runtime behavior, configuration, APIs, database schema, or calculation logic**, update **both** `README.md` and `AGENTS.md` (and `documentation/calculation.md` when the calculation changes).

## What the service does

- Accepts a zipped vector dataset (polygons) via `POST /calculation`
- Stores the uploaded plan and calculation state in a Postgres “state DB”
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
- `docker-compose.prod.yml`: prod-ish stack (expects external `proxy-net` and Traefik labels)

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
- State DB pgcrypto (fresh DB):

```bash
docker compose exec state-db-dev sh -lc \
  'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "CREATE EXTENSION IF NOT EXISTS \"pgcrypto\";"'
```

## Configuration (env vars)

Authoritative list: `.env.template`. Highlights:

- **Ports (compose)**: `APP_PORT`, `NOTEBOOK_PORT`, `SAQ_WEB_PORT`
- **SAQ workers**: `SAQ_WORKERS_COUNT`
- **Redis**: `REDIS_HOST` (set by compose), `REDIS_PORT` (default 6379)
- **State DB**: `STATE_PG_*` (dev), `STATE_PG_TEST_*` (tests)
- **GIS DB**: `GIS_PG_*` (must point to a PostGIS DB with required datasets)
- **Auth (Zitadel)**: `ZITADEL_DOMAIN`, `ZITADEL_CLIENT_ID`, `ZITADEL_CLIENT_SECRET`
- **GIS throttling**: `GIS_LOCAL_MAX_CONCURRENT`, `GIS_DISTRIBUTED_MAX_CONCURRENT`, `GIS_SLOT_TTL`, `GIS_STATEMENT_TIMEOUT_SECONDS`
- **Analytics (optional, prod)**: `UMAMI_ENABLED`, `UMAMI_HOST_URL`, `UMAMI_WEBSITE_ID`

## Data dependencies (don’t “simplify” away)

### GIS DB tables/rasters

Used by `app/db/gis.py` and described in `documentation/calculation.md`:

- `hiilikartta_kasvillisuudenhiili_2021_tcha`
- `hiilikartta_maaperanhiili_2023_tcha`
- `luke_mvmisegmentit_id_kokomaa`
- `luke_mvmisegmentit_muuttujat_kokomaa`
- `maakunta` (`geom`, `natcode`)

### Repo files under `data/`

Loaded into memory on API startup (`app/utils/data_loader.py`):

- `data/BiomassCurves.txt`
- `data/SoilCurves.txt`
- `data/aluekertoimet.csv`
- `data/Hiilikartta_Kasvillisuuden_ja_maaperan_hiilensidonta_kayttotarkoitusluokittain.csv`

## Gotchas / agent tips

- **Worker is required**: the API only enqueues jobs; without `worker-*` nothing completes.
- **Responses are gzip**: read endpoints set `Content-Encoding: gzip`; use `curl --compressed` in examples.
- **GIS is the bottleneck**: prefer the throttled helpers in `app/db/gis.py` (they apply local + Redis semaphores and `statement_timeout`).
- **Timeouts aren’t fatal to the whole plan**: single-feature GIS timeouts are skipped and calculation continues.
- **State DB schema is Alembic-managed**: update `app/db/models/*` + create migrations; don’t hand-edit `sql/state/create.sql` and expect it to be authoritative.

## Where to change things

- HTTP API / auth: `app/main.py`, `app/auth/validator.py`
- Worker behavior / retries: `app/saq_worker.py`, `app/db/errors.py`
- Calculation logic: `app/calculator/calculator.py` (keep `documentation/calculation.md` in sync)
- GIS SQL: `app/db/gis.py` (be careful: performance + PostGIS semantics)
- State DB access/model: `app/db/plan.py`, `app/db/models/plan.py`, `alembic/`
- Devcontainer: `.devcontainer/devcontainer.json`
