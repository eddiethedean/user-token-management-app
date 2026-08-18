"""Ephemeral PostgreSQL via testing.postgresql for connector tests."""

from __future__ import annotations

import shutil
from typing import Any

import pytest

from app.config import Settings


def postgres_binaries() -> dict[str, str]:
    """Resolve initdb/postgres from PATH so Homebrew prefixes work."""
    found: dict[str, str] = {}
    for name in ("initdb", "postgres"):
        path = shutil.which(name)
        if path:
            found[name] = path
    return found


def postgres_available() -> bool:
    try:
        from testing.postgresql import find_program

        binaries = postgres_binaries()
        if "postgres" not in binaries:
            find_program("postgres", ["bin"])
        if "initdb" not in binaries:
            find_program("initdb", ["bin"])
        return True
    except Exception:
        return False


def credentials_from(postgresql: Any) -> dict[str, str]:
    dsn = postgresql.dsn()
    return {
        "host": str(dsn["host"]),
        "port": str(dsn["port"]),
        "database": str(dsn["database"]),
        "username": str(dsn["user"]),
        "password": str(dsn.get("password") or ""),
        "sslmode": "disable",
    }


def connector_settings() -> Settings:
    return Settings(_env_file=None, data_mover_mode="demo")


requires_postgres = pytest.mark.skipif(
    not postgres_available(),
    reason="PostgreSQL server binaries (initdb, postgres) are not on PATH",
)
