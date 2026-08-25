"""Development-only console traces for Workbench URL troubleshooting."""

from __future__ import annotations

import os
from typing import Any

from app.config import get_settings

_STATIC_PREFIXES = ("/assets/", "/app-assets/", "/hedron-static/", "/hedron-assets/", "/favicon")


def is_dev_trace_enabled() -> bool:
    """True when ``APP_ENV=development`` outside pytest.

    Forced off under pytest (even if `.env` says development) so unit tests stay quiet.
    Set ``ACCESS_REGISTRY_DEV_TRACE=1`` to force traces on for a local debug session.
    """
    force = os.environ.get("ACCESS_REGISTRY_DEV_TRACE", "").strip().casefold()
    if force in {"1", "true", "yes", "on"}:
        return True
    if force in {"0", "false", "no", "off"}:
        return False
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return False
    env = os.environ.get("APP_ENV", "").strip().casefold()
    if env:
        return env == "development"
    return get_settings().app_env == "development"


def is_static_scope_path(path: str) -> bool:
    """Return whether ``path`` looks like a static asset request."""
    route = path if path.startswith("/") else f"/{path}"
    return any(route.startswith(prefix) for prefix in _STATIC_PREFIXES)


def dev_trace(event: str, **fields: Any) -> None:
    """Print a single-line diagnostic when development tracing is enabled."""
    if not is_dev_trace_enabled():
        return
    parts = [f"[access-registry:dev] {event}"]
    for key, value in fields.items():
        parts.append(f"{key}={value!r}")
    print(" ".join(parts), flush=True)
