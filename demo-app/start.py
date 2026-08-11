"""Start the demo locally or through Posit Workbench's session proxy."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from urllib.parse import urlsplit

import uvicorn

DEFAULT_RSERVER_URL = Path("/usr/lib/rstudio-server/bin/rserver-url")


def _root_path_from_output(value: str) -> str:
    candidate = value.strip()
    parsed = urlsplit(candidate)
    if parsed.scheme and parsed.netloc:
        candidate = parsed.path
    candidate = candidate.rstrip("/")
    if not candidate or not candidate.startswith("/") or candidate.startswith("//"):
        raise RuntimeError(f"Workbench returned an invalid application path: {value!r}")
    return candidate


def workbench_root_path(port: int) -> tuple[str, str]:
    override = os.environ.get("DEMO_ROOT_PATH", "").strip()
    if override:
        return _root_path_from_output(override), override
    uvicorn_root = os.environ.get("UVICORN_ROOT_PATH", "").strip()
    if port == 8000 and uvicorn_root:
        return _root_path_from_output(uvicorn_root), uvicorn_root
    if not os.environ.get("RS_SERVER_URL", "").strip():
        return "", ""

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
            "Posit Workbench was detected, but rserver-url could not determine the proxy path. "
            "Ask your Workbench administrator to verify the rserver-url installation."
        ) from exc
    visible_url = result.stdout.strip()
    return _root_path_from_output(visible_url), visible_url


def main() -> None:
    host = os.environ.get("DEMO_HOST", "127.0.0.1")
    port = int(os.environ.get("DEMO_PORT", "8000"))
    root_path, visible_url = workbench_root_path(port)

    if visible_url:
        print(f"Posit Workbench URL: {visible_url}", flush=True)
    else:
        print(f"Local URL: http://{host}:{port}", flush=True)

    uvicorn.run("app:app", host=host, port=port, root_path=root_path)


if __name__ == "__main__":
    main()
