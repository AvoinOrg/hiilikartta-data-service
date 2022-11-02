FROM python:3.9.15

WORKDIR /app

RUN pip install poetry

COPY ./pyproject.toml ./poetry.lock* /app/

RUN poetry install --no-dev