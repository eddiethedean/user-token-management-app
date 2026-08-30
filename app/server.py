"""HedronPosit-backed application launcher."""

from __future__ import annotations

import os
from collections.abc import Mapping, MutableMapping
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


def run_server(*, host: str, port: int, reload: bool = False) -> None:
    """Discover the Posit deployment before importing and serving the app."""
    public_base_url = _workbench_public_base_from_environment()
    workbench_mount = _prepare_workbench_environment()
    run_target(
        "app.main:app",
        config=WorkbenchConfig(
            host=host,
            port=port,
            reload=reload,
            allow_external_bind=host not in {"127.0.0.1", "::1", "localhost"},
            app_target="app.main:app",
            mount=workbench_mount,
            public_base_url=public_base_url,
        ),
    )
