from __future__ import annotations

import hmac
import secrets
import time
from base64 import urlsafe_b64encode
from hashlib import sha256

from fastapi import HTTPException, Request, Response, status

from app.config import Settings
from app.routing import cookie_path

PREAUTH_CSRF_COOKIE = "access_registry_login_csrf"
PREAUTH_CSRF_MAX_AGE = 3600


def issue_preauth_csrf(settings: Settings, *, issued_at: int | None = None) -> str:
    timestamp = int(time.time()) if issued_at is None else issued_at
    payload = f"{timestamp}.{secrets.token_urlsafe(32)}"
    signature = (
        urlsafe_b64encode(
            hmac.new(settings.csrf_secret.encode(), payload.encode(), sha256).digest()
        )
        .decode()
        .rstrip("=")
    )
    return f"{payload}.{signature}"


def validate_preauth_csrf(
    submitted: str,
    cookie_value: str,
    settings: Settings,
    *,
    now: int | None = None,
) -> bool:
    if not submitted or not cookie_value or not hmac.compare_digest(submitted, cookie_value):
        return False
    try:
        timestamp_text, nonce, signature = submitted.split(".", 2)
        timestamp = int(timestamp_text)
    except (TypeError, ValueError):
        return False
    current_time = int(time.time()) if now is None else now
    if (
        not nonce
        or timestamp > current_time + 60
        or current_time - timestamp > PREAUTH_CSRF_MAX_AGE
    ):
        return False
    payload = f"{timestamp}.{nonce}"
    expected = (
        urlsafe_b64encode(
            hmac.new(settings.csrf_secret.encode(), payload.encode(), sha256).digest()
        )
        .decode()
        .rstrip("=")
    )
    return hmac.compare_digest(signature, expected)


def require_preauth_csrf(request: Request, submitted: str, settings: Settings) -> None:
    if not validate_preauth_csrf(submitted, request.cookies.get(PREAUTH_CSRF_COOKIE, ""), settings):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid CSRF token")


def set_preauth_csrf_cookie(
    response: Response, request: Request, token: str, settings: Settings
) -> None:
    response.set_cookie(
        PREAUTH_CSRF_COOKIE,
        token,
        max_age=PREAUTH_CSRF_MAX_AGE,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        path=cookie_path(request, settings.cookie_path),
    )


def clear_preauth_csrf_cookie(response: Response, request: Request, settings: Settings) -> None:
    response.delete_cookie(
        PREAUTH_CSRF_COOKIE,
        path=cookie_path(request, settings.cookie_path),
        secure=settings.cookie_secure,
        httponly=True,
        samesite="lax",
    )


def assert_csrf(submitted: str, expected: str) -> None:
    """Raise 403 unless ``submitted`` matches the session CSRF token."""
    if not submitted or not hmac.compare_digest(submitted, expected):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid CSRF token")
