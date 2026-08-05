"""Safe URL helpers for Hedron HTML attributes."""

from __future__ import annotations

from typing import Any

from hedron_core.security import SafeUrl, UrlPurpose


def _abs_path(path: str) -> str:
    """Normalize to an absolute app path (SafeUrl rejects relative paths)."""
    if not path:
        return "/"
    if path.startswith("/"):
        return path
    return f"/{path}"


def page_href(path: str) -> SafeUrl:
    return SafeUrl.parse(_abs_path(path), purpose=UrlPurpose.NAVIGATION)


def form_action(path: str) -> SafeUrl:
    return SafeUrl.parse(_abs_path(path), purpose=UrlPurpose.FORM_ACTION)


def hx_path(path: str) -> SafeUrl:
    # Hedron requires purpose=navigation for hx-* URL attributes.
    return SafeUrl.parse(_abs_path(path), purpose=UrlPurpose.NAVIGATION)


def hx_attrs(
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
    indicator: str | None = None,
) -> dict[str, Any]:
    """Build hyphenated HTMX attributes accepted by Hedron's HTML allowlist."""
    key = f"hx-{method.lower()}"
    attrs: dict[str, Any] = {key: hx_path(path)}
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
        attrs["hx-push-url"] = "true" if push_url is True else str(push_url)
    if include is not None:
        attrs["hx-include"] = include
    if select is not None:
        attrs["hx-select"] = select
    if indicator is not None:
        attrs["hx-indicator"] = indicator
    return attrs
