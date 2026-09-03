"""Canonical external links for email and other operator-facing messages."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from hedron_posit import compose_external_url, validate_external_base_url

from app.config import Settings


def public_url(
    settings: Settings,
    path: str,
    *,
    query: Mapping[str, object] | Sequence[tuple[str, object]] | None = None,
    fragment: str | None = None,
) -> str:
    """Build a validated URL beneath the configured external deployment base."""

    base = validate_external_base_url(settings.public_base_url)
    return compose_external_url(path, base=base, query=query, fragment=fragment)
