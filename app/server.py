from __future__ import annotations

import os
import subprocess
from pathlib import Path
from urllib.parse import urlsplit

import uvicorn

from app.routing import safe_base_path

DEFAULT_RSERVER_URL = Path("/usr/lib/rstudio-server/bin/rserver-url")


def _normalize_root_path(value: str, *, allow_absolute_url: bool = False) -> str:
    """Strict ASGI root-path sanitizer for Workbench/uvicorn detection."""
    return safe_base_path(value, allow_absolute_url=allow_absolute_url, strict=True)


def detect_root_path(port: int) -> str:
    """Return Workbench's session mount path for messaging and tests.

    Workbench may export ``UVICORN_ROOT_PATH`` as either an ASGI path
    (``/s/…/p/…``) or the full externally visible URL that ``rserver-url -l``
    prints. The ASGI server intentionally does **not** receive this value as
    ``root_path``: mounting at ``/s/…`` breaks Proxied Servers (``/proxy/<port>/``)
    because absolute redirects get double-prefixed. Request mounts are derived
    per request by :func:`app.routing.normalize_workbench_scope`.
    """
    configured = os.environ.get("UVICORN_ROOT_PATH", "").strip()
    if configured:
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
    root_path = _normalize_root_path(result.stdout, allow_absolute_url=True)
    if not root_path:
        raise RuntimeError("Posit Workbench returned an empty proxy root path.")
    return root_path


def workbench_launch_hint(port: int) -> str:
    """Best-effort URL or path to print when starting under Workbench."""
    configured = os.environ.get("UVICORN_ROOT_PATH", "").strip()
    if configured:
        return configured.rstrip("/")
    if not os.environ.get("RS_SERVER_URL", "").strip():
        return ""
    try:
        path = detect_root_path(port)
    except RuntimeError:
        return ""
    server = os.environ.get("RS_SERVER_URL", "").strip().rstrip("/")
    if not path:
        return server
    parsed = urlsplit(server)
    if parsed.scheme and parsed.netloc:
        return f"{parsed.scheme}://{parsed.netloc}{path}"
    return path


def run_server(*, host: str, port: int, reload: bool = False) -> None:
    # Expose the listen port so request middleware can synthesize /proxy/<port>
    # when Workbench Proxied Servers strips that prefix before forwarding.
    os.environ["PORT"] = str(port)

    hint = workbench_launch_hint(port)
    if hint:
        print(f"Posit Workbench URL: {hint}", flush=True)
        print(
            f"Prefer that session URL. Proxied Servers (/proxy/{port}/) also works after redirects.",
            flush=True,
        )
    else:
        print(f"Local URL: http://{host}:{port}", flush=True)

    # Leave Uvicorn root_path empty. WorkbenchPathMiddleware derives the mount
    # from each request so both /s/…/p/… and /proxy/<port>/ entry points work.
    uvicorn.run(
        "app.main:app",
        host=host,
        port=port,
        reload=reload,
        root_path="",
    )
