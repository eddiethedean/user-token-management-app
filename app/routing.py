from urllib.parse import urlsplit

from fastapi import Request


def _safe_base_path(value: str, *, allow_absolute_url: bool = False) -> str:
    """Reduce a trusted proxy value to a safe, same-origin mount path."""
    candidate = value.strip()
    parsed = urlsplit(candidate)
    if parsed.scheme or parsed.netloc:
        if not allow_absolute_url or parsed.scheme not in {"http", "https"} or not parsed.netloc:
            return ""
        candidate = parsed.path
    elif parsed.query or parsed.fragment:
        return ""
    candidate = candidate.rstrip("/")
    if candidate in {"", "/"}:
        return ""
    if (
        not candidate.startswith("/")
        or candidate.startswith("//")
        or "\\" in candidate
        or "?" in candidate
        or "#" in candidate
        or any(ord(character) < 32 for character in candidate)
    ):
        return ""
    return candidate


def app_base_url(request: Request) -> str:
    """Resolve the external mount path for Connect, Workbench, or a root deployment."""
    connect_base = _safe_base_path(
        request.headers.get("rstudio-connect-app-base-url", ""), allow_absolute_url=True
    )
    workbench_or_asgi_base = _safe_base_path(str(request.scope.get("root_path", "")))
    return connect_base or workbench_or_asgi_base


def app_path(request: Request, path: str) -> str:
    """Prefix an application-absolute path with its external deployment mount path."""
    normalized = path if path.startswith("/") else f"/{path}"
    return f"{app_base_url(request)}{normalized}"


def cookie_path(request: Request, configured_path: str) -> str:
    """Scope cookies to the external app path unless an explicit path is configured."""
    if configured_path != "auto":
        return configured_path
    return app_base_url(request) or "/"
