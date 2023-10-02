#!/bin/bash
source /root/.bashrc >/dev/null 2>&1

poetry install --no-dev
poetry run uvicorn app.main:app --host 0.0.0.0 --port 80
