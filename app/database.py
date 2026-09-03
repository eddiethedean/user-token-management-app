from __future__ import annotations

import fcntl
from collections.abc import Generator, Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.engine import make_url
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import Settings, get_settings


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


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
