"""HedronPosit-backed application launcher."""

from __future__ import annotations

import inspect
import os
from collections.abc import Mapping, MutableMapping
from typing import Any
from urllib.parse import urlsplit

from hedron_posit import WorkbenchConfig
from hedron_posit.resolve import resolve_deployment
from hedron_posit.runner import run_target

_PUBLIC_BASE_ENV_NAMES = (
    "HEDRON_WORKBENCH_PUBLIC_BASE_URL",
    "FASTAPI_WORKBENCH_PUBLIC_BASE_URL",
    "HEDRON_WORKBENCH_RESOLVED_PUBLIC_BASE",
    "FASTAPI_WORKBENCH_RESOLVED_PUBLIC_BASE",
    "PUBLIC_BASE_URL",
)

_WORKBENCH_HANDOFF_ENV_NAMES = (
    "HEDRON_WORKBENCH_PUBLIC_BASE_URL",
    "FASTAPI_WORKBENCH_PUBLIC_BASE_URL",
    "HEDRON_WORKBENCH_RESOLVED_PUBLIC_BASE",
    "FASTAPI_WORKBENCH_RESOLVED_PUBLIC_BASE",
    "HEDRON_WORKBENCH_MOUNT",
    "FASTAPI_WORKBENCH_MOUNT",
    "HEDRON_WORKBENCH_RESOLVED_MOUNT",
    "FASTAPI_WORKBENCH_RESOLVED_MOUNT",
    "HEDRON_ROOT_PATH",
    "FASTAPI_WORKBENCH_ROOT_PATH",
    "UVICORN_ROOT_PATH",
)


def _workbench_public_base_from_environment(
    environ: Mapping[str, str] | None = None,
) -> str | None:
    """Preserve the trusted origin from Workbench's full root-path URL.

    Hedron 1.0 extracts only the mount from a full ``UVICORN_ROOT_PATH``.
    That leaves its encoded-absolute-target guard expecting the loopback
    origin. Promote the Workbench runtime value only when it is a full HTTP(S)
    URL and no operator-supplied public base takes precedence. Hedron remains
    responsible for validating the complete URL and rejecting unsafe forms.
    """
    env = os.environ if environ is None else environ
    candidate = _workbench_runtime_url(env)
    if candidate is None:
        return None
    if any(str(env.get(name) or "").strip() for name in _PUBLIC_BASE_ENV_NAMES):
        return None
    return candidate


def _workbench_runtime_url(environ: Mapping[str, str]) -> str | None:
    """Return Workbench's full root URL when the interactive runtime supplied one."""
    if not str(environ.get("RS_SERVER_URL") or "").strip():
        return None

    candidate = str(environ.get("UVICORN_ROOT_PATH") or "").strip()
    parsed = urlsplit(candidate)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
        return None
    return candidate


def _prepare_workbench_environment(
    environ: MutableMapping[str, str] | None = None,
) -> str | None:
    """Validate Workbench's full URL and keep it out of Uvicorn's CLI environment."""
    env = os.environ if environ is None else environ
    candidate = _workbench_runtime_url(env)
    if candidate is None:
        return None

    # Resolve through Hedron before changing the handoff. This retains its
    # validation for credentials, queries, fragments, unsafe paths, and ports.
    resolved = resolve_deployment(WorkbenchConfig(public_base_url=candidate), environ={})
    env.pop("UVICORN_ROOT_PATH", None)
    return resolved.browser_mount or "/"


def _has_workbench_handoff(environ: Mapping[str, str] | None = None) -> bool:
    env = os.environ if environ is None else environ
    return bool(
        str(env.get("RS_SERVER_URL") or "").strip()
        or any(str(env.get(name) or "").strip() for name in _WORKBENCH_HANDOFF_ENV_NAMES)
    )


def run_server(*, host: str, port: int, reload: bool = False, discover: bool = False) -> None:
    """Discover the Posit deployment before importing and serving the app."""
    public_base_url = _workbench_public_base_from_environment()
    workbench_mount = _prepare_workbench_environment()
    config = WorkbenchConfig(
        host=host,
        port=port,
        reload=reload,
        allow_external_bind=host not in {"127.0.0.1", "::1", "localhost"},
        app_target="app.main:app",
        mount=workbench_mount,
        public_base_url=public_base_url,
    )
    runner: Any = run_target
    supports_explicit_discovery = "discover" in inspect.signature(runner).parameters
    if discover and supports_explicit_discovery:
        runner("app.main:app", config=config, discover=True)
        return
    if discover and not _has_workbench_handoff():
        raise RuntimeError(
            "Explicit Workbench discovery requires a hedron-posit release with "
            "run_target(..., discover=True), or a validated Workbench public-base handoff."
        )
    runner(
        "app.main:app",
        config=config,
    )
