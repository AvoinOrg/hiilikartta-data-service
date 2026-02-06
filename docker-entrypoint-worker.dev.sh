#!/bin/bash
source /root/.bashrc >/dev/null 2>&1

STATE_DB_NAME_LC="$(echo "${STATE_PG_DB:-}" | tr '[:upper:]' '[:lower:]')"
if [[ -z "${STATE_PG_DB:-}" || "${STATE_DB_NAME_LC}" != *dev* ]]; then
    echo "Refusing to start dev worker: STATE_PG_DB must contain 'dev' to avoid accidentally using production." >&2
    echo "Got STATE_PG_DB=${STATE_PG_DB:-<unset>}" >&2
    exit 1
fi

WORKERS="${SAQ_WORKERS_COUNT:-1}"
watchmedo auto-restart --directory='app' --recursive -- saq app.saq_worker.settings --workers "${WORKERS}" --web
