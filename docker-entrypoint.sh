#!/bin/bash
source /root/.bashrc >/dev/null 2>&1

poetry install
# autossh -4 -v -M 0 -o "StrictHostKeyChecking no" -o "ServerAliveInterval 30" -o "ServerAliveCountMax 3" -nNT -L "${PG_PORT}":0.0.0.0:"${REMOTE_DB_PORT}" ${REMOTE_DB_CONNECTION_STRING} -i /root/.ssh/remote_db_rsa &
poetry run uvicorn app.main:app --host 0.0.0.0 --port 80 --reload &
poetry run jupyter notebook --ip='*' --NotebookApp.token="${NOTEBOOK_TOKEN}" --NotebookApp.password='' --allow-root
