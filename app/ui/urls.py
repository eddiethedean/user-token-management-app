"""Safe URL helpers for Hedron HTML attributes."""

from __future__ import annotations

from typing import Any
from urllib.parse import parse_qsl, urlsplit

from fastapi import Request
from hedron_core.security import SafeUrl, UrlPurpose
from hedron_posit import browser_mount_from_request, local_href


def _abs_path(path: str) -> str:
    """Normalize to an absolute app path (SafeUrl rejects relative paths)."""
    if not path:
        return "/"
    if path.startswith("/"):
        return path
    return f"/{path}"


def _browser_mount(request: Request) -> str:
    """Resolve a mount for both live requests and bare component scopes."""
    if "app" not in request.scope:
        return str(request.scope.get("root_path") or "")
    return browser_mount_from_request(request)


def mounted_path(request: Request, path: str) -> str:
    """Application path prefixed with the external deployment mount."""
    parsed = urlsplit(path)
    query = parse_qsl(parsed.query, keep_blank_values=True)
    mounted = local_href(
        _abs_path(parsed.path),
        mount=_browser_mount(request),
        query=query or None,
        fragment=parsed.fragment or None,
    )
    # Hedron rejects paths that normalize only by dropping a terminal slash. The
    # root app URL is the one exception: without a mount it must remain "/".
    normalized = mounted.rstrip("/") or "/"
    return normalized


def page_href(request: Request, path: str) -> SafeUrl:
    return SafeUrl.parse(mounted_path(request, path), purpose=UrlPurpose.NAVIGATION)


def form_action(request: Request, path: str) -> SafeUrl:
    return SafeUrl.parse(mounted_path(request, path), purpose=UrlPurpose.FORM_ACTION)


def hx_path(request: Request, path: str) -> SafeUrl:
    # HTMX navigation attributes use Hedron's navigation-safe URL purpose.
    return SafeUrl.parse(mounted_path(request, path), purpose=UrlPurpose.NAVIGATION)


def asset_href(request: Request, path: str) -> SafeUrl:
    # A stylesheet's HTML ``href`` is a navigation URL even though it points to
    # an asset resource.
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
    hx_trigger: str | None = None,
    hx_ext: str | None = None,
    polling: str | float | int | None = None,
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
    if hx_trigger is not None:
        attrs["hx-trigger"] = hx_trigger
    if hx_ext is not None:
        attrs["hx-ext"] = hx_ext
    if polling is not None:
        # Numeric polling values are seconds. Bare HTMX timing numbers are
        # interpreted as milliseconds, which can turn a 45-second refresh
        # into a tight request loop.
        interval = f"{polling:g}s" if isinstance(polling, (int, float)) else polling.strip()
        if not interval.startswith("every "):
            attrs["hx-trigger"] = f"every {interval}"
        else:
            attrs["hx-trigger"] = interval
    if indicator is not None:
        attrs["hx-indicator"] = indicator
    return attrs
