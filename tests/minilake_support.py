"""Run an isolated MiniLake management API for real SDK contract tests."""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import httpx2


@contextmanager
def run_minilake(data_dir: Path) -> Iterator[str]:
    """Own one loopback server, database directory, and log per pytest worker."""

    extension_dir = data_dir / "extensions"
    extension_dir.mkdir(parents=True)
    log_path = data_dir / "server.log"
    server_env = {
        **os.environ,
        "MINILAKE_DATA_DIR": str(data_dir),
        "MINILAKE_SERVICES": "sql_warehouses,clusters",
        "MINILAKE_NOTEBOOK": "0",
        "MINILAKE_MCP": "0",
        "MINILAKE_SPARK_PREWARM": "0",
        # Management tests need neither Delta nor runtime extension downloads.
        "MINILAKE_DUCKDB_EXTENSION_DIR": str(extension_dir),
        "MINILAKE_PERSIST": "0",
    }
    with socket.socket() as listener, log_path.open("w", encoding="utf-8") as log:
        listener.bind(("127.0.0.1", 0))
        endpoint = f"http://127.0.0.1:{listener.getsockname()[1]}"
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "minilake.app:create_app",
                "--factory",
                "--fd",
                str(listener.fileno()),
                "--http",
                "h11",
                "--log-level",
                "warning",
                "--no-access-log",
            ],
            env=server_env,
            stdout=log,
            stderr=subprocess.STDOUT,
            pass_fds=(listener.fileno(),),
        )
        try:
            deadline = time.monotonic() + 30
            while process.poll() is None and time.monotonic() < deadline:
                try:
                    response = httpx2.get(
                        f"{endpoint}/_minilake/health", timeout=1, trust_env=False
                    )
                    if response.status_code == 200:
                        break
                except httpx2.HTTPError:
                    time.sleep(0.05)
            else:
                raise RuntimeError(
                    f"MiniLake failed to become ready:\n{log_path.read_text(encoding='utf-8')}"
                )
            yield endpoint
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
