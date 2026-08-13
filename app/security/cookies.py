"""Application cookie names and duplicate-cookie parsing."""

from __future__ import annotations

from fastapi import Request

ACCESS_COOKIE = "access_registry_access"
REFRESH_COOKIE = "access_registry_refresh"
PREAUTH_CSRF_COOKIE = "access_registry_login_csrf"
APPLICATION_COOKIE_NAMES = frozenset({ACCESS_COOKIE, REFRESH_COOKIE, PREAUTH_CSRF_COOKIE})


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
