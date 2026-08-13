"""Helpers for opt-in Posit Workbench Docker integration tests."""

from __future__ import annotations

import base64
import json
import os
import re
import subprocess
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx2
from cryptography.hazmat.primitives.asymmetric import padding, rsa

ROOT = Path(__file__).resolve().parents[1]
COMPOSE_HELPER = ROOT / "docker" / "workbench-compose.sh"
APP_CONTAINER = "access-registry-workbench-app-1"
WORKBENCH_CONTAINER = "access-registry-workbench-workbench-1"

DEFAULT_WORKBENCH = "http://127.0.0.1:8787"
DEFAULT_APP = "http://127.0.0.1:8000"
DEFAULT_REWRITE = "http://127.0.0.1:8788"
DEFAULT_USER = os.environ.get("PWB_TESTUSER", "posit")
DEFAULT_PASSWORD = os.environ.get("PWB_TESTUSER_PASSWD", "Xk9#mQ2$vL8!nR4p")
DEFAULT_APP_EMAIL = os.environ.get("ADMIN_EMAIL", "admin@example.gov")
DEFAULT_APP_PASSWORD = os.environ.get("ADMIN_BOOTSTRAP_PASSWORD", "Tr0pic-Maple!River92")
DEFAULT_USER_PASSWORD = "Aspen-Compass-64!River"
SESSION_MOUNT = "/s/docker-session/p/8000"


def client(base_url: str, *, follow_redirects: bool = False) -> httpx2.Client:
    return httpx2.Client(
        base_url=base_url.rstrip("/"),
        follow_redirects=follow_redirects,
        timeout=30.0,
        trust_env=False,
    )


def compose(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(COMPOSE_HELPER), *args],
        cwd=ROOT,
        check=check,
        capture_output=True,
        text=True,
        timeout=600,
    )


def docker_exec(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["docker", "exec", WORKBENCH_CONTAINER, *args],
        check=check,
        capture_output=True,
        text=True,
        timeout=60,
    )


def app_exec(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["docker", "exec", APP_CONTAINER, *args],
        check=check,
        capture_output=True,
        text=True,
        timeout=60,
    )


def unique_email(prefix: str = "wb") -> str:
    return f"{prefix}.{uuid.uuid4().hex[:10]}@example.gov"


def short_id() -> str:
    return uuid.uuid4().hex[:10]


def encrypt_workbench_credentials(username: str, password: str, public_key_body: str) -> str:
    """Return the ``v`` form field value produced by Workbench browser sign-in."""
    exp_hex, mod_hex = public_key_body.strip().split(":", 1)
    key = rsa.RSAPublicNumbers(int(exp_hex, 16), int(mod_hex, 16)).public_key()
    payload = f"{username}\n{password}".encode()
    encrypted = key.encrypt(payload, padding.PKCS1v15())
    return base64.b64encode(encrypted).decode("ascii")


def fetch_auth_public_key(http: httpx2.Client) -> str:
    response = http.get("/auth-public-key")
    response.raise_for_status()
    body = response.text.strip()
    if ":" not in body:
        raise AssertionError(f"Unexpected /auth-public-key payload: {body!r}")
    return body


def workbench_login(
    http: httpx2.Client,
    *,
    username: str = DEFAULT_USER,
    password: str = DEFAULT_PASSWORD,
) -> httpx2.Response:
    """Sign in through ``/auth-do-sign-in`` using the RSA-encrypted ``v`` package field."""
    page = http.get("/auth-sign-in", follow_redirects=True)
    page.raise_for_status()
    match = re.search(
        r'name=["\']rs-csrf-token["\'][^>]*value=["\']([^"\']+)',
        page.text,
    )
    if not match:
        raise AssertionError("Workbench login page missing rs-csrf-token")
    csrf = match.group(1)
    package = encrypt_workbench_credentials(username, password, fetch_auth_public_key(http))
    return http.post(
        "/auth-do-sign-in",
        data={
            "username": username,
            "password": "",
            "v": package,
            "rs-csrf-token": csrf,
            "appUri": "/",
            "persist": "0",
            "clientPath": "/auth-sign-in",
        },
    )


def follow_redirects(
    http: httpx2.Client, response: httpx2.Response, *, limit: int = 8
) -> httpx2.Response:
    current = response
    for _ in range(limit):
        if current.status_code not in {301, 302, 303, 307, 308}:
            return current
        location = current.headers.get("location")
        if not location:
            return current
        current = http.get(location)
    return current


def session_mount_from_home_redirect(location: str) -> str:
    """Extract ``/s/<id>`` from a Workbench ``/home`` → ``/s/…/workspaces/`` redirect."""
    path = urlsplit(location).path if "://" in location else location
    match = re.match(r"^(?P<root>/s/[^/]+)(?:/.*)?$", path)
    if not match:
        raise AssertionError(f"Expected Workbench session mount in Location, got {location!r}")
    return match.group("root")


def redact_license_text(text: str) -> str:
    """Remove product-key material from license-manager output before assertions/logging."""
    redacted = re.sub(
        r"(Product-Key|product-key)\s*[:=]\s*\S+",
        r"\1: ***REDACTED***",
        text,
        flags=re.IGNORECASE,
    )
    return re.sub(r"\b[A-Z0-9]{4}(?:-[A-Z0-9]{4}){6}\b", "***REDACTED***", redacted)


def rewrite_location_rule(value: str, *, proxy_prefix: str = "/proxy/8000") -> str:
    """Mirror ``docker/location_rewrite_proxy.py`` without importing container deps."""
    prefix = proxy_prefix.rstrip("/")
    candidate = value.strip()
    if not candidate:
        return candidate
    parsed = urlsplit(candidate)
    if parsed.scheme and parsed.netloc:
        return candidate
    if candidate.startswith(prefix):
        return candidate
    if candidate.startswith("/"):
        return f"{prefix}{candidate}"
    return candidate


def login_csrf_from(html: str) -> str:
    match = re.search(r'name="preauth_csrf_token"\s+value="([^"]+)"', html)
    if match is None:
        match = re.search(r'name="preauth_csrf_token" value="([^"]+)"', html)
    if match is None:
        raise AssertionError("preauth_csrf_token missing from HTML")
    return match.group(1)


def csrf_from(html: str) -> str:
    match = re.search(r'name="csrf_token"\s+value="([^"]+)"', html)
    if match is None:
        match = re.search(r'name="csrf_token" value="([^"]+)"', html)
    if match is None:
        raise AssertionError("csrf_token missing from HTML")
    return match.group(1)


def latest_outbox_token(recipient: str, *, subject_like: str | None = None) -> str:
    """Read the newest EmailOutbox link token from the app container SQLite DB."""
    payload = json.dumps({"recipient": recipient, "subject_like": subject_like})
    script = f"""
import json, re, sys
from urllib.parse import parse_qs, urlparse
from sqlalchemy import select
from app.database import SessionLocal
from app.models import EmailOutbox

payload = json.loads({payload!r})
with SessionLocal() as db:
    statement = select(EmailOutbox).where(EmailOutbox.recipient == payload["recipient"])
    if payload.get("subject_like"):
        statement = statement.where(EmailOutbox.subject.like(payload["subject_like"]))
    message = db.scalar(statement.order_by(EmailOutbox.created_at.desc()))
    if message is None:
        raise SystemExit("no-email")
    match = re.search(r"https?://[^\\s]+", message.body_text)
    if match is None:
        raise SystemExit("no-link")
    print(parse_qs(urlparse(match.group(0)).query)["token"][0])
"""
    result = app_exec("python", "-c", script, check=False)
    token = result.stdout.strip()
    if result.returncode != 0 or not token:
        raise AssertionError(
            f"Failed to read outbox token for {recipient}: "
            f"rc={result.returncode} stdout={result.stdout!r} stderr={result.stderr!r}"
        )
    return token


def user_id_for_email(email: str) -> str:
    script = f"""
from sqlalchemy import select
from app.database import SessionLocal
from app.models import User
with SessionLocal() as db:
    user = db.scalar(select(User).where(User.email == {email!r}))
    if user is None:
        raise SystemExit("missing")
    print(user.id)
"""
    result = app_exec("python", "-c", script, check=False)
    user_id = result.stdout.strip()
    if result.returncode != 0 or not user_id:
        raise AssertionError(
            f"Failed to resolve user id for {email}: "
            f"rc={result.returncode} stdout={result.stdout!r} stderr={result.stderr!r}"
        )
    return user_id


def widen_cookie_paths(http: httpx2.Client) -> None:
    """Re-scope jar cookies to ``/`` so bare app paths receive Workbench-scoped cookies.

    With ``UVICORN_ROOT_PATH=/s/…/p/…``, Data Mover sets ``Path=/s/…/p/…`` on auth
    cookies. Docker tests hit the published app port where the mount is already stripped
    (``/login``, not ``/s/…/login``), so httpx would otherwise omit those cookies.
    """
    snapshots = [(cookie.name, cookie.value, cookie.domain) for cookie in list(http.cookies.jar)]
    http.cookies.clear()
    for name, value, domain in snapshots:
        if domain:
            http.cookies.set(name, value, domain=domain, path="/")
        else:
            http.cookies.set(name, value, path="/")


def app_login(
    http: httpx2.Client,
    *,
    email: str = DEFAULT_APP_EMAIL,
    password: str = DEFAULT_APP_PASSWORD,
    next_path: str = "/profile",
    login_path: str = "/login",
) -> httpx2.Response:
    """Sign into Data Mover with real email/password credentials (+ CSRF)."""
    page = http.get(login_path, follow_redirects=False)
    page.raise_for_status()
    widen_cookie_paths(http)
    token = login_csrf_from(page.text)
    response = http.post(
        login_path,
        data={
            "email": email,
            "password": password,
            "next": next_path,
            "preauth_csrf_token": token,
        },
        follow_redirects=False,
    )
    widen_cookie_paths(http)
    return response


def preauth_post(
    http: httpx2.Client,
    path: str,
    data: dict[str, str],
    *,
    page_path: str | None = None,
) -> httpx2.Response:
    """GET a public form for preauth CSRF, widen cookie paths, then POST."""
    page = http.get(page_path or path, follow_redirects=False)
    page.raise_for_status()
    widen_cookie_paths(http)
    payload = {**data, "preauth_csrf_token": login_csrf_from(page.text)}
    response = http.post(path, data=payload, follow_redirects=False)
    widen_cookie_paths(http)
    return response


def mounted(mount: str, path: str) -> str:
    """Join a Workbench session mount with an app-absolute path."""
    normalized = path if path.startswith("/") else f"/{path}"
    return f"{mount.rstrip('/')}{normalized}"


def stack_urls() -> dict[str, Any]:
    return {
        "workbench": os.environ.get("WORKBENCH_URL", DEFAULT_WORKBENCH).rstrip("/"),
        "app": os.environ.get("APP_URL", DEFAULT_APP).rstrip("/"),
        "rewrite": os.environ.get("REWRITE_URL", DEFAULT_REWRITE).rstrip("/"),
        "username": DEFAULT_USER,
        "password": DEFAULT_PASSWORD,
        "app_email": DEFAULT_APP_EMAIL,
        "app_password": DEFAULT_APP_PASSWORD,
        "mount": SESSION_MOUNT,
    }
