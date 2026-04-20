#!/bin/bash
source /root/.bashrc >/dev/null 2>&1

poetry run python -m app.db.migration_guard --mode "${STATE_DB_MIGRATION_MODE:-check}" --role api || exit 1
poetry run gunicorn -k uvicorn.workers.UvicornWorker app.main:app --bind 0.0.0.0:80
