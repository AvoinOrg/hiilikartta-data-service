#!/bin/bash
source /root/.bashrc >/dev/null 2>&1
cd /app
exec poetry run watchmedo auto-restart --directory app --recursive -- \
    saq app.saq_worker.settings -- --workers 3 --web