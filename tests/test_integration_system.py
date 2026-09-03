"""Cross-process integration and system tests for the supported local runtime."""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import httpx2
import pytest
from sqlalchemy import select

from app.config import get_settings
from app.database import SessionLocal
from app.models import PipelineDefinition, PipelineRun, PipelineRunStatus
from tests.helpers import csrf_from, login_csrf_from, web_login

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SYSTEM_PASSWORD = "System-Test-48!Harbor"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _system_environment(tmp_path: Path, port: int) -> dict[str, str]:
    env = os.environ.copy()
    env.update(
        {
            "APP_ENV": "development",
            "DATABASE_URL": f"sqlite:///{tmp_path / 'system.db'}",
            "PUBLIC_BASE_URL": f"http://127.0.0.1:{port}",
            "JWT_SECRET": "system-test-jwt-secret-at-least-32-bytes-long!!",
            "SESSION_PEPPER": "system-test-session-pepper-at-least-32-bytes!!",
            "CSRF_SECRET": "system-test-csrf-secret-at-least-32-bytes-long!",
            "API_TOKEN_ENCRYPTION_KEYS": '{"development-v1":"AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="}',
            "API_TOKEN_ACTIVE_KEY_ID": "development-v1",
            "ALLOWED_EMAIL_DOMAINS": "example.gov",
            "AUTHENTICATION_MODE": "local_password",
            "COOKIE_SECURE": "false",
            "COOKIE_PATH": "auto",
            "EMAIL_BACKEND": "console",
            "PASSWORD_HASH_SCHEME": "pbkdf2_sha256",
            "PBKDF2_ITERATIONS": "100000",
            "DATA_MOVER_MODE": "demo",
            "TRUSTED_PROXY_IPS": "127.0.0.1",
            "PYTHONUNBUFFERED": "1",
        }
    )
    return env


def _run_cli(env: dict[str, str], *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "app", *args],
        cwd=PROJECT_ROOT,
        env=env,
        check=True,
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_pipeline_run_executes_as_an_in_process_background_task(
    client, demo_connections, monkeypatch
) -> None:
    """A live-mode run completes in the app process after the enqueue response."""
    monkeypatch.setattr(get_settings(), "app_env", "development")
    web_login(client, next_path="/pipeline")
    page = client.get("/pipeline")
    saved = client.post(
        "/pipeline/save",
        data={
            "csrf_token": csrf_from(page.text),
            "pipeline_name": "In-process transfer route",
            "source_provider": "mss",
            "source_schema": "ri.foundry.main.dataset.demo-operations",
            "source_table": "mission_orders.parquet",
            "destination_provider": "postgres",
            "destination_schema": "public",
            "destination_table": "mission_orders",
            "write_mode": "append",
        },
    )
    assert saved.status_code == 303

    with SessionLocal() as db:
        pipeline = db.scalar(
            select(PipelineDefinition).where(PipelineDefinition.name == "In-process transfer route")
        )
        assert pipeline is not None
        pipeline_id = pipeline.id

    queued = client.post(
        "/pipeline/runs",
        data={
            "csrf_token": csrf_from(client.get("/pipeline").text),
            "pipeline_id": pipeline_id,
        },
        headers={"HX-Request": "true", "HX-Target": "pipeline-run-monitor"},
    )
    assert queued.status_code == 202
    assert "queued" in queued.text.lower()

    with SessionLocal() as db:
        completed = db.scalar(
            select(PipelineRun).where(PipelineRun.pipeline_definition_id == pipeline_id)
        )
        assert completed is not None
        assert completed.status == PipelineRunStatus.SUCCEEDED.value
        assert completed.finished_at is not None

    history = client.get(f"/pipeline?pipeline_id={pipeline_id}")
    assert history.status_code == 200
    assert "Persisted run history" in history.text
    assert "Transfer succeeded." in history.text


@pytest.mark.system
def test_cli_serve_process_exposes_health_and_authenticated_profile(tmp_path: Path) -> None:
    """The packaged CLI can boot a real server and complete a browser login."""
    port = _free_port()
    env = _system_environment(tmp_path, port)
    env["ADMIN_BOOTSTRAP_PASSWORD"] = SYSTEM_PASSWORD
    _run_cli(env, "migrate")
    _run_cli(
        env,
        "create-admin",
        "--email",
        "admin@example.gov",
        "--password-env",
        "ADMIN_BOOTSTRAP_PASSWORD",
    )

    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "app",
            "serve",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
        ],
        cwd=PROJECT_ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    client = httpx2.Client(base_url=f"http://127.0.0.1:{port}", trust_env=False)
    try:
        deadline = time.monotonic() + 30
        health = None
        last_error = "server did not respond"
        while time.monotonic() < deadline:
            if process.poll() is not None:
                output = process.stdout.read() if process.stdout else ""
                pytest.fail(f"serve process exited with {process.returncode}:\n{output}")
            try:
                candidate = client.get("/health", timeout=2)
                if candidate.status_code == 200:
                    health = candidate
                    break
                last_error = f"status={candidate.status_code}"
            except httpx2.HTTPError as exc:
                last_error = str(exc)
            time.sleep(0.2)
        assert health is not None, last_error
        assert health.json() == {"status": "ok"}

        login_page = client.get("/login")
        assert login_page.status_code == 200
        token = login_csrf_from(login_page.text)
        signed_in = client.post(
            "/login",
            data={
                "email": "admin@example.gov",
                "password": SYSTEM_PASSWORD,
                "next": "/profile",
                "preauth_csrf_token": token,
            },
            follow_redirects=False,
        )
        assert signed_in.status_code == 303
        redirected = urlsplit(urljoin(str(signed_in.url), signed_in.headers["location"]))
        assert redirected.path == "/profile"
        profile = client.get("/profile")
        assert profile.status_code == 200
        assert "Account settings" in profile.text
    finally:
        client.close()
        if process.poll() is None:
            process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=10)


pytestmark = [pytest.mark.integration]
