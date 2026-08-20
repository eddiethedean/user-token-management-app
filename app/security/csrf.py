from __future__ import annotations

import hmac
import secrets
import time
from base64 import urlsafe_b64encode
from hashlib import sha256

from fastapi import HTTPException, Request, Response, status

from app.config import Settings
from app.dev_trace import dev_trace
from app.security.cookies import PREAUTH_CSRF_COOKIE, request_cookie_values

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
    cookie_values = request_cookie_values(request, PREAUTH_CSRF_COOKIE)
    matching_index = next(
        (
            index
            for index, value in enumerate(cookie_values)
            if validate_preauth_csrf(submitted, value, settings)
        ),
        None,
    )
    if matching_index is None:
        dev_trace(
            "csrf.preauth.rejected",
            submitted=bool(submitted),
            cookie_count=len(cookie_values),
            reason="missing_cookie" if not cookie_values else "no_matching_cookie",
        )
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid CSRF token")
    dev_trace(
        "csrf.preauth.accepted",
        cookie_count=len(cookie_values),
        matching_cookie_index=matching_index,
    )


def set_preauth_csrf_cookie(
    response: Response, request: Request, token: str, settings: Settings
) -> None:
    path = "/" if settings.cookie_path == "auto" else settings.cookie_path
    if path not in {None, "/"}:
        # Remove cookies produced by older deployments before mount-aware paths
        # were enabled. Otherwise browsers send both names and frameworks may
        # retain the stale root value.
        response.delete_cookie(
            PREAUTH_CSRF_COOKIE,
            path="/",
            secure=settings.cookie_secure,
            httponly=True,
            samesite="lax",
        )
    response.set_cookie(
        PREAUTH_CSRF_COOKIE,
        token,
        max_age=PREAUTH_CSRF_MAX_AGE,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        path=path,
    )
    dev_trace(
        "csrf.preauth.cookie.issued",
        cookie_path=path,
        secure=settings.cookie_secure,
        samesite="lax",
        legacy_root_cleanup=path not in {None, "/"},
    )


def clear_preauth_csrf_cookie(response: Response, request: Request, settings: Settings) -> None:
    path = "/" if settings.cookie_path == "auto" else settings.cookie_path
    response.delete_cookie(
        PREAUTH_CSRF_COOKIE,
        path=path,
        secure=settings.cookie_secure,
        httponly=True,
        samesite="lax",
    )
    if path != "/":
        response.delete_cookie(
            PREAUTH_CSRF_COOKIE,
            path="/",
            secure=settings.cookie_secure,
            httponly=True,
            samesite="lax",
        )
    dev_trace(
        "csrf.preauth.cookie.cleared",
        cookie_path=path,
        legacy_root_cleanup=path != "/",
    )


def assert_csrf(submitted: str, expected: str) -> None:
    """Raise 403 unless ``submitted`` matches the session CSRF token."""
    if not submitted or not hmac.compare_digest(submitted, expected):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid CSRF token")
