"""Cookie parsing and the opt-in Posit Connect cookie bridge."""

from __future__ import annotations

import logging
from collections.abc import Collection, Sequence
from dataclasses import dataclass

from fastapi import Request
from starlette.responses import PlainTextResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from app.dev_trace import dev_trace

ACCESS_COOKIE = "access_registry_access"
REFRESH_COOKIE = "access_registry_refresh"
PREAUTH_CSRF_COOKIE = "access_registry_login_csrf"
APPLICATION_COOKIE_NAMES = frozenset({ACCESS_COOKIE, REFRESH_COOKIE, PREAUTH_CSRF_COOKIE})
CONNECT_COOKIE_BRIDGE_HEADER = b"x-access-registry-cookie"
MAX_BRIDGE_COOKIE_BYTES = 16_384

log = logging.getLogger(__name__)


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


def _application_cookie_pairs(
    raw_headers: Sequence[bytes], allowed_names: Collection[bytes]
) -> list[bytes]:
    """Select complete application cookie pairs without parsing unrelated cookies."""
    pairs: list[bytes] = []
    for raw_header in raw_headers:
        for raw_pair in raw_header.split(b";"):
            pair = raw_pair.strip()
            name, separator, _value = pair.partition(b"=")
            if separator and name in allowed_names:
                pairs.append(pair)
    return pairs


@dataclass
class ConnectCookieBridgeMiddleware:
    """Restore app-owned cookies removed by the Connect 2025.06 content proxy.

    This middleware is inert unless it is explicitly enabled *and* the process has
    Connect's runtime marker. The administrator-owned front proxy must overwrite
    ``X-Access-Registry-Cookie`` from the incoming ``Cookie`` header and must be the
    only network path to Connect.
    """

    app: ASGIApp
    enabled: bool
    connect_runtime: bool
    cookie_names: Collection[str] = APPLICATION_COOKIE_NAMES

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if not self.enabled or not self.connect_runtime or scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        headers = list(scope.get("headers") or [])
        bridge_values = [
            bytes(value)
            for name, value in headers
            if bytes(name).lower() == CONNECT_COOKIE_BRIDGE_HEADER
        ]
        if not bridge_values:
            await self.app(scope, receive, send)
            return
        if len(bridge_values) != 1:
            await self._reject(scope, receive, send, "duplicate_bridge_header")
            return

        bridged = bridge_values[0]
        if len(bridged) > MAX_BRIDGE_COOKIE_BYTES:
            await self._reject(scope, receive, send, "bridge_header_too_large")
            return
        if any(character in bridged for character in (b"\r", b"\n", b"\x00")):
            await self._reject(scope, receive, send, "invalid_bridge_header")
            return

        allowed_names = {name.encode("ascii") for name in self.cookie_names}
        native_values = [
            bytes(value) for name, value in headers if bytes(name).lower() == b"cookie"
        ]
        bridged_pairs = _application_cookie_pairs([bridged], allowed_names)
        native_pairs = _application_cookie_pairs(native_values, allowed_names)
        if native_pairs and native_pairs != bridged_pairs:
            await self._reject(scope, receive, send, "conflicting_cookie_transports")
            return

        filtered_headers = [
            (name, value)
            for name, value in headers
            if bytes(name).lower() not in {CONNECT_COOKIE_BRIDGE_HEADER, b"cookie"}
        ]
        if bridged_pairs:
            filtered_headers.append((b"cookie", b"; ".join(bridged_pairs)))

        bridged_scope = dict(scope)
        bridged_scope["headers"] = filtered_headers
        if bridged_pairs:
            dev_trace(
                "cookie.bridge.accepted",
                application_cookie_count=len(bridged_pairs),
                native_application_cookie_count=len(native_pairs),
            )
        await self.app(bridged_scope, receive, send)

    async def _reject(self, scope: Scope, receive: Receive, send: Send, reason: str) -> None:
        log.warning("cookie bridge rejected reason=%s", reason)
        dev_trace("cookie.bridge.rejected", reason=reason)
        response = PlainTextResponse("Invalid cookie bridge request.", status_code=400)
        await response(scope, receive, send)
