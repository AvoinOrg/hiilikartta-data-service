from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from sqlalchemy.exc import DBAPIError, SQLAlchemyError, TimeoutError as SQLAlchemyTimeoutError


@dataclass(frozen=True)
class GisRetryLaterError(Exception):
    """
    Raised when work should be retried later (e.g. GIS DB capacity constraints).

    This is meant to be caught by the SAQ worker so it can re-enqueue the job
    with a delay, without counting as a calculation failure.
    """

    message: str
    retry_in_seconds: float = 60.0

    def __str__(self) -> str:  # pragma: no cover
        return self.message


class GisOperationTimedOutError(Exception):
    """
    Raised when a GIS DB operation exceeds its allowed runtime.

    This should be treated as a permanent failure for the current feature.
    """


def _combined_message(exc: BaseException) -> str:
    parts = [str(exc)]
    orig = getattr(exc, "orig", None)
    if orig is not None:
        parts.append(str(orig))
    return " ".join(part for part in parts if part).lower()


def is_sqlalchemy_pool_timeout(exc: BaseException) -> bool:
    return isinstance(exc, SQLAlchemyTimeoutError) or "queuepool" in _combined_message(exc)


def is_db_capacity_error(exc: BaseException) -> bool:
    msg = _combined_message(exc)
    return any(
        phrase in msg
        for phrase in (
            "too many clients",
            "remaining connection slots",
            "could not connect",
            "connection refused",
            "server closed the connection",
            "connection reset",
            "connection terminated",
            "connection error",
            "terminating connection",
        )
    ) or is_sqlalchemy_pool_timeout(exc)


def is_statement_timeout(exc: BaseException) -> bool:
    msg = _combined_message(exc)
    if "statement timeout" in msg:
        return True

    if isinstance(exc, DBAPIError):
        orig = exc.orig
        sqlstate = getattr(orig, "sqlstate", None)
        if sqlstate == "57014" and "canceling statement" in msg:
            return True

    return False


def is_retryable_sqlalchemy_error(exc: BaseException) -> bool:
    return isinstance(exc, SQLAlchemyError) and is_db_capacity_error(exc)

