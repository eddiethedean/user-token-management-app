"""Safe URL helpers for Hedron HTML attributes."""

from __future__ import annotations

from typing import Any

from fastapi import Request
from hedron_core.security import SafeUrl, UrlPurpose

from app.routing import app_path


def _abs_path(path: str) -> str:
    """Normalize to an absolute app path (SafeUrl rejects relative paths)."""
    if not path:
        return "/"
    if path.startswith("/"):
        return path
    return f"/{path}"


def mounted_path(request: Request, path: str) -> str:
    """Application path prefixed with the external deployment mount."""
    path_part, separator, fragment = path.partition("#")
    mounted = app_path(request, _abs_path(path_part))
    # Hedron 0.26 rejects paths that normalize only by dropping a terminal slash.
    # The root app URL is the one exception: without a mount it must remain "/".
    normalized = mounted.rstrip("/") or "/"
    return f"{normalized}#{fragment}" if separator else normalized


def page_href(request: Request, path: str) -> SafeUrl:
    return SafeUrl.parse(mounted_path(request, path), purpose=UrlPurpose.NAVIGATION)


def form_action(request: Request, path: str) -> SafeUrl:
    return SafeUrl.parse(mounted_path(request, path), purpose=UrlPurpose.FORM_ACTION)


def hx_path(request: Request, path: str) -> SafeUrl:
    # Hedron requires purpose=navigation for hx-* URL attributes.
    return SafeUrl.parse(mounted_path(request, path), purpose=UrlPurpose.NAVIGATION)


def asset_href(request: Request, path: str) -> SafeUrl:
    # Hedron 0.26 validates URL purpose by HTML attribute: a stylesheet's
    # ``href`` is a navigation URL even though the resource is an asset.
    return SafeUrl.parse(mounted_path(request, path), purpose=UrlPurpose.NAVIGATION)


def hx_attrs(
    request: Request,
    *,
    method: str = "post",
    path: str,
    target: str | None = None,
    swap: str = "outerHTML",
    trigger: str | None = None,
    sync: str | None = None,
    disabled_elt: str | None = None,
    confirm: str | None = None,
    push_url: str | bool | None = None,
    include: str | None = None,
    select: str | None = None,
    select_oob: str | None = None,
    indicator: str | None = None,
) -> dict[str, Any]:
    """Build hyphenated HTMX attributes accepted by Hedron's HTML allowlist."""
    key = f"hx-{method.lower()}"
    attrs: dict[str, Any] = {key: hx_path(request, path)}
    if target is not None:
        attrs["hx-target"] = target
    if swap is not None:
        attrs["hx-swap"] = swap
    if trigger is not None:
        attrs["hx-trigger"] = trigger
    if sync is not None:
        attrs["hx-sync"] = sync
    if disabled_elt is not None:
        attrs["hx-disabled-elt"] = disabled_elt
    if confirm is not None:
        attrs["hx-confirm"] = confirm
    if push_url is not None:
        if push_url is True:
            attrs["hx-push-url"] = "true"
        elif push_url is False:
            attrs["hx-push-url"] = "false"
        else:
            attrs["hx-push-url"] = mounted_path(request, str(push_url))
    if include is not None:
        attrs["hx-include"] = include
    if select is not None:
        attrs["hx-select"] = select
    if select_oob is not None:
        attrs["hx-select-oob"] = select_oob
    if indicator is not None:
        attrs["hx-indicator"] = indicator
    return attrs
