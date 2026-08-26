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
    DEFAULT_USER_PASSWORD,
    app_login,
    client,
    compose,
    csrf_from,
    docker_exec,
    follow_redirects,
    latest_outbox_token,
    preauth_post,
    redact_license_text,
    session_mount_from_home_redirect,
    short_id,
    stack_urls,
    unique_email,
    user_id_for_email,
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
        pytest.fail(
            "Workbench RSA login failed with real PWB_TESTUSER credentials. "
            "Reset the home volume (ACCESS_REGISTRY_WORKBENCH_RESET=1) and ensure "
            f"PWB_TESTUSER_PASSWD meets PAM rules. location={location!r} url={landed.url!r}"
        )
    yield http
    http.close()


@pytest.fixture(scope="module")
def app_session(workbench_stack):
    """Authenticated Data Mover client using seeded ADMIN_EMAIL credentials."""
    http = client(workbench_stack["app"])
    response = app_login(
        http,
        email=workbench_stack["app_email"],
        password=workbench_stack["app_password"],
        next_path="/profile",
    )
    assert response.status_code in {303, 302, 307}, response.text[:500]
    location = response.headers.get("location", "")
    assert "/profile" in location, location
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
    assert 'href="/s/docker-session/p/8000/assets/theme.css?v=8"' in response.text
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


def test_app_login_with_real_admin_credentials(workbench_stack) -> None:
    http = client(workbench_stack["app"])
    response = app_login(
        http,
        email=workbench_stack["app_email"],
        password=workbench_stack["app_password"],
        next_path="/profile",
    )
    assert response.status_code in {303, 302, 307}, response.text[:500]
    location = response.headers.get("location", "")
    assert location.endswith("/profile") or "/profile" in location
    assert "/proxy/8000/s/" not in location
    assert location.startswith("http://") or location.startswith(workbench_stack["mount"])


def test_app_login_rejects_wrong_password(workbench_stack) -> None:
    http = client(workbench_stack["app"])
    response = app_login(
        http,
        email=workbench_stack["app_email"],
        password="Definitely-Wrong-Password-99!",
        next_path="/profile",
    )
    if response.status_code in {303, 302, 307}:
        assert "/profile" not in response.headers.get("location", "")
        assert "login" in response.headers.get("location", "").casefold()
    else:
        assert response.status_code in {200, 400}
        body = response.text.casefold()
        assert "sign in" in body
        assert "/profile" not in response.headers.get("location", "")


def test_app_authenticated_profile_and_security(app_session, workbench_stack) -> None:
    profile = app_session.get("/profile", follow_redirects=False)
    assert profile.status_code == 200, profile.text[:300]
    body = profile.text.casefold()
    assert workbench_stack["app_email"].casefold() in body or "profile" in body
    assert 'name="preauth_csrf_token"' not in body

    security = app_session.get("/security", follow_redirects=False)
    assert security.status_code == 200, security.text[:300]
    assert "session" in security.text.casefold() or "security" in security.text.casefold()


def test_app_logout_returns_to_login(workbench_stack) -> None:
    http = client(workbench_stack["app"])
    assert app_login(
        http,
        email=workbench_stack["app_email"],
        password=workbench_stack["app_password"],
    ).status_code in {303, 302, 307}
    page = http.get("/profile", follow_redirects=False)
    assert page.status_code == 200
    response = http.post(
        "/logout",
        data={"csrf_token": csrf_from(page.text)},
        follow_redirects=False,
    )
    assert response.status_code in {303, 302, 307}
    location = response.headers.get("location", "")
    assert "login" in location.casefold()

    blocked = http.get("/profile", follow_redirects=False)
    assert blocked.status_code in {303, 302, 307, 401}
    if blocked.status_code != 401:
        assert "login" in blocked.headers.get("location", "").casefold()


def test_app_login_through_location_rewrite_proxy(workbench_stack) -> None:
    """Login via the SOCOM-style rewrite proxy using the same real admin credentials."""
    http = client(workbench_stack["rewrite"])
    response = app_login(
        http,
        email=workbench_stack["app_email"],
        password=workbench_stack["app_password"],
        next_path="/profile",
    )
    assert response.status_code in {303, 302, 307}, response.text[:500]
    location = response.headers.get("location", "")
    assert "/profile" in location
    assert "/proxy/8000/s/" not in location
    assert any(name.startswith("access_registry_") for name in http.cookies.keys())
    profile = http.get("/profile", follow_redirects=False)
    assert profile.status_code == 200, profile.text[:300]
    assert 'name="preauth_csrf_token"' not in profile.text.casefold()


def test_app_admin_pages_require_auth(workbench_stack) -> None:
    http = client(workbench_stack["app"])
    for path in ("/admin/users", "/admin/audit"):
        response = http.get(path, follow_redirects=False)
        assert response.status_code in {303, 302, 307, 401}, path
        if response.status_code != 401:
            assert "login" in response.headers.get("location", "").casefold()


def test_app_admin_users_and_audit_pages(app_session, workbench_stack) -> None:
    users = app_session.get("/admin/users", follow_redirects=False)
    assert users.status_code == 200, users.text[:300]
    body = users.text.casefold()
    assert workbench_stack["app_email"].casefold() in body
    assert "sign in" not in body
    assert "admin" in body or "directory" in body or "invitation" in body

    audit = app_session.get("/admin/audit", follow_redirects=False)
    assert audit.status_code == 200, audit.text[:300]
    assert "audit" in audit.text.casefold()
    filtered = app_session.get("/admin/audit?event_type=auth.login", follow_redirects=False)
    assert filtered.status_code == 200


def test_app_public_register_and_forgot_pages(workbench_stack) -> None:
    http = client(workbench_stack["app"])
    register = http.get("/register", follow_redirects=False)
    assert register.status_code == 200
    assert "register" in register.text.casefold() or "email" in register.text.casefold()
    assert 'name="preauth_csrf_token"' in register.text

    forgot = http.get("/password/forgot", follow_redirects=False)
    assert forgot.status_code == 200
    assert "password" in forgot.text.casefold() or "email" in forgot.text.casefold()
    assert 'name="preauth_csrf_token"' in forgot.text


def test_app_profile_update(app_session, workbench_stack) -> None:
    page = app_session.get("/profile", follow_redirects=False)
    assert page.status_code == 200
    marker = f"Workbench Docker Admin {short_id()}"
    response = app_session.post(
        "/profile",
        data={
            "csrf_token": csrf_from(page.text),
            "full_name": marker,
            "organization": "Workbench QA",
            "job_title": "Administrator",
            "phone": "",
        },
        follow_redirects=False,
    )
    assert response.status_code in {303, 302, 307}
    assert "updated=true" in response.headers.get("location", "")
    updated = app_session.get("/profile", follow_redirects=False)
    assert updated.status_code == 200
    assert marker in updated.text


def test_app_security_secret_save_and_delete(app_session) -> None:
    security = app_session.get("/security", follow_redirects=False)
    assert security.status_code == 200
    token_value = f"workbench-mss-token-{short_id()}"
    saved = app_session.post(
        "/security/secrets/mss",
        data={
            "csrf_token": csrf_from(security.text),
            "endpoint": "https://mss.example",
            "token": token_value,
        },
        follow_redirects=False,
    )
    assert saved.status_code in {303, 302, 307}
    assert "secret-saved" in saved.headers.get("location", "")
    after_save = app_session.get("/security", follow_redirects=False)
    assert after_save.status_code == 200
    assert token_value not in after_save.text

    deleted = app_session.post(
        "/security/secrets/mss/delete",
        data={"csrf_token": csrf_from(after_save.text)},
        follow_redirects=False,
    )
    assert deleted.status_code in {303, 302, 307}
    assert "secret-deleted" in deleted.headers.get("location", "")


def test_app_invite_accept_and_login(app_session, workbench_stack) -> None:
    email = unique_email("invite")
    users = app_session.get("/admin/users", follow_redirects=False)
    assert users.status_code == 200
    invited = app_session.post(
        "/admin/invitations",
        data={"csrf_token": csrf_from(users.text), "email": email, "role": "user"},
        follow_redirects=False,
    )
    assert invited.status_code in {303, 302, 307}
    assert "invitation-queued" in invited.headers.get("location", "")

    token = latest_outbox_token(email, subject_like="%invitation%")
    guest = client(workbench_stack["app"])
    accepted = guest.post(
        "/invitations/accept",
        data={
            "token": token,
            "full_name": "Invited Workbench User",
            "password": DEFAULT_USER_PASSWORD,
            "password_confirm": DEFAULT_USER_PASSWORD,
        },
        follow_redirects=False,
    )
    assert accepted.status_code in {200, 303, 302, 307}, accepted.text[:400]
    if accepted.status_code in {303, 302, 307}:
        assert "login" in accepted.headers.get("location", "").casefold()

    signed_in = app_login(guest, email=email, password=DEFAULT_USER_PASSWORD)
    assert signed_in.status_code in {303, 302, 307}, signed_in.text[:400]
    assert "/profile" in signed_in.headers.get("location", "")
    profile = guest.get("/profile", follow_redirects=False)
    assert profile.status_code == 200
    assert email.casefold() in profile.text.casefold()


def test_app_register_verify_approve_and_login(app_session, workbench_stack) -> None:
    email = unique_email("register")
    password = DEFAULT_USER_PASSWORD
    public = client(workbench_stack["app"])
    submitted = preauth_post(
        public,
        "/register",
        {"email": email, "full_name": "Self Registered Workbench"},
    )
    assert submitted.status_code == 202, submitted.text[:400]

    token = latest_outbox_token(email, subject_like="Verify your%registration")
    verified = public.post(
        "/registration/verify",
        data={
            "token": token,
            "password": password,
            "password_confirm": password,
        },
        follow_redirects=False,
    )
    assert verified.status_code == 200, verified.text[:400]
    assert "awaiting administrator" in verified.text.casefold()

    user_id = user_id_for_email(email)
    approved = app_session.post(
        f"/admin/users/{user_id}/approve",
        data={"csrf_token": csrf_from(app_session.get("/admin/users").text)},
        follow_redirects=False,
    )
    assert approved.status_code in {303, 302, 307}
    assert "registration-approved" in approved.headers.get("location", "")

    signed_in = app_login(public, email=email, password=password)
    assert signed_in.status_code in {303, 302, 307}, signed_in.text[:400]
    profile = public.get("/profile", follow_redirects=False)
    assert profile.status_code == 200
    assert email.casefold() in profile.text.casefold()


def test_app_admin_can_disable_user(app_session, workbench_stack) -> None:
    email = unique_email("toggle")
    users = app_session.get("/admin/users", follow_redirects=False)
    invited = app_session.post(
        "/admin/invitations",
        data={"csrf_token": csrf_from(users.text), "email": email, "role": "user"},
        follow_redirects=False,
    )
    assert "invitation-queued" in invited.headers.get("location", "")
    token = latest_outbox_token(email, subject_like="%invitation%")
    guest = client(workbench_stack["app"])
    guest.post(
        "/invitations/accept",
        data={
            "token": token,
            "full_name": "Toggle Target",
            "password": DEFAULT_USER_PASSWORD,
            "password_confirm": DEFAULT_USER_PASSWORD,
        },
        follow_redirects=False,
    )
    user_id = user_id_for_email(email)
    disabled = app_session.post(
        f"/admin/users/{user_id}/toggle",
        data={"csrf_token": csrf_from(app_session.get("/admin/users").text)},
        follow_redirects=False,
    )
    assert disabled.status_code in {303, 302, 307}

    rejected = app_login(guest, email=email, password=DEFAULT_USER_PASSWORD)
    if rejected.status_code in {303, 302, 307}:
        assert "/profile" not in rejected.headers.get("location", "")
    else:
        assert rejected.status_code in {200, 400}
        assert "sign in" in rejected.text.casefold() or "unable" in rejected.text.casefold()


def test_app_password_forgot_queues_reset_email(workbench_stack) -> None:
    http = client(workbench_stack["app"])
    response = preauth_post(
        http,
        "/password/forgot",
        {"email": workbench_stack["app_email"]},
    )
    assert response.status_code in {200, 202, 303, 302, 307}, response.text[:300]
    token = latest_outbox_token(workbench_stack["app_email"], subject_like="%password%")
    assert token


def test_app_admin_invitation_rejects_disallowed_domain(app_session) -> None:
    users = app_session.get("/admin/users", follow_redirects=False)
    response = app_session.post(
        "/admin/invitations",
        data={
            "csrf_token": csrf_from(users.text),
            "email": "bad@example.com",
            "role": "user",
        },
        follow_redirects=False,
    )
    assert response.status_code == 400
    assert "domain" in response.text.casefold() or "approved" in response.text.casefold()
