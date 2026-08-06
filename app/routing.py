from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import unquote, urlsplit

from fastapi import Request
from starlette.types import ASGIApp, Receive, Scope, Send

from app.config import get_settings
from app.security.client import is_trusted_direct_proxy

_PROXY_ROOT = re.compile(r"^/proxy/\d+(?P<mount>/.*)$")


def _raw_path(path: str) -> bytes:
    return path.encode("utf-8", errors="surrogatepass")


def normalize_workbench_scope(scope: Scope) -> Scope:
    """Normalize Workbench path forms while retaining the externally visible mount as root_path."""
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
    if root_path and (path == root_path or path.startswith(f"{root_path}/")):
        path = path[len(root_path) :] or "/"
        changed = True
    elif root_path:
        proxy_match = _PROXY_ROOT.match(root_path)
        if proxy_match:
            mount = proxy_match.group("mount").rstrip("/")
            if mount and (path == mount or path.startswith(f"{mount}/")):
                path = path[len(mount) :] or "/"
                effective_root = mount
                changed = True

    if not changed:
        return scope
    normalized = dict(scope)
    normalized["path"] = path
    normalized["raw_path"] = _raw_path(path)
    normalized["root_path"] = effective_root
    return normalized


@dataclass
class WorkbenchPathMiddleware:
    app: ASGIApp

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope.get("type") in {"http", "websocket"}:
            scope = normalize_workbench_scope(scope)
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


def app_base_url(request: Request) -> str:
    """Resolve the external mount path for Connect, Workbench, or a root deployment."""
    connect_base = ""
    if is_trusted_direct_proxy(request, get_settings()):
        connect_base = safe_base_path(
            request.headers.get("rstudio-connect-app-base-url", ""), allow_absolute_url=True
        )
    workbench_or_asgi_base = safe_base_path(str(request.scope.get("root_path", "")))
    return connect_base or workbench_or_asgi_base


def app_path(request: Request, path: str) -> str:
    """Prefix an application-absolute path with its external deployment mount path."""
    normalized = path if path.startswith("/") else f"/{path}"
    return f"{app_base_url(request)}{normalized}"


def is_htmx_request(request: Request) -> bool:
    """Return whether the request was initiated by HTMX."""
    return request.headers.get("HX-Request", "").casefold() == "true"


def cookie_path(request: Request, configured_path: str) -> str:
    """Scope cookies to the external app path unless an explicit path is configured."""
    if configured_path != "auto":
        return configured_path
    return app_base_url(request) or "/"
