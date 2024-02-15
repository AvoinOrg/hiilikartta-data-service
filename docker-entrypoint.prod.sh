#!/bin/bash
source /root/.bashrc >/dev/null 2>&1

poetry install --no-dev
poetry run gunicorn -k uvicorn.workers.UvicornWorker app.main:app --bind 0.0.0.0:80
