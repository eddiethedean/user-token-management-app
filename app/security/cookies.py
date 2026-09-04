"""Application cookie names and duplicate-cookie parsing."""

from __future__ import annotations

from typing import Any

from fastapi import Request, Response

from app.config import Settings

ACCESS_COOKIE = "access_registry_access"
REFRESH_COOKIE = "access_registry_refresh"
PREAUTH_CSRF_COOKIE = "access_registry_login_csrf"
THEME_COOKIE = "data_mover_theme"
COLOR_MODE_COOKIE = "data_mover_color_mode"
APPLICATION_COOKIE_NAMES = frozenset(
    {
        ACCESS_COOKIE,
        REFRESH_COOKIE,
        PREAUTH_CSRF_COOKIE,
        THEME_COOKIE,
        COLOR_MODE_COOKIE,
    }
)


def _posit_cookie_registry(request: Request) -> Any:
    """Return HedronPosit's registry when this request belongs to the adapter."""
    # Component/unit-test requests may intentionally omit an ASGI app. Read the
    # scope directly because Request.app indexes scope["app"] and raises KeyError
    # when the ASGI server has not attached one.
    app = request.scope.get("app")
    return getattr(app, "cookies", None)


def set_application_cookie(
    response: Response,
    request: Request,
    settings: Settings,
    name: str,
    value: str,
    *,
    max_age: int | None = None,
) -> None:
    """Set an application cookie through HedronPosit when its path is automatic."""
    registry = _posit_cookie_registry(request) if settings.cookie_path == "auto" else None
    if registry is not None:
        registry.set(
            response,
            name,
            value,
            max_age=max_age,
            secure=settings.cookie_secure,
            httponly=True,
            samesite="lax",
        )
        return
    path = "/" if settings.cookie_path == "auto" else settings.cookie_path
    response.set_cookie(
        name,
        value,
        max_age=max_age,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        path=path,
    )


def delete_application_cookie(
    response: Response, request: Request, settings: Settings, name: str
) -> None:
    """Delete an application cookie using the same Posit-aware path as creation."""
    registry = _posit_cookie_registry(request) if settings.cookie_path == "auto" else None
    if registry is not None:
        registry.delete(response, name)
        return
    path = "/" if settings.cookie_path == "auto" else settings.cookie_path
    response.delete_cookie(
        name,
        path=path,
        secure=settings.cookie_secure,
        httponly=True,
        samesite="lax",
    )


def request_cookie_values(request: Request, name: str) -> list[str]:
    """Return every value for ``name`` in browser order.

    Browsers may send both a mount-scoped cookie and an older root-scoped cookie
    with the same name. ``Request.cookies`` is a dictionary and therefore drops
    all but one duplicate, which can select the stale value.
    """
    values: list[str] = []
    for header_name, raw_header in request.scope.get("headers", []):
        if bytes(header_name).lower() != b"cookie":
            continue
        header = bytes(raw_header).decode("latin-1")
        for item in header.split(";"):
            cookie_name, separator, value = item.strip().partition("=")
            if separator and cookie_name == name:
                values.append(value.strip())
    return values
