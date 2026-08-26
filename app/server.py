"""HedronPosit-backed application launcher."""

from __future__ import annotations

import os
from collections.abc import Mapping
from urllib.parse import urlsplit

from hedron_posit import WorkbenchConfig
from hedron_posit.runner import run_target

_PUBLIC_BASE_ENV_NAMES = (
    "HEDRON_WORKBENCH_PUBLIC_BASE_URL",
    "FASTAPI_WORKBENCH_PUBLIC_BASE_URL",
    "HEDRON_WORKBENCH_RESOLVED_PUBLIC_BASE",
    "FASTAPI_WORKBENCH_RESOLVED_PUBLIC_BASE",
    "PUBLIC_BASE_URL",
)


def _workbench_public_base_from_environment(
    environ: Mapping[str, str] | None = None,
) -> str | None:
    """Preserve the trusted origin from Workbench's full root-path URL.

    Hedron 0.66 extracts only the mount from a full ``UVICORN_ROOT_PATH``.
    That leaves its encoded-absolute-target guard expecting the loopback
    origin. Promote the Workbench runtime value only when it is a full HTTP(S)
    URL and no operator-supplied public base takes precedence. Hedron remains
    responsible for validating the complete URL and rejecting unsafe forms.
    """
    env = os.environ if environ is None else environ
    if any(str(env.get(name) or "").strip() for name in _PUBLIC_BASE_ENV_NAMES):
        return None
    if not str(env.get("RS_SERVER_URL") or "").strip():
        return None

    candidate = str(env.get("UVICORN_ROOT_PATH") or "").strip()
    parsed = urlsplit(candidate)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
        return None
    return candidate


def run_server(*, host: str, port: int, reload: bool = False) -> None:
    """Discover the Posit deployment before importing and serving the app."""
    run_target(
        "app.main:app",
        config=WorkbenchConfig(
            host=host,
            port=port,
            reload=reload,
            allow_external_bind=host not in {"127.0.0.1", "::1", "localhost"},
            app_target="app.main:app",
            public_base_url=_workbench_public_base_from_environment(),
        ),
    )
