import argparse
import os
import sys
from pathlib import Path
from typing import Sequence

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL


def _eprint(message: str) -> None:
    print(message, file=sys.stderr)


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _env(name: str) -> str:
    value = os.getenv(name)
    if value is None or value == "":
        raise RuntimeError(f"Missing required env var: {name}")
    return value


def _build_state_db_url() -> URL:
    user = _env("STATE_PG_USER")
    password = _env("STATE_PG_PASSWORD")
    host = _env("STATE_PG_HOST")
    port = int(os.getenv("STATE_PG_PORT", "5432"))
    database = _env("STATE_PG_DB")
    return URL.create(
        "postgresql+psycopg2",
        username=user,
        password=password,
        host=host,
        port=port,
        database=database,
    )


def _load_alembic_config() -> Config:
    cfg = Config(str(_repo_root() / "alembic.ini"))
    cfg.set_main_option("is_testing", "False")
    return cfg


def _script_heads(cfg: Config) -> Sequence[str]:
    return ScriptDirectory.from_config(cfg).get_heads()


def _db_heads(state_db_url: URL) -> Sequence[str]:
    engine = create_engine(state_db_url)
    with engine.connect() as conn:
        context = MigrationContext.configure(conn)
        return context.get_current_heads()


def _pgcrypto_exists(state_db_url: URL) -> bool:
    engine = create_engine(state_db_url)
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT 1 FROM pg_extension WHERE extname='pgcrypto' LIMIT 1;")
        ).first()
        return row is not None


def _format_heads(heads: Sequence[str]) -> str:
    if not heads:
        return "<none>"
    return ", ".join(heads)


def _explain_pending(*, role: str, db_heads: Sequence[str], required_heads: Sequence[str]) -> None:
    _eprint(f"[{role}] State DB migrations are pending.")
    _eprint(f"[{role}] DB heads: {_format_heads(db_heads)}")
    _eprint(f"[{role}] Required heads: {_format_heads(required_heads)}")
    _eprint(
        f"[{role}] Refusing to start. Run migrations manually (poetry run alembic upgrade head) "
        "or set STATE_DB_MIGRATION_MODE=upgrade for the API container."
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="State DB Alembic migration guard")
    parser.add_argument(
        "--mode",
        choices=("check", "upgrade"),
        default=os.getenv("STATE_DB_MIGRATION_MODE", "check"),
        help="check: refuse to start unless DB is at head; upgrade: run alembic upgrade head",
    )
    parser.add_argument(
        "--role",
        choices=("api", "worker"),
        default="api",
        help="Used only for log messages",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)

    state_db_url = _build_state_db_url()
    cfg = _load_alembic_config()

    try:
        required_heads = list(_script_heads(cfg))
    except Exception as exc:
        _eprint(f"[{args.role}] Failed to read Alembic script heads: {exc}")
        return 1

    try:
        db_heads = list(_db_heads(state_db_url))
    except Exception as exc:
        safe_url = state_db_url.render_as_string(hide_password=True)
        _eprint(f"[{args.role}] Failed to check DB migration state for {safe_url}: {exc}")
        return 1

    if set(db_heads) == set(required_heads):
        return 0

    if args.mode == "check":
        _explain_pending(role=args.role, db_heads=db_heads, required_heads=required_heads)
        return 1

    if args.role != "api":
        _eprint(f"[{args.role}] Refusing to run migrations from non-API role.")
        _explain_pending(role=args.role, db_heads=db_heads, required_heads=required_heads)
        return 1

    if not _pgcrypto_exists(state_db_url):
        _eprint(
            f"[{args.role}] The state DB is missing the required Postgres extension pgcrypto "
            "(needed for gen_random_uuid())."
        )
        _eprint(
            f"[{args.role}] Enable it and retry: CREATE EXTENSION IF NOT EXISTS \"pgcrypto\";"
        )
        return 1

    try:
        command.upgrade(cfg, "head")
    except Exception as exc:
        _eprint(f"[{args.role}] Alembic upgrade failed: {exc}")
        return 1

    try:
        db_heads_after = list(_db_heads(state_db_url))
    except Exception as exc:
        _eprint(f"[{args.role}] Migration finished but could not re-check DB heads: {exc}")
        return 1

    if set(db_heads_after) != set(required_heads):
        _explain_pending(
            role=args.role, db_heads=db_heads_after, required_heads=required_heads
        )
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

