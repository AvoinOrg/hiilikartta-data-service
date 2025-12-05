import os
from logging.config import fileConfig

import alembic_postgresql_enum
from alembic import context
from dotenv import load_dotenv
from sqlalchemy import engine_from_config, pool

from app.db.models.base import Base
from app.db.models.plan import Plan

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

load_dotenv()
env_vars = os.environ

# Interpret the config file for Python logging.
# This line sets up loggers basically.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# add your model's MetaData object here
target_metadata = Base.metadata

is_testing = config.get_main_option("is_testing", "False")


def _db_url():
    if is_testing == "True":
        username = env_vars.get("STATE_PG_TEST_USER")
        password = env_vars.get("STATE_PG_TEST_PASSWORD")
        host = env_vars.get("STATE_PG_TEST_REAL_HOST")
        port = env_vars.get("STATE_PG_TEST_PORT")
        database = env_vars.get("STATE_PG_TEST_DB")
    else:
        username = env_vars["STATE_PG_USER"]
        password = env_vars["STATE_PG_PASSWORD"]
        host = env_vars.get("STATE_PG_REAL_HOST",)
        port = env_vars.get("STATE_PG_PORT")
        database = env_vars["STATE_PG_DB"]

    return f"postgresql+psycopg2://{username}:{password}@{host}:{port}/{database}"


config.set_main_option("sqlalchemy.url", _db_url())


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode."""

    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode."""

    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
