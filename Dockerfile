FROM python:3.9.15

WORKDIR /app

COPY ./pyproject.toml ./poetry.lock* /app/

RUN pip install poetry && \
    poetry config virtualenvs.in-project true && \
    poetry install --no-dev