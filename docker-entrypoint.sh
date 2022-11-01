#!/bin/bash
poetry install
poetry run uvicorn app.main:app --reload --host 0.0.0.0 --port 80 --reload-include "app/*" --reload-exclude "*/__pycache__"