from __future__ import annotations

import os
import subprocess
from pathlib import Path

import uvicorn

from app.routing import safe_base_path

DEFAULT_RSERVER_URL = Path("/usr/lib/rstudio-server/bin/rserver-url")


def _normalize_root_path(value: str, *, allow_absolute_url: bool = False) -> str:
    """Strict ASGI root-path sanitizer for Workbench/uvicorn detection."""
    return safe_base_path(value, allow_absolute_url=allow_absolute_url, strict=True)


def detect_root_path(port: int) -> str:
    """Discover Workbench's dynamic proxy path while remaining a no-op elsewhere.

    Workbench may export ``UVICORN_ROOT_PATH`` as either an ASGI path
    (``/s/…/p/…``) or the full externally visible URL that ``rserver-url -l``
    prints. Uvicorn only accepts the path component.
    """
    configured = os.environ.get("UVICORN_ROOT_PATH", "").strip()
    if configured:
        # Accept absolute URLs here: recent Workbench sessions inject the full
        # proxied URL into UVICORN_ROOT_PATH, matching rserver-url output.
        return _normalize_root_path(configured, allow_absolute_url=True)
    if not os.environ.get("RS_SERVER_URL", "").strip():
        return ""

    executable = Path(os.environ.get("RSERVER_URL_BIN", str(DEFAULT_RSERVER_URL)))
    try:
        result = subprocess.run(
            [str(executable), "-l", str(port)],
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError(
            "Posit Workbench was detected, but its proxy root path could not be resolved. "
            "Set UVICORN_ROOT_PATH or verify the rserver-url binary."
        ) from exc
    # Posit's documented `rserver-url -l` output is a full externally visible URL.
    # Uvicorn needs only its path component as the ASGI root path.
    root_path = _normalize_root_path(result.stdout, allow_absolute_url=True)
    if not root_path:
        raise RuntimeError("Posit Workbench returned an empty proxy root path.")
    return root_path


def run_server(*, host: str, port: int, reload: bool = False) -> None:
    root_path = detect_root_path(port)
    uvicorn.run(
        "app.main:app",
        host=host,
        port=port,
        reload=reload,
        root_path=root_path,
    )
