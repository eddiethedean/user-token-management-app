"""Shared partial helpers."""

from __future__ import annotations

from urllib.parse import urlencode

from fastapi import Request
from hedron import Alert, Fragment, Pagination

from app.ui.layout import alert_box


def hedron_pagination(
    *,
    page: int,
    page_size: int,
    total: int,
    base_path: str,
    target: str,
) -> Pagination | Fragment:
    """Hedron Pagination builtin (innerHTML into a dedicated body region)."""
    pages = max(1, (total + page_size - 1) // page_size) if total else 1
    if pages <= 1:
        return Fragment()
    return Pagination(
        page=page,
        page_size=page_size,
        total=total,
        base_path=base_path,
        target=target,
    )


def _filter_base_path(request: Request, path: str, **params: str) -> str:
    from app.ui.urls import mounted_path

    cleaned = {key: value for key, value in params.items() if value}
    base = mounted_path(request, path)
    if not cleaned:
        return base
    return f"{base}?{urlencode(cleaned)}"


def request_error(message: str) -> Alert | Fragment:
    return alert_box(message or "The request could not be completed.")
