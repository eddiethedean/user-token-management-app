"""Helpers for opt-in Posit Workbench Docker integration tests."""

from __future__ import annotations

import base64
import os
import re
import subprocess
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx2
from cryptography.hazmat.primitives.asymmetric import padding, rsa

ROOT = Path(__file__).resolve().parents[1]
COMPOSE_HELPER = ROOT / "docker" / "workbench-compose.sh"

DEFAULT_WORKBENCH = "http://127.0.0.1:8787"
DEFAULT_APP = "http://127.0.0.1:8000"
DEFAULT_REWRITE = "http://127.0.0.1:8788"
DEFAULT_USER = os.environ.get("PWB_TESTUSER", "posit")
DEFAULT_PASSWORD = os.environ.get("PWB_TESTUSER_PASSWD", "Xk9#mQ2$vL8!nR4p")


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
        ["docker", "exec", "access-registry-workbench-workbench-1", *args],
        check=check,
        capture_output=True,
        text=True,
        timeout=60,
    )


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


def stack_urls() -> dict[str, Any]:
    return {
        "workbench": os.environ.get("WORKBENCH_URL", DEFAULT_WORKBENCH).rstrip("/"),
        "app": os.environ.get("APP_URL", DEFAULT_APP).rstrip("/"),
        "rewrite": os.environ.get("REWRITE_URL", DEFAULT_REWRITE).rstrip("/"),
        "username": DEFAULT_USER,
        "password": DEFAULT_PASSWORD,
    }
