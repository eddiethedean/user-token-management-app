import os
import socket
import subprocess
import time
from pathlib import Path

import httpx
import pytest
from playwright.sync_api import Browser, sync_playwright

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PYTHON = PROJECT_ROOT / ".venv" / "bin" / "python"
PREFIX = "/content/access-registry"
ADMIN_EMAIL = "browser.admin@example.gov"
ADMIN_PASSWORD = "Browser-Harbor-73!Signal"


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def wait_for_http(url: str, process: subprocess.Popen, log_path: Path) -> None:
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"Process exited while starting:\n{log_path.read_text()}")
        try:
            response = httpx.get(url, timeout=1)
            if response.status_code < 500:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.1)
    raise RuntimeError(f"Timed out waiting for {url}:\n{log_path.read_text()}")


@pytest.fixture(scope="session")
def browser() -> Browser:
    with sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch(headless=True)
        except Exception as exc:
            pytest.fail(
                "Chromium is not installed. Run `.venv/bin/python -m playwright install chromium`. "
                f"Original error: {exc}"
            )
        yield browser
        browser.close()


@pytest.fixture(params=["preserve", "strip"])
def live_proxy(request, tmp_path):
    mode = request.param
    upstream_port = free_port()
    proxy_port = free_port()
    upstream = f"http://127.0.0.1:{upstream_port}"
    external_base = f"http://127.0.0.1:{proxy_port}{PREFIX}"
    database_path = tmp_path / f"browser-{mode}.db"
    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)
    environment.update(
        {
            "APP_ENV": "development",
            "DATABASE_URL": f"sqlite:///{database_path}",
            "PUBLIC_BASE_URL": external_base,
            "ALLOWED_EMAIL_DOMAINS": "example.gov",
            "JWT_SECRET": "browser-jwt-secret-that-is-at-least-thirty-two-bytes",
            "SESSION_PEPPER": "browser-session-pepper-that-is-at-least-thirty-two-bytes",
            "CSRF_SECRET": "browser-csrf-secret-that-is-at-least-thirty-two-bytes",
            "PASSWORD_HASH_SCHEME": "pbkdf2_sha256",
            "PBKDF2_ITERATIONS": "100000",
            "COOKIE_SECURE": "false",
            "COOKIE_PATH": "auto",
            "TRUSTED_PROXY_IPS": "127.0.0.1",
            "RATE_LIMIT_LOGIN_PER_SOURCE": "100",
            "RATE_LIMIT_LOGIN_PER_ACCOUNT": "100",
            "ADMIN_BOOTSTRAP_PASSWORD": ADMIN_PASSWORD,
        }
    )
    subprocess.run(
        [str(PYTHON), "-m", "app", "migrate"],
        cwd=PROJECT_ROOT,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        [
            str(PYTHON),
            "-m",
            "app",
            "create-admin",
            "--email",
            ADMIN_EMAIL,
            "--password-env",
            "ADMIN_BOOTSTRAP_PASSWORD",
        ],
        cwd=PROJECT_ROOT,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )

    app_log = tmp_path / f"app-{mode}.log"
    proxy_log = tmp_path / f"proxy-{mode}.log"
    app_stream = app_log.open("w")
    proxy_stream = proxy_log.open("w")
    app_command = [
        str(PYTHON),
        "-m",
        "uvicorn",
        "app.main:app",
        "--host",
        "127.0.0.1",
        "--port",
        str(upstream_port),
    ]
    if mode == "preserve":
        app_command.extend(["--root-path", PREFIX])
    app_process = subprocess.Popen(
        app_command,
        cwd=PROJECT_ROOT,
        env=environment,
        stdout=app_stream,
        stderr=subprocess.STDOUT,
    )
    proxy_process = None
    try:
        wait_for_http(f"{upstream}/health", app_process, app_log)
        proxy_process = subprocess.Popen(
            [
                str(PYTHON),
                str(Path(__file__).with_name("connect_like_proxy.py")),
                "--listen-port",
                str(proxy_port),
                "--upstream",
                upstream,
                "--prefix",
                PREFIX,
                "--mode",
                mode,
            ],
            cwd=PROJECT_ROOT,
            env=environment,
            stdout=proxy_stream,
            stderr=subprocess.STDOUT,
        )
        wait_for_http(f"{external_base}/health", proxy_process, proxy_log)
        yield {"mode": mode, "base_url": external_base, "prefix": PREFIX}
    finally:
        for process in (proxy_process, app_process):
            if process and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
        app_stream.close()
        proxy_stream.close()
