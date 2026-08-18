"""Central redaction for logs, run events, and connector errors."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

_BEARER = re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._\-+=/]+")
_PASSWORD_QUERY = re.compile(r"(?i)(password|passwd|pwd|token|secret|authorization)=([^&\s]+)")
_DSN = re.compile(r"(?i)(postgres(?:ql)?|postgresql\+psycopg)://([^:@/]+):([^@/]+)@")
_PEM = re.compile(r"-----BEGIN [A-Z ]+-----.*?-----END [A-Z ]+-----", re.S)

SENTINEL_REPLACEMENT = "[redacted]"
MAX_EVENT_DETAIL_CHARS = 2000


def redact_text(value: str) -> str:
    redacted = _BEARER.sub(rf"\1{SENTINEL_REPLACEMENT}", value)
    redacted = _PASSWORD_QUERY.sub(rf"\1={SENTINEL_REPLACEMENT}", redacted)
    redacted = _DSN.sub(rf"\1://\2:{SENTINEL_REPLACEMENT}@", redacted)
    redacted = _PEM.sub(SENTINEL_REPLACEMENT, redacted)
    if len(redacted) > MAX_EVENT_DETAIL_CHARS:
        return redacted[: MAX_EVENT_DETAIL_CHARS - 1] + "…"
    return redacted


def redact_mapping(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    if not payload:
        return {}
    redacted: dict[str, Any] = {}
    for key, value in payload.items():
        folded = key.casefold()
        if folded in {
            "token",
            "password",
            "secret",
            "authorization",
            "dsn",
            "ciphertext",
        }:
            redacted[key] = SENTINEL_REPLACEMENT
        elif isinstance(value, str):
            redacted[key] = redact_text(value)
        elif isinstance(value, Mapping):
            redacted[key] = redact_mapping(value)
        else:
            redacted[key] = value
    return redacted
