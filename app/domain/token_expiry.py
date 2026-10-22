"""Advisory token-expiry metadata; never an authentication or authorization decision."""

from __future__ import annotations

import base64
import binascii
import json
import math
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime


@dataclass(frozen=True)
class TokenExpiry:
    expires_at: datetime | None = None
    source: str = "unknown"


def parse_expiry_input(value: str) -> datetime:
    """Accept an ISO datetime, interpreting an omitted timezone as UTC."""

    try:
        if "T" not in value:
            raise ValueError
        parsed = datetime.fromisoformat(value)
        return parsed if parsed.tzinfo is None else parsed.astimezone(UTC).replace(tzinfo=None)
    except (ValueError, OverflowError) as exc:
        raise ValueError("Token expiry must be a valid date and time in UTC.") from exc


def expiry_from_credentials(credentials: Mapping[str, str]) -> TokenExpiry:
    """Use an optional user date or an explicitly unverified compact-JWT exp claim."""

    if not credentials.get("token"):
        return TokenExpiry()
    if entered := credentials.get("token_expires_at"):
        return TokenExpiry(parse_expiry_input(entered), "user")
    token = credentials["token"]
    if len(token) > 8192 or token.count(".") != 2:
        return TokenExpiry()
    try:
        payload = token.split(".")[1]
        decoded = base64.b64decode(
            payload + "=" * (-len(payload) % 4), altchars=b"-_", validate=True
        )
        claims = json.loads(decoded)
        value = claims.get("exp") if isinstance(claims, dict) else None
        if (
            not isinstance(value, (int, float))
            or isinstance(value, bool)
            or not math.isfinite(value)
        ):
            return TokenExpiry()
        expires_at = datetime.fromtimestamp(value, UTC).replace(tzinfo=None)
        return TokenExpiry(expires_at, "token_claim")
    except (ValueError, TypeError, OverflowError, OSError, binascii.Error, RecursionError):
        return TokenExpiry()
