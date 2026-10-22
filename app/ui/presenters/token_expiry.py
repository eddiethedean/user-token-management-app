"""Display only advisory dates and provenance, never token payloads."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal


@dataclass(frozen=True)
class TokenExpiryView:
    summary: str
    badge: str = ""
    tone: Literal["neutral", "warning", "danger"] = "neutral"


def token_expiry_view(secret: Any, *, now: datetime | None = None) -> TokenExpiryView:
    source = getattr(secret, "token_expiry_source", "unknown")
    expires_at = getattr(secret, "token_expires_at", None)
    if source == "provider_no_expiry":
        return TokenExpiryView("Token expiry: No scheduled expiry reported by Databricks.")
    if not isinstance(expires_at, datetime):
        return TokenExpiryView("Token expiry: Unknown. You can enter the date from the token page.")
    source_label = {
        "provider": "reported by Databricks",
        "token_claim": "unverified token claim",
        "user": "user supplied",
    }.get(source, "unknown source")
    if expires_at.tzinfo is not None:
        expires_at = expires_at.astimezone(UTC).replace(tzinfo=None)
    instant = now or datetime.now(UTC)
    instant = instant.replace(tzinfo=UTC) if instant.tzinfo is None else instant
    instant = instant.astimezone(UTC).replace(tzinfo=None)
    summary = f"Token expiry: {expires_at:%Y-%m-%d %H:%M} UTC ({source_label})."
    if expires_at <= instant:
        return TokenExpiryView(summary, "Expiry date passed", "danger")
    if expires_at <= instant + timedelta(days=7):
        return TokenExpiryView(summary, "Expires within 7 days", "warning")
    return TokenExpiryView(summary)
