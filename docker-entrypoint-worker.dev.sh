#!/bin/bash
source /root/.bashrc >/dev/null 2>&1

WORKERS="${SAQ_WORKERS_COUNT:-1}"
watchmedo auto-restart --directory='app' --recursive -- saq app.saq_worker.settings --workers "${WORKERS}" --web
