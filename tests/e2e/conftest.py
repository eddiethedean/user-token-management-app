import os
import socket
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

import httpx
import pytest
from playwright.sync_api import Browser, sync_playwright

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PYTHON = PROJECT_ROOT / ".venv" / "bin" / "python"
ADMIN_EMAIL = "browser.admin@example.gov"
ADMIN_PASSWORD = "Browser-Harbor-73!Signal"


@dataclass(frozen=True)
class PositProfile:
    name: str
    platform: str
    prefix: str
    public_origin: str
    connect_base: str = "path"
    root_path: str = ""
    upstream_path: str = "strip"
    use_workbench_launcher: bool = False
    worker_cookie: bool = False


POSIT_PROFILES = (
    PositProfile(
        name="connect-guid-current",
        platform="connect",
        prefix="/content/42",
        public_origin="https://connect.example.gov",
        connect_base="absolute",
        root_path="/content/42",
        worker_cookie=True,
    ),
    PositProfile(
        name="connect-vanity-header-only",
        platform="connect",
        prefix="/access-registry",
        public_origin="https://connect.example.gov",
    ),
    PositProfile(
        name="workbench-rserver-url",
        platform="workbench",
        prefix="/s/4566a3c9ab5a7ad01e1a7/p/30507931",
        public_origin="https://workbench.example.gov",
        use_workbench_launcher=True,
    ),
    PositProfile(
        name="workbench-external-prefix",
        platform="workbench",
        prefix="/rstudio/s/8a76b5c4d3e2f1098765/p/30507932",
        public_origin="https://gateway.example.gov",
        use_workbench_launcher=True,
    ),
    PositProfile(
        name="workbench-preserved-asgi-path",
        platform="workbench",
        prefix="/s/9f8e7d6c5b4a32100123/p/30507933",
        public_origin="https://workbench.example.gov",
        upstream_path="preserve",
        use_workbench_launcher=True,
    ),
)


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
            response = httpx.get(url, timeout=1, verify=False)
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


@pytest.fixture(scope="session")
def tls_certificate(tmp_path_factory) -> tuple[Path, Path]:
    certificate_directory = tmp_path_factory.mktemp("posit-simulation-tls")
    certificate = certificate_directory / "certificate.pem"
    private_key = certificate_directory / "private-key.pem"
    openssl_config = certificate_directory / "openssl.cnf"
    openssl_config.write_text(
        """[req]
distinguished_name = subject
x509_extensions = extensions
prompt = no

[subject]
CN = 127.0.0.1

[extensions]
subjectAltName = IP:127.0.0.1,DNS:localhost
keyUsage = digitalSignature,keyEncipherment
extendedKeyUsage = serverAuth
""",
        encoding="utf-8",
    )
    try:
        subprocess.run(
            [
                "openssl",
                "req",
                "-x509",
                "-nodes",
                "-newkey",
                "rsa:2048",
                "-days",
                "2",
                "-config",
                str(openssl_config),
                "-keyout",
                str(private_key),
                "-out",
                str(certificate),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        pytest.fail(f"OpenSSL is required to create the local HTTPS test certificate: {exc}")
    return certificate, private_key


@pytest.fixture(params=[pytest.param(profile, id=profile.name) for profile in POSIT_PROFILES])
def live_proxy(request, tmp_path, tls_certificate):
    profile: PositProfile = request.param
    upstream_port = free_port()
    proxy_port = free_port()
    upstream = f"http://127.0.0.1:{upstream_port}"
    external_base = f"https://127.0.0.1:{proxy_port}{profile.prefix}"
    database_path = tmp_path / f"browser-{profile.name}.db"
    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)
    environment.pop("RSERVER_URL_BIN", None)
    environment.pop("RS_SERVER_URL", None)
    environment.pop("UVICORN_ROOT_PATH", None)
    environment.update(
        {
            "APP_ENV": "development",
            "DATABASE_URL": f"sqlite:///{database_path}",
            "PUBLIC_BASE_URL": f"{profile.public_origin}{profile.prefix}",
            "ALLOWED_EMAIL_DOMAINS": "example.gov",
            "JWT_SECRET": "browser-jwt-secret-that-is-at-least-thirty-two-bytes",
            "SESSION_PEPPER": "browser-session-pepper-that-is-at-least-thirty-two-bytes",
            "CSRF_SECRET": "browser-csrf-secret-that-is-at-least-thirty-two-bytes",
            "PASSWORD_HASH_SCHEME": "pbkdf2_sha256",
            "PBKDF2_ITERATIONS": "100000",
            "COOKIE_SECURE": "true",
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

    app_log = tmp_path / f"app-{profile.name}.log"
    proxy_log = tmp_path / f"proxy-{profile.name}.log"
    app_stream = app_log.open("w")
    proxy_stream = proxy_log.open("w")
    if profile.use_workbench_launcher:
        rserver_url = tmp_path / "rserver-url"
        rserver_url.write_text(
            f"#!/bin/sh\nprintf '%s\\n' '{profile.public_origin}{profile.prefix}/'\n",
            encoding="utf-8",
        )
        rserver_url.chmod(0o700)
        session_url = profile.prefix.rpartition("/p/")[0]
        environment["RS_SERVER_URL"] = f"{profile.public_origin}{session_url}/"
        environment["RSERVER_URL_BIN"] = str(rserver_url)
        app_command = [
            str(PYTHON),
            "-m",
            "app",
            "serve",
            "--host",
            "127.0.0.1",
            "--port",
            str(upstream_port),
        ]
    else:
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
        if profile.root_path:
            app_command.extend(["--root-path", profile.root_path])
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
                str(Path(__file__).with_name("posit_proxy.py")),
                "--listen-port",
                str(proxy_port),
                "--upstream",
                upstream,
                "--prefix",
                profile.prefix,
                "--platform",
                profile.platform,
                "--public-origin",
                profile.public_origin,
                "--upstream-path",
                profile.upstream_path,
                "--connect-base",
                profile.connect_base,
                "--tls-cert",
                str(tls_certificate[0]),
                "--tls-key",
                str(tls_certificate[1]),
                *(["--worker-cookie"] if profile.worker_cookie else []),
            ],
            cwd=PROJECT_ROOT,
            env=environment,
            stdout=proxy_stream,
            stderr=subprocess.STDOUT,
        )
        wait_for_http(f"{external_base}/health", proxy_process, proxy_log)
        yield {
            "base_url": external_base,
            "platform": profile.platform,
            "prefix": profile.prefix,
            "profile": profile.name,
            "public_origin": profile.public_origin,
            "worker_cookie": profile.worker_cookie,
        }
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
