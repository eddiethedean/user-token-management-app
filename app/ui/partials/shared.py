"""Shared partial helpers."""

from __future__ import annotations

from urllib.parse import urlencode

from hedron import Pagination, html
from hedron_core import HtmlAttrValue, NodeLike

from app.ui.layout import alert_box


def field_error(field_id: str, message: str = "") -> NodeLike:
    """Inline field error slot (empty when valid)."""
    attrs: dict[str, HtmlAttrValue] = {
        "id": f"field-error-{field_id}",
        "class_": "field-error" + (" is-active" if message else ""),
    }
    if message:
        attrs["role"] = "alert"
    return html.div(message, **attrs)


def hedron_pagination(
    *,
    page: int,
    page_size: int,
    total: int,
    base_path: str,
    target: str,
) -> NodeLike:
    """Hedron Pagination builtin (innerHTML into a dedicated body region)."""
    pages = max(1, (total + page_size - 1) // page_size) if total else 1
    if pages <= 1:
        return html.div()
    return Pagination(
        page=page,
        page_size=page_size,
        total=total,
        base_path=base_path,
        target=target,
    )


def _filter_base_path(path: str, **params: str) -> str:
    cleaned = {key: value for key, value in params.items() if value}
    if not cleaned:
        return path
    return f"{path}?{urlencode(cleaned)}"


def request_error(message: str) -> NodeLike:
    return alert_box(message or "The request could not be completed.")
