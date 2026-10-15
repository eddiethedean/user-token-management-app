"""Loopback-only, bounded subprocess fixtures for WireMock and Toxiproxy."""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.request import Request, urlopen

import pytest

TOOLS = Path(__file__).resolve().parents[1] / ".cache" / "databricks-tools"


def request_json(endpoint: str, path: str, *, method: str = "GET", body: Any = None) -> Any:
    data = json.dumps(body).encode() if body is not None else None
    request = Request(
        endpoint + path, data=data, method=method, headers={"Content-Type": "application/json"}
    )
    with urlopen(request, timeout=3) as response:
        raw = response.read()
        return json.loads(raw) if raw else None


def _available_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


def require_tool(path: Path) -> str:
    if not path.is_file():
        message = "Run make databricks-tools to install the pinned WireMock/Toxiproxy test servers."
        if os.environ.get("DATABRICKS_TEST_TOOLS_REQUIRED") == "1":
            pytest.fail(message)
        pytest.skip(message)
    return str(path)


@contextmanager
def _server(command: list[str], endpoint: str, health: str, log_path: Path) -> Iterator[str]:
    with log_path.open("w") as log:
        process = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT)
        try:
            deadline = time.monotonic() + 30
            while process.poll() is None and time.monotonic() < deadline:
                try:
                    request_json(endpoint, health)
                    break
                except (URLError, TimeoutError, OSError):
                    time.sleep(0.05)
            else:
                raise RuntimeError(f"Test server failed to start:\n{log_path.read_text()}")
            yield endpoint
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


@contextmanager
def run_wiremock(directory: Path) -> Iterator[str]:
    jar = require_tool(TOOLS / "wiremock-3.13.2.jar")
    java = os.environ.get("DATABRICKS_TEST_JAVA") or shutil.which("java")
    if not java:
        pytest.fail("WireMock requires Java 11+ (or DATABRICKS_TEST_JAVA).")
    port = _available_port()
    with _server(
        [
            java,
            "-Xmx128m",
            "-jar",
            jar,
            "--bind-address",
            "127.0.0.1",
            "--port",
            str(port),
            "--root-dir",
            str(directory),
            "--disable-banner",
            "--max-request-journal-entries",
            "200",
        ],
        f"http://127.0.0.1:{port}",
        "/__admin/mappings",
        directory / "wiremock.log",
    ) as endpoint:
        yield endpoint


@contextmanager
def run_toxiproxy(directory: Path) -> Iterator[str]:
    executable = require_tool(TOOLS / "toxiproxy-2.12.0")
    port = _available_port()
    with _server(
        [executable, "-host", "127.0.0.1", "-port", str(port)],
        f"http://127.0.0.1:{port}",
        "/proxies",
        directory / "toxiproxy.log",
    ) as endpoint:
        yield endpoint


class WireMock:
    def __init__(self, endpoint: str):
        self.endpoint = endpoint

    def stub(self, method: str, path: str, response: dict[str, Any], **mapping: Any) -> None:
        request_json(
            self.endpoint,
            "/__admin/mappings",
            method="POST",
            body={"request": {"method": method, "urlPath": path}, "response": response, **mapping},
        )

    def requests(self, method: str, path: str) -> list[dict[str, Any]]:
        return request_json(
            self.endpoint,
            "/__admin/requests/find",
            method="POST",
            body={"method": method, "urlPath": path},
        )["requests"]


class ToxicProxy:
    def __init__(self, admin: str, upstream: str):
        self.admin = admin
        proxy = request_json(
            admin,
            "/proxies",
            method="POST",
            body={
                "name": "databricks",
                "listen": "127.0.0.1:0",
                "upstream": upstream.removeprefix("http://"),
                "enabled": True,
            },
        )
        self.endpoint = "http://" + proxy["listen"]

    def add(self, kind: str, attributes: dict[str, Any]) -> None:
        request_json(
            self.admin,
            "/proxies/databricks/toxics",
            method="POST",
            body={
                "name": "fault",
                "type": kind,
                "stream": "downstream",
                "toxicity": 1.0,
                "attributes": attributes,
            },
        )

    def clear(self) -> None:
        request_json(self.admin, "/proxies/databricks/toxics/fault", method="DELETE")

    def close(self) -> None:
        request_json(self.admin, "/proxies/databricks", method="DELETE")
