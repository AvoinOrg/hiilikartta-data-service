#!/bin/bash
if [ "$IS_PRODUCTION" = 1 ]; then
    poetry install --no-dev
    poetry run uvicorn app.main:app --host 0.0.0.0 --port 80
else
    poetry install
    poetry run uvicorn app.main:app --reload --host 0.0.0.0 --port 80 --reload-include "app/*" --reload-exclude "*/__pycache__" &
    poetry run jupyter notebook --ip='*' --NotebookApp.token="${NOTEBOOK_TOKEN}" --NotebookApp.password='' --allow-root -i 0.0.0.0 --allow_remote_access=true
fi
