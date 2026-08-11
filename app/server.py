from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path
from urllib.parse import urlsplit

import uvicorn

from app.routing import safe_base_path

DEFAULT_RSERVER_URL = Path("/usr/lib/rstudio-server/bin/rserver-url")
_PROXY_PREFIX = re.compile(r"^/proxy/\d+(?P<rest>/.*)?$")


def _normalize_root_path(value: str, *, allow_absolute_url: bool = False) -> str:
    """Strict ASGI root-path sanitizer for Workbench/uvicorn detection."""
    return safe_base_path(value, allow_absolute_url=allow_absolute_url, strict=True)


def detect_root_path(port: int) -> str:
    """Return Workbench's session mount path (``/s/…/p/…``) for Uvicorn and messaging.

    Follows fastapi-workbench's runner: read ``UVICORN_ROOT_PATH`` or ``rserver-url -l``,
    then drop a leading ``/proxy/<port>`` so links stay on the session URL rather than
    Proxied Servers.
    """
    configured = os.environ.get("UVICORN_ROOT_PATH", "").strip()
    if configured:
        root_path = _normalize_root_path(configured, allow_absolute_url=True)
    elif not os.environ.get("RS_SERVER_URL", "").strip():
        return ""
    else:
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

    proxy_match = _PROXY_PREFIX.match(root_path)
    if proxy_match:
        # fastapi-workbench: keep only the session mount under /proxy/<port>/…
        return (proxy_match.group("rest") or "").rstrip("/")
    return root_path


def workbench_launch_hint(port: int) -> str:
    """Best-effort URL or path to print when starting under Workbench."""
    configured = os.environ.get("UVICORN_ROOT_PATH", "").strip()
    if configured:
        # Prefer the injected URL as-is when it is already absolute.
        parsed = urlsplit(configured)
        if parsed.scheme and parsed.netloc:
            path = detect_root_path(port)
            return f"{parsed.scheme}://{parsed.netloc}{path}" if path else configured.rstrip("/")
        path = detect_root_path(port)
        return path or configured.rstrip("/")
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
    os.environ["PORT"] = str(port)
    root_path = detect_root_path(port)

    hint = workbench_launch_hint(port)
    if hint:
        login = hint.rstrip("/") + "/login"
        print(f"Posit Workbench URL: {hint}", flush=True)
        print(f"Open this login URL: {login}", flush=True)
        print(
            "Do not open /proxy/<port>/ and do not combine it with /s/…/p/… "
            "(that yields /proxy/<port>/s/…/login → 404).",
            flush=True,
        )
    else:
        print(f"Local URL: http://{host}:{port}", flush=True)

    # fastapi-workbench sets Uvicorn root_path to the session mount so hrefs and
    # form actions stay under /s/…/p/… instead of inventing /proxy/<port>.
    uvicorn.run(
        "app.main:app",
        host=host,
        port=port,
        reload=reload,
        root_path=root_path,
    )
