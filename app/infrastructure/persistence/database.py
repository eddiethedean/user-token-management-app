"""Instance-scoped SQLAlchemy runtime.

The legacy module globals remain in ``app.database`` for compatibility with
the CLI and existing fixtures. New app instances can own this object instead
of rebinding process-wide state.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings

_THIN_SESSIONMAKER_LAYER_ATTRIBUTES = frozenset({"__module__", "__doc__"})


def _session_implementation(factory: object) -> type[Session] | None:
    """Return the session class after SQLAlchemy's generated wrapper layers."""

    if not isinstance(factory, sessionmaker):
        return None
    session_class = factory.class_
    while (
        isinstance(session_class, type)
        and session_class.__module__ == "sqlalchemy.orm.session"
        and set(session_class.__dict__) <= _THIN_SESSIONMAKER_LAYER_ATTRIBUTES
        and len(session_class.__bases__) == 1
    ):
        session_class = session_class.__bases__[0]
    return session_class if isinstance(session_class, type) else None


def session_factory_implementation_matches(factory: object, source_factory: object) -> bool:
    """Accept only a source sessionmaker or its behavior-preserving clone."""

    if not isinstance(factory, sessionmaker) or not isinstance(source_factory, sessionmaker):
        return False
    if type(factory) is not type(source_factory):
        return False
    implementation = _session_implementation(factory)
    source_implementation = _session_implementation(source_factory)
    return implementation is not None and implementation is source_implementation


def clone_session_factory(
    source_factory: sessionmaker[Session], engine: Any
) -> sessionmaker[Session]:
    """Clone a source factory without changing its session implementation."""

    options = dict(source_factory.kw)
    options.pop("bind", None)
    binds = options.get("binds")
    if binds:
        options["binds"] = dict(binds)
    session_class = source_factory.class_
    if session_class is not None:
        options["class_"] = session_class
    return type(source_factory)(bind=engine, **options)


def _options(settings: Settings) -> dict[str, Any]:
    url = settings.database_url
    if url.startswith("sqlite"):
        return {"connect_args": {"check_same_thread": False, "timeout": 30}}
    return {
        "pool_pre_ping": True,
        "pool_size": settings.db_pool_size,
        "max_overflow": settings.db_max_overflow,
        "pool_timeout": settings.db_pool_timeout,
        "pool_recycle": settings.db_pool_recycle,
    }


@dataclass
class DatabaseRuntime:
    """Own one engine and session factory, with an explicit close boundary."""

    engine: Any
    sessions: sessionmaker[Session]

    @classmethod
    def from_settings(cls, settings: Settings) -> DatabaseRuntime:
        engine = create_engine(settings.database_url, **_options(settings))
        if engine.dialect.name == "sqlite":

            @event.listens_for(engine, "connect")
            def _enable_sqlite_foreign_keys(dbapi_connection, _) -> None:
                cursor = dbapi_connection.cursor()
                try:
                    cursor.execute("PRAGMA foreign_keys=ON")
                    cursor.execute("PRAGMA busy_timeout=30000")
                finally:
                    cursor.close()

        return cls(
            engine=engine,
            sessions=sessionmaker(bind=engine, autoflush=False, expire_on_commit=False),
        )

    @contextmanager
    def session(self) -> Iterator[Session]:
        db = self.sessions()
        try:
            yield db
        finally:
            db.close()

    def dispose(self) -> None:
        self.engine.dispose()


def create_database(settings: Settings) -> DatabaseRuntime:
    return DatabaseRuntime.from_settings(settings)
