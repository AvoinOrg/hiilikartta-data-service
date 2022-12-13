#!/bin/bash
if [ "$IS_PRODUCTION" = 1 ]; then
    poetry install --no-dev
    poetry run uvicorn app.main:app --host 0.0.0.0 --port 80
else
    poetry install
    autossh -M 0 -o "ServerAliveInterval 30" -o "ServerAliveCountMax 3" -nNT -L 5432:localhost:"${REMOTE_DB_PORT}" ${REMOTE_DB_CONNECTION_STRING} -i /root/.ssh/id_rsa &
    poetry run uvicorn app.main:app --reload --host 0.0.0.0 --port 80 --reload-exclude "*" --reload-include "app/**/*" &
    poetry run jupyter notebook --ip='*' --NotebookApp.token="${NOTEBOOK_TOKEN}" --NotebookApp.password='' --allow-root
fi
