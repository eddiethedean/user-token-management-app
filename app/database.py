from __future__ import annotations

import fcntl
from collections.abc import Generator, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import create_engine, event
from sqlalchemy.engine import URL, make_url
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from starlette.requests import Request

from app.config import Settings, get_settings
from app.infrastructure.persistence.database import (
    DatabaseRuntime,
    clone_session_factory,
    create_database,
)


class Base(DeclarativeBase):
    pass


def _engine_options(database_url: str, settings: Settings | None = None) -> dict:
    if database_url.startswith("sqlite"):
        return {"connect_args": {"check_same_thread": False, "timeout": 30}}
    cfg = settings or get_settings()
    return {
        "pool_pre_ping": True,
        "pool_size": cfg.db_pool_size,
        "max_overflow": cfg.db_max_overflow,
        "pool_timeout": cfg.db_pool_timeout,
        "pool_recycle": cfg.db_pool_recycle,
    }


settings = get_settings()
engine = create_engine(settings.database_url, **_engine_options(settings.database_url, settings))


if engine.dialect.name == "sqlite":

    @event.listens_for(engine, "connect")
    def _enable_sqlite_foreign_keys(dbapi_connection, _) -> None:
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA busy_timeout=30000")
        finally:
            cursor.close()


@contextmanager
def sqlite_worker_lock(database_url: str, worker_kind: str) -> Iterator[None]:
    """Allow only one worker of each kind to use a SQLite application database."""
    url = make_url(database_url)
    if url.drivername not in {"sqlite", "sqlite+pysqlite"}:
        yield
        return
    if not url.database or url.database == ":memory:":
        raise RuntimeError("SQLite workers require a file-backed application database")
    database_path = Path(url.database).expanduser()
    if not database_path.is_absolute():
        database_path = Path.cwd() / database_path
    lock_path = database_path.with_name(f".{database_path.name}.{worker_kind}.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+") as lock_file:
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError(
                f"A SQLite {worker_kind} worker is already running for {database_path}"
            ) from exc
        try:
            yield
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
_ACTIVE_SESSION_FACTORY: ContextVar[sessionmaker[Session] | None] = ContextVar(
    "access_registry_active_session_factory", default=None
)


def bind_runtime(runtime: Any):
    """Bind an app-owned session factory for the current request/task context."""

    return _ACTIVE_SESSION_FACTORY.set(runtime.sessions if runtime is not None else None)


def unbind_runtime(token) -> None:
    _ACTIVE_SESSION_FACTORY.reset(token)


def current_session_factory():
    """Return the request-bound factory or the compatibility factory."""

    return _ACTIVE_SESSION_FACTORY.get() or SessionLocal


def legacy_session_factory():
    """Return the process-global factory without consulting request context."""

    return SessionLocal


def legacy_database_engine():
    """Return the process-global engine without consulting request context."""

    return engine


def legacy_database_url() -> URL:
    """Return the database URL owned by the compatibility engine."""

    return engine.url


def legacy_session_engine():
    """Return the engine bound to the compatibility session factory."""

    return getattr(SessionLocal, "kw", {}).get("bind")


def stable_session_factory():
    """Clone the compatibility factory so a runtime owns its engine binding."""

    return clone_session_factory(SessionLocal, engine)


def create_runtime(settings: Settings | None = None) -> DatabaseRuntime:
    """Create an independent database runtime for a composed app instance."""

    return create_database(settings or get_settings())


def get_db(request: Request) -> Generator[Session, None, None]:
    """Yield the app-owned session when composed, preserving CLI compatibility."""

    runtime = getattr(getattr(request, "state", None), "execution", None)
    if runtime is None:
        lifecycle = getattr(getattr(request, "app", None), "state", None)
        if getattr(lifecycle, "runtime_lifecycle", None) != "fixture":
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE)
        runtime = getattr(lifecycle, "execution", None)
    if runtime is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE)
    factory = runtime.sessions
    db = factory()
    try:
        yield db
    finally:
        db.close()
