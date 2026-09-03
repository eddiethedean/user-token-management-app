"""HedronPosit-backed application launcher."""

from __future__ import annotations

import os

from hedron_posit import WorkbenchConfig
from hedron_posit.runner import run_target

_EXPLICIT_WORKBENCH_PUBLIC_BASE_ENV_NAMES = (
    "HEDRON_WORKBENCH_PUBLIC_BASE_URL",
    "FASTAPI_WORKBENCH_PUBLIC_BASE_URL",
    "HEDRON_WORKBENCH_RESOLVED_PUBLIC_BASE",
    "FASTAPI_WORKBENCH_RESOLVED_PUBLIC_BASE",
)


def run_server(*, host: str, port: int, reload: bool = False, discover: bool = False) -> None:
    """Run Data Mover through HedronPosit's supported launcher.

    ``run_target`` owns Workbench discovery, root-path normalization, environment
    handoff, cookie-path adaptation, and application wrapping. Keeping this
    adapter declarative avoids duplicating those rules in the application.
    """

    config = WorkbenchConfig(
        host=host,
        port=port,
        reload=reload,
        allow_external_bind=host not in {"127.0.0.1", "::1", "localhost"},
        app_target="app.main:app",
    )

    # A Workbench restart can leave UVICORN_ROOT_PATH pointing at the previous
    # session while the explicit public-base handoff has already been refreshed.
    # Let the explicit handoff win; otherwise Hedron rejects the two mounts
    # before --discover has a chance to start the application.
    stale_runtime_root = None
    runtime_root = os.environ.get("UVICORN_ROOT_PATH", "").strip()
    if any(
        os.environ.get(name, "").strip() for name in _EXPLICIT_WORKBENCH_PUBLIC_BASE_ENV_NAMES
    ) and runtime_root.startswith(("http://", "https://")):
        # A path-only UVICORN_ROOT_PATH is still useful when the explicit base
        # supplies only the Workbench origin. Remove only the absolute URL form,
        # which is the stale handoff that can conflict with the refreshed base.
        stale_runtime_root = os.environ.pop("UVICORN_ROOT_PATH")
    try:
        run_target("app.main:app", config=config, discover=discover)
    finally:
        if stale_runtime_root is not None:
            os.environ["UVICORN_ROOT_PATH"] = stale_runtime_root
