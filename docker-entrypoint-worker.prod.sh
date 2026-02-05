#!/bin/bash
source /root/.bashrc >/dev/null 2>&1

WORKERS="${SAQ_WORKERS_COUNT:-5}"
poetry run saq --workers "${WORKERS}" app.saq_worker.settings --web
