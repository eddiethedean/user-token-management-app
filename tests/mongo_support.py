"""Ephemeral MongoDB via pytest-mongo. Not a product connector."""

from __future__ import annotations

import os
import shutil
from pathlib import Path


def mongod_executable() -> str | None:
    path = shutil.which("mongod")
    if path:
        return path
    fallback = Path("/usr/bin/mongod")
    return str(fallback) if fallback.is_file() else None


def use_external_mongo() -> bool:
    return os.environ.get("PYTEST_MONGO_NOPROC", "").strip().casefold() in {"1", "true", "yes"}


def mongodb_available() -> bool:
    return use_external_mongo() or mongod_executable() is not None
