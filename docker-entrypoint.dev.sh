#!/bin/bash
if [ -f /root/dev/.bash_history ]; then
    echo "History file exists"
    cp /root/dev/.bash_history /root/.bash_history
else
    cp /root/.bash_history /root/dev/.bash_history
fi
rm -f /root/.bash_history
ln -s /root/dev/.bash_history /root/.bash_history

source /root/.bashrc >/dev/null 2>&1

STATE_DB_NAME_LC="$(echo "${STATE_PG_DB:-}" | tr '[:upper:]' '[:lower:]')"
if [[ -z "${STATE_PG_DB:-}" || "${STATE_DB_NAME_LC}" != *dev* ]]; then
    echo "Refusing to start dev container: STATE_PG_DB must contain 'dev' to avoid accidentally using production." >&2
    echo "Got STATE_PG_DB=${STATE_PG_DB:-<unset>}" >&2
    exit 1
fi

poetry install
# autossh -4 -v -M 0 -o "StrictHostKeyChecking no" -o "ServerAliveInterval 30" -o "ServerAliveCountMax 3" -nNT -L "${PG_PORT}":0.0.0.0:"${REMOTE_DB_PORT}" ${REMOTE_DB_CONNECTION_STRING} -i /root/.ssh/remote_db_rsa &
poetry run uvicorn app.main:app --host 0.0.0.0 --port 80 --reload --reload-exclude=".vscode-server/**/*" --reload-dir="app" &
poetry run jupyter notebook --ip='*' --NotebookApp.token="${NOTEBOOK_TOKEN}" --NotebookApp.password='' --allow-root
