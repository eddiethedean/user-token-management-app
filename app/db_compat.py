"""SQLAlchemy helpers shared by SQLite (dev/Workbench) and PostgreSQL (production).

All statements here are SQLAlchemy Core/ORM — no raw SQL strings. Dialect differences:

- **Upsert** uses the dialect-specific ``insert()`` (PostgreSQL ``ON CONFLICT`` /
  SQLite ``ON CONFLICT``). Both are expressed through SQLAlchemy, not hand-written SQL.
- **RETURNING** is used only on PostgreSQL. SQLite always follows write-then-``select``
  so older Workbench ``libsqlite`` builds (pre-3.35) keep working.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, TypeVar

from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.engine import CursorResult
from sqlalchemy.orm import Session

T = TypeVar("T")


def dialect_name(db: Session) -> str:
    return db.get_bind().dialect.name


def require_supported_dialect(db: Session) -> str:
    name = dialect_name(db)
    if name not in {"postgresql", "sqlite"}:
        raise RuntimeError(f"Unsupported database dialect {name!r}; use sqlite or postgresql.")
    return name


def supports_returning(db: Session) -> bool:
    """PostgreSQL supports ``RETURNING``; SQLite paths avoid it for Workbench compatibility."""
    return dialect_name(db) == "postgresql"


def insert_for(db: Session, table: Any) -> Any:
    """Return a dialect-appropriate SQLAlchemy ``INSERT`` for upsert / insert-ignore."""
    name = require_supported_dialect(db)
    if name == "postgresql":
        return postgresql_insert(table)
    return sqlite_insert(table)


def execute_dml(db: Session, statement: Any) -> CursorResult[Any]:
    """Execute a SQLAlchemy DML statement and return the cursor result."""
    return db.execute(statement)  # type: ignore[return-value]


def scalar_returning(
    db: Session,
    statement: Any,
    returning_column: Any,
    *,
    fallback: Callable[[], T | None],
) -> T | None:
    """Run DML with ``RETURNING`` on PostgreSQL, or ``fallback()`` after a successful write.

    ``fallback`` should issue a SQLAlchemy ``select`` (via ``db.scalar``) for the value that
    ``RETURNING`` would have produced on PostgreSQL.
    """
    if supports_returning(db):
        return db.scalar(statement.returning(returning_column))

    result = execute_dml(db, statement)
    if not result.rowcount:
        return None
    return fallback()
