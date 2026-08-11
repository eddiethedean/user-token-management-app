from __future__ import annotations

import os
import re
from dataclasses import dataclass
from urllib.parse import unquote, urlsplit

from fastapi import Request
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.config import get_settings
from app.dev_trace import dev_trace, is_dev_trace_enabled, is_static_scope_path
from app.security.client import is_trusted_direct_proxy

# Workbench Proxied Servers may expose /proxy/<port>/…; rserver-url uses /s/<id>/p/<id>/….
_PROXY_ROOT = re.compile(r"^/proxy/\d+(?P<mount>/.*)$")
_SESSION_MOUNT = re.compile(r"^(?P<root>/s/[^/]+/p/[^/]+)(?P<rest>/.*)?$")
_PROXY_MOUNT = re.compile(r"^(?P<root>/proxy/\d+)(?P<rest>/.*)?$")
_PROXY_ONLY = re.compile(r"^/proxy/\d+$")


def _raw_path(path: str) -> bytes:
    return path.encode("utf-8", errors="surrogatepass")


def workbench_is_active() -> bool:
    """Return whether this process appears to be running inside Posit Workbench."""
    return bool(
        os.environ.get("RS_SERVER_URL", "").strip()
        or os.environ.get("UVICORN_ROOT_PATH", "").strip()
    )


def normalize_workbench_scope(scope: Scope) -> Scope:
    """Set Workbench ``root_path`` without stripping ``path``.

    Starlette 1.4 routes and StaticFiles use ``get_route_path(scope)``, which expects
    ``path`` to still include the mount prefix when ``root_path`` is set. Stripping the
    prefix (the Starlette 0.x habit) makes ``/assets/…`` 404 under ``/s/…/p/…``.
    """
    path = str(scope.get("path") or "/")
    root_path = str(scope.get("root_path") or "").rstrip("/")
    changed = False

    candidate = path.lstrip("/")
    lowered = candidate.casefold()
    if root_path and (
        lowered.startswith("http%3a")
        or lowered.startswith("https%3a")
        or lowered.startswith("http://")
        or lowered.startswith("https://")
    ):
        decoded = unquote(candidate)
        parsed = urlsplit(decoded)
        if parsed.scheme in {"http", "https"} and parsed.netloc:
            path = parsed.path or "/"
            if parsed.query:
                scope = dict(scope)
                scope["query_string"] = parsed.query.encode("utf-8")
            changed = True

    effective_root = root_path
    if not root_path:
        for pattern in (_SESSION_MOUNT, _PROXY_MOUNT):
            match = pattern.match(path)
            if match:
                effective_root = match.group("root")
                changed = True
                break
    else:
        proxy_match = _PROXY_ROOT.match(root_path)
        if proxy_match:
            mount = proxy_match.group("mount").rstrip("/")
            if mount and (path == mount or path.startswith(f"{mount}/")):
                # root_path was /proxy/<port>/<session-mount>; keep path intact and
                # expose only the session mount to URL helpers.
                effective_root = mount
                changed = True

    if not changed:
        return scope
    normalized = dict(scope)
    if path != str(scope.get("path") or "/"):
        normalized["path"] = path
        normalized["raw_path"] = _raw_path(path)
    normalized["root_path"] = effective_root
    return normalized


def route_path_from_scope(scope: Scope) -> str:
    """Mount-relative path (Starlette ``get_route_path`` semantics without a private import)."""
    path = str(scope.get("path") or "/")
    root_path = str(scope.get("root_path") or "")
    if not root_path:
        return path
    if not path.startswith(root_path):
        return path
    if path == root_path:
        return "/"
    if len(path) > len(root_path) and path[len(root_path)] == "/":
        return path[len(root_path) :] or "/"
    return path


def application_path(request: Request) -> str:
    """Return the mount-relative path Starlette uses for routing (leading ``/``)."""
    route_path = route_path_from_scope(request.scope) or "/"
    return route_path if route_path.startswith("/") else f"/{route_path}"


@dataclass
class WorkbenchPathMiddleware:
    app: ASGIApp

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope.get("type") not in {"http", "websocket"}:
            await self.app(scope, receive, send)
            return

        path_before = str(scope.get("path") or "/")
        root_before = str(scope.get("root_path") or "")
        scope = normalize_workbench_scope(scope)
        path_after = str(scope.get("path") or "/")
        root_after = str(scope.get("root_path") or "")
        route = route_path_from_scope(scope)
        trace_http = (
            scope.get("type") == "http"
            and is_dev_trace_enabled()
            and not is_static_scope_path(route)
            and not is_static_scope_path(path_after)
        )
        if trace_http:
            dev_trace(
                "workbench.request",
                method=scope.get("method"),
                path_before=path_before,
                path_after=path_after,
                root_before=root_before,
                root_after=root_after,
                route=route,
                workbench=workbench_is_active(),
            )

            async def send_with_trace(message: Message) -> None:
                if message.get("type") == "http.response.start":
                    status_code = int(message.get("status") or 0)
                    headers = {
                        key.decode("latin-1").casefold(): value.decode("latin-1")
                        for key, value in message.get("headers") or []
                    }
                    location = headers.get("location")
                    hx_redirect = headers.get("hx-redirect")
                    if location or hx_redirect or status_code >= 300:
                        dev_trace(
                            "workbench.response",
                            status=status_code,
                            location=location,
                            hx_redirect=hx_redirect,
                            path=path_after,
                            root_path=root_after,
                            route=route,
                        )
                await send(message)

            await self.app(scope, receive, send_with_trace)
            return

        await self.app(scope, receive, send)


def safe_base_path(value: str, *, allow_absolute_url: bool = False, strict: bool = False) -> str:
    """Reduce a path-like value to a safe, same-origin mount path.

    When ``strict`` is True (Workbench/ASGI root detection), invalid values raise
    ``RuntimeError``. When False (request base-path resolution), they become ``""``.
    """
    candidate = value.strip()
    parsed = urlsplit(candidate)
    if parsed.scheme or parsed.netloc:
        invalid = (
            not allow_absolute_url
            or parsed.scheme not in {"http", "https"}
            or not parsed.netloc
            or (strict and (parsed.username is not None or parsed.password is not None))
            or (strict and (parsed.query or parsed.fragment))
        )
        if invalid:
            if strict:
                raise RuntimeError(f"Invalid ASGI root path returned for Workbench: {candidate!r}")
            return ""
        candidate = parsed.path
    elif parsed.query or parsed.fragment:
        if strict:
            raise RuntimeError(f"Invalid ASGI root path returned for Workbench: {candidate!r}")
        return ""
    path = candidate.rstrip("/")
    if path in {"", "/"}:
        return ""
    if (
        not path.startswith("/")
        or path.startswith("//")
        or "\\" in path
        or (not strict and ("?" in path or "#" in path))
        or any(ord(character) < 32 for character in path)
        or (strict and any(ord(character) == 127 for character in path))
    ):
        if strict:
            raise RuntimeError(f"Invalid ASGI root path returned for Workbench: {path!r}")
        return ""
    return path


def _request_mount_path(request: Request) -> str:
    """Real Connect/ASGI mount only — never the inferred Proxied Servers prefix."""
    connect_base = ""
    if is_trusted_direct_proxy(request, get_settings()):
        connect_base = safe_base_path(
            request.headers.get("rstudio-connect-app-base-url", ""), allow_absolute_url=True
        )
    workbench_or_asgi_base = safe_base_path(str(request.scope.get("root_path", "")))
    return connect_base or workbench_or_asgi_base


def app_base_url(request: Request) -> str:
    """Resolve the external mount path for Connect, Workbench, or a root deployment.

    Mirrors fastapi-workbench ``base_path``: use the real ASGI ``root_path`` or Connect
    base header only. Never invent ``/proxy/<port>`` — that sends session-URL users to the
    wrong Workbench entry point.
    """
    return _request_mount_path(request)


def app_path(request: Request, path: str) -> str:
    """Prefix an application-absolute path with its external deployment mount path."""
    normalized = path if path.startswith("/") else f"/{path}"
    return f"{app_base_url(request)}{normalized}"


def _request_under_mount(request: Request, mount: str) -> bool:
    """True when the ASGI path is already under ``mount`` (session or proxy prefix)."""
    if not mount:
        return False
    path = str(request.scope.get("path") or "/")
    return path == mount or path.startswith(f"{mount}/")


def workbench_public_origin() -> str:
    """Return ``https://workbench…`` origin from Workbench env, or ``\"\"`` if unknown."""
    configured = os.environ.get("UVICORN_ROOT_PATH", "").strip()
    if configured:
        parsed = urlsplit(configured)
        if parsed.scheme in {"http", "https"} and parsed.netloc:
            return f"{parsed.scheme}://{parsed.netloc}"
    server = os.environ.get("RS_SERVER_URL", "").strip()
    if server:
        parsed = urlsplit(server)
        if parsed.scheme in {"http", "https"} and parsed.netloc:
            return f"{parsed.scheme}://{parsed.netloc}"
    return ""


def redirect_path(request: Request, path: str) -> str:
    """Build a ``Location`` / ``HX-Redirect`` target safe behind Workbench Proxied Servers.

    Workbench rewrites **path-absolute** ``Location`` values by prefixing ``/proxy/<port>``.
    That turns a correct ``/s/…/p/…/login`` into the broken
    ``/proxy/8000/s/…/p/…/login`` (HTTP 404). Evidence from SOCOM Workbench: the app
    emitted ``Location: /s/…/login`` and the browser received the combined URL.

    Mitigation:

    - Session-mount requests: emit a **scheme-absolute** URL
      (``https://workbench…/s/…/login``) so the proxy leaves ``Location`` alone.
    - Proxied Servers / stripped-prefix requests: emit a **relative** target
      (``login``) so the browser stays under ``/proxy/<port>/``.

    See https://github.com/eddiethedean/jwt-user-management/tree/main/fastapi_workbench
    """
    normalized = path if path.startswith("/") else f"/{path}"
    mount = _request_mount_path(request)
    raw_mount = mount
    reason = "mount"
    # Workbench Proxied Servers rewrites absolute Location by prefixing /proxy/<port>.
    if mount and _PROXY_ONLY.match(mount):
        mount = ""
        reason = "proxy-only-mount→relative"
    elif mount and _SESSION_MOUNT.match(mount) and not _request_under_mount(request, mount):
        mount = ""
        reason = "session-root-without-path→relative"
    if mount:
        path_location = mount if normalized == "/" else f"{mount}{normalized}"
        origin = workbench_public_origin() if workbench_is_active() else ""
        if origin and _SESSION_MOUNT.match(mount):
            # Avoid path-absolute Location → /proxy/<port> rewrite.
            location = f"{origin}{path_location}"
            reason = "session-mount→scheme-absolute"
        else:
            location = path_location
            reason = "mount"
    elif workbench_is_active():
        reason = "workbench-relative" if reason == "mount" else reason
        location = "." if normalized == "/" else (normalized.lstrip("/") or ".")
    else:
        reason = "absolute"
        location = normalized
    dev_trace(
        "redirect_path",
        target=normalized,
        raw_mount=raw_mount,
        under_mount=_request_under_mount(request, raw_mount),
        reason=reason,
        location=location,
        path=str(request.scope.get("path") or "/"),
        root_path=str(request.scope.get("root_path") or ""),
    )
    return location


def is_htmx_request(request: Request) -> bool:
    """Return whether the request was initiated by HTMX."""
    return request.headers.get("HX-Request", "").casefold() == "true"


def cookie_path(request: Request, configured_path: str) -> str:
    """Scope cookies to the external app path unless an explicit path is configured."""
    if configured_path != "auto":
        return configured_path
    return app_base_url(request) or "/"
