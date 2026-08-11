"""Optional Docker + Posit Workbench integration tests.

Opt-in only: ``ACCESS_REGISTRY_WORKBENCH_DOCKER=1`` plus ``POSIT_WORKBENCH_KEY`` in
``.env`` and a working Docker engine. Use ``make workbench-test``.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import time
from pathlib import Path
from urllib.parse import urlsplit

import httpx2
import pytest

from tests.workbench_docker_support import (
    client,
    compose,
    docker_exec,
    follow_redirects,
    redact_license_text,
    session_mount_from_home_redirect,
    stack_urls,
    workbench_login,
)

ROOT = Path(__file__).resolve().parents[1]
COMPOSE_HELPER = ROOT / "docker" / "workbench-compose.sh"
ENV_FILE = ROOT / ".env"
HOME_VOLUME = "access-registry-workbench_workbench-home"


def _load_dotenv_key() -> str:
    if not ENV_FILE.is_file():
        return ""
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        if line.startswith("POSIT_WORKBENCH_KEY="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    return os.environ.get("POSIT_WORKBENCH_KEY", "").strip()


def _docker_available() -> bool:
    if shutil.which("docker") is None:
        return False
    try:
        subprocess.run(
            ["docker", "info"],
            check=True,
            capture_output=True,
            timeout=20,
        )
        return True
    except (OSError, subprocess.SubprocessError):
        return False


WORKBENCH_KEY = _load_dotenv_key()
OPT_IN = os.environ.get("ACCESS_REGISTRY_WORKBENCH_DOCKER", "").strip() == "1"
SKIP_REASON = (
    "Set ACCESS_REGISTRY_WORKBENCH_DOCKER=1 with Docker and POSIT_WORKBENCH_KEY "
    "in .env (see make workbench-test)."
)

pytestmark = [
    pytest.mark.workbench_docker,
    pytest.mark.skipif(
        not OPT_IN or not WORKBENCH_KEY or not _docker_available() or not COMPOSE_HELPER.is_file(),
        reason=SKIP_REASON,
    ),
]


def _wait_http(url: str, *, timeout: float = 180.0) -> None:
    deadline = time.monotonic() + timeout
    last_error = ""
    while time.monotonic() < deadline:
        try:
            response = httpx2.get(url, follow_redirects=False, timeout=5.0, trust_env=False)
            if response.status_code < 500:
                return
            last_error = f"status={response.status_code}"
        except httpx2.HTTPError as exc:
            last_error = str(exc)
        time.sleep(2)
    raise AssertionError(f"Timed out waiting for {url}: {last_error}")


def _reset_workbench_home_volume() -> None:
    """Drop only the home volume so PAM can recreate ``posit`` with the compose password."""
    compose("stop", "workbench", check=False)
    compose("rm", "-f", "workbench", check=False)
    subprocess.run(
        ["docker", "volume", "rm", "-f", HOME_VOLUME],
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )


@pytest.fixture(scope="module")
def workbench_stack():
    """Bring the Workbench compose stack up for this module, then tear it down."""
    reset = os.environ.get("ACCESS_REGISTRY_WORKBENCH_RESET", "1").strip() != "0"
    if reset:
        _reset_workbench_home_volume()
    up = compose("up", "-d", "--build", "--wait", check=False)
    if up.returncode != 0:
        pytest.fail(f"docker compose up failed:\nstdout:\n{up.stdout}\nstderr:\n{up.stderr}")
    urls = stack_urls()
    try:
        _wait_http(f"{urls['workbench']}/")
        _wait_http(f"{urls['workbench']}/health-check")
        _wait_http(f"{urls['app']}/health")
        _wait_http(f"{urls['rewrite']}/")
        yield urls
    finally:
        keep = os.environ.get("ACCESS_REGISTRY_WORKBENCH_KEEP", "").strip() == "1"
        if not keep:
            compose("down", "--remove-orphans", check=False)


@pytest.fixture(scope="module")
def workbench_session(workbench_stack):
    """Authenticated Workbench HTTP client (RSA-encrypted credentials package)."""
    http = client(workbench_stack["workbench"])
    response = workbench_login(
        http,
        username=workbench_stack["username"],
        password=workbench_stack["password"],
    )
    landed = follow_redirects(http, response)
    location = response.headers.get("location", "")
    if "error=" in location or "auth-sign-in" in str(getattr(landed, "url", "")):
        pytest.skip(
            "Workbench RSA login not available in this image/password setup "
            f"(location={location!r}). Non-auth Workbench tests still ran."
        )
    yield http
    http.close()


def test_posit_workbench_health_check_reports_activated_license(workbench_stack) -> None:
    response = httpx2.get(
        f"{workbench_stack['workbench']}/health-check",
        timeout=15.0,
        trust_env=False,
    )
    assert response.status_code == 200
    body = response.text.casefold()
    assert "license-status: activated" in body
    assert "license-allow-product-usage: 1" in body
    assert re.search(r"license-days-left:\s*\d+", body)


def test_posit_workbench_image_serves_login_form(workbench_stack) -> None:
    response = httpx2.get(
        f"{workbench_stack['workbench']}/auth-sign-in",
        follow_redirects=True,
        timeout=30.0,
        trust_env=False,
    )
    assert response.status_code == 200
    assert 'name="username"' in response.text
    assert 'name="password"' in response.text
    assert "rs-csrf-token" in response.text
    assert "auth-do-sign-in" in response.text


def test_posit_workbench_auth_public_key_is_rsa_modulus(workbench_stack) -> None:
    response = httpx2.get(
        f"{workbench_stack['workbench']}/auth-public-key",
        timeout=15.0,
        trust_env=False,
    )
    assert response.status_code == 200
    exp, mod = response.text.strip().split(":", 1)
    assert exp == "010001"
    assert re.fullmatch(r"[0-9A-Fa-f]+", mod)
    assert len(mod) >= 256


def test_posit_workbench_binary_version(workbench_stack) -> None:
    result = docker_exec("rstudio-server", "version")
    assert result.returncode == 0
    assert "Workbench" in result.stdout
    assert re.search(r"20\d{2}\.\d+", result.stdout)


def test_posit_workbench_license_manager_activated(workbench_stack) -> None:
    result = docker_exec("rstudio-server", "license-manager", "status", check=False)
    text = redact_license_text(result.stdout + result.stderr)
    assert "Status: Activated" in text or "status: Activated" in text
    assert "***REDACTED***" in text or "Product-Key" not in text
    assert not re.search(r"\b[A-Z0-9]{4}(?:-[A-Z0-9]{4}){6}\b", text)


def test_posit_workbench_home_redirects_to_session_mount(workbench_stack) -> None:
    response = httpx2.get(
        f"{workbench_stack['workbench']}/home",
        follow_redirects=False,
        timeout=20.0,
        trust_env=False,
    )
    assert response.status_code in {301, 302, 303, 307, 308}
    location = response.headers.get("location", "")
    mount = session_mount_from_home_redirect(location)
    assert mount.startswith("/s/")
    assert "/workspaces" in location


def test_posit_workbench_login_reaches_authenticated_surface(
    workbench_stack, workbench_session
) -> None:
    home = workbench_session.get("/home", follow_redirects=False)
    assert home.status_code in {301, 302, 303, 307, 308}
    location = home.headers.get("location", "")
    mount = session_mount_from_home_redirect(location)
    landed = follow_redirects(workbench_session, home)
    assert landed.status_code == 200
    body = landed.text.casefold()
    assert mount in urlsplit(location).path or mount in str(landed.url)
    assert any(token in body for token in ("workspace", "session", "sign out", "logout", "new"))


def test_app_health_beside_workbench(workbench_stack) -> None:
    response = httpx2.get(
        f"{workbench_stack['app']}/health",
        timeout=10.0,
        trust_env=False,
    )
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_app_ready_beside_workbench(workbench_stack) -> None:
    response = httpx2.get(
        f"{workbench_stack['app']}/ready",
        timeout=10.0,
        trust_env=False,
    )
    assert response.status_code == 200
    assert response.json()["status"] == "ready"


def test_app_login_page_under_session_mount(workbench_stack) -> None:
    response = httpx2.get(
        f"{workbench_stack['app']}/login",
        follow_redirects=False,
        timeout=20.0,
        trust_env=False,
    )
    assert response.status_code == 200
    assert "Sign in" in response.text
    assert 'href="/s/docker-session/p/8000/assets/theme.css"' in response.text
    assert "/s/docker-session/p/8000/hedron-static/" in response.text
    cookie = response.headers.get("set-cookie", "")
    assert "Path=/s/docker-session/p/8000" in cookie


def test_app_mounted_assets_are_css_and_js(workbench_stack) -> None:
    css = httpx2.get(
        f"{workbench_stack['app']}/assets/theme.css",
        timeout=15.0,
        trust_env=False,
    )
    js = httpx2.get(
        f"{workbench_stack['app']}/assets/app.js",
        timeout=15.0,
        trust_env=False,
    )
    assert css.status_code == 200
    assert "text/css" in css.headers.get("content-type", "")
    assert js.status_code == 200
    assert "javascript" in js.headers.get("content-type", "") or js.headers.get(
        "content-type", ""
    ).startswith("text/")


def test_app_direct_redirect_is_scheme_absolute(workbench_stack) -> None:
    response = httpx2.get(
        f"{workbench_stack['app']}/",
        follow_redirects=False,
        timeout=20.0,
        trust_env=False,
    )
    assert response.status_code in {303, 302, 307}
    location = response.headers.get("location", "")
    assert location == "http://127.0.0.1:8787/s/docker-session/p/8000/login"


def test_scheme_absolute_redirect_survives_workbench_location_rewrite(
    workbench_stack,
) -> None:
    """SOCOM bug: path-absolute Location became /proxy/8000/s/…/login."""
    response = httpx2.get(
        f"{workbench_stack['rewrite']}/",
        follow_redirects=False,
        timeout=20.0,
        trust_env=False,
    )
    assert response.status_code in {303, 302, 307}
    location = response.headers.get("location", "")
    assert location.startswith("http://127.0.0.1:8787/s/docker-session/p/8000/login")
    assert "/proxy/8000/s/" not in location


def test_rewrite_proxy_prefixes_path_absolute_locations(workbench_stack) -> None:
    """Sanity-check the SOCOM simulator still rewrites path-absolute Locations."""
    from tests.workbench_docker_support import rewrite_location_rule

    assert (
        rewrite_location_rule("/s/docker-session/p/8000/login")
        == "/proxy/8000/s/docker-session/p/8000/login"
    )
    assert (
        rewrite_location_rule("http://127.0.0.1:8787/s/docker-session/p/8000/login")
        == "http://127.0.0.1:8787/s/docker-session/p/8000/login"
    )
