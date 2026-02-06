#!/bin/bash
source /root/.bashrc >/dev/null 2>&1

poetry install --without dev
poetry run python -m app.db.migration_guard --mode check --role worker || exit 1

WORKERS="${SAQ_WORKERS_COUNT:-25}"
poetry run saq --workers "${WORKERS}" app.saq_worker.settings --web
