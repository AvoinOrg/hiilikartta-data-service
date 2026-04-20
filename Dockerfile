FROM python:3.11

SHELL ["/bin/bash", "-l", "-c"]

WORKDIR /app

ENV POETRY_NO_INTERACTION=1

RUN apt-get update && \
    pip install poetry && \
    touch /root/.bash_history &&\
    echo 'PS0="$PS0"'"'"'$(history -a)'"'" >> /root/.bashrc &&\
    echo 'PROMPT_COMMAND="history -n; $PROMPT_COMMAND"' >> /root/.bashrc &&\
    printf "  PasswordAuthentication yes\n  KbdInteractiveAuthentication yes" >> /etc/ssh/ssh_config &&\
    sed -i '1,6d' /root/.bashrc &&\
    echo "source \"\$(poetry env info --path)/bin/activate\"" >> /root/.bashrc

COPY pyproject.toml poetry.lock ./
RUN poetry install --without dev --no-root

COPY alembic.ini ./
COPY alembic ./alembic
COPY app ./app
COPY docker-entrypoint.prod.sh docker-entrypoint-worker.prod.sh ./

RUN mkdir -p data && chmod +x docker-entrypoint.prod.sh docker-entrypoint-worker.prod.sh
