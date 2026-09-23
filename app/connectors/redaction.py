"""Central redaction for logs, run events, and connector errors."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

_BEARER = re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._\-+=/]+")
_AUTH_HEADER = re.compile(
    r"(?i)(\b(?:authorization|proxy-authorization|x-api-key|x-auth-token|cookie|set-cookie)\s*:\s*)[^\r\n]+"
)
_AUTH_SCHEME = re.compile(
    r"(?i)(\b(?:authorization|proxy-authorization)\s*[:=]\s*)(?:basic|bearer)\s+[^,;\s}]+"
)
_CREDENTIAL_NAME = (
    r"(?:"
    r"api[-_]?key|api[-_]?secret|api[-_]?token|"
    r"access[-_]?key|access[-_]?secret|access[-_]?token|"
    r"auth[-_]?key|auth[-_]?secret|auth[-_]?token|"
    r"client[-_]?secret|client[-_]?token|"
    r"credential(?:s)?(?:[-_](?:key|secret|token))?|"
    r"id[-_]?token|private[-_]?key|private[-_]?secret|private[-_]?token|"
    r"refresh[-_]?token|session[-_]?cookie|session[-_]?token|"
    r"secret[-_]?key|secret[-_]?token|token[-_]?secret|"
    r"x[-_]?api[-_]?key|x[-_]?auth[-_]?token|"
    r"authorization|cookie|password|passwd|pwd|secret|token|set[-_]?cookie"
    r")"
)
_CREDENTIAL_QUERY = re.compile(rf"(?i)([?&]{_CREDENTIAL_NAME}=)([^&#\s]+)")
_CREDENTIAL_ASSIGNMENT = re.compile(rf"(?i)(\b{_CREDENTIAL_NAME}\s*[:=]\s*)([^,;\s]+)")
_QUOTED_KEY_CREDENTIAL_ASSIGNMENT = re.compile(
    rf"(?i)([\"']{_CREDENTIAL_NAME}[\"']\s*[:=]\s*)([\"'])(?:\\.|(?!\2).)*\2"
)
_QUOTED_CREDENTIAL_ASSIGNMENT = re.compile(
    rf"(?i)(\b{_CREDENTIAL_NAME}\s*[:=]\s*)([\"'])(?:\\.|(?!\2).)*\2"
)
_DSN = re.compile(
    r"(?i)(\b(?:postgres(?:ql)?(?:\+[^:/\s]+)?|mysql(?:\+[^:/\s]+)?|mongodb(?:\+srv)?|mssql(?:\+[^:/\s]+)?|redshift)://)([^/@\s:]+)(:)([^@\s]+)(@)"
)
_URL_USERINFO = re.compile(r"(?i)(https?://)([^/@\s:]+)(:)([^@\s]+)(@)")
_PEM = re.compile(r"-----BEGIN [A-Z ]+-----.*?-----END [A-Z ]+-----", re.S)

SENTINEL_REPLACEMENT = "[redacted]"
MAX_EVENT_DETAIL_CHARS = 2000


def redact_text(value: str) -> str:
    redacted = _BEARER.sub(rf"\1{SENTINEL_REPLACEMENT}", value)
    redacted = _AUTH_HEADER.sub(rf"\1{SENTINEL_REPLACEMENT}", redacted)
    redacted = _AUTH_SCHEME.sub(rf"\1{SENTINEL_REPLACEMENT}", redacted)
    redacted = _CREDENTIAL_QUERY.sub(rf"\1{SENTINEL_REPLACEMENT}", redacted)
    redacted = _QUOTED_KEY_CREDENTIAL_ASSIGNMENT.sub(rf"\1\2{SENTINEL_REPLACEMENT}\2", redacted)
    redacted = _QUOTED_CREDENTIAL_ASSIGNMENT.sub(rf"\1\2{SENTINEL_REPLACEMENT}\2", redacted)
    redacted = _CREDENTIAL_ASSIGNMENT.sub(rf"\1{SENTINEL_REPLACEMENT}", redacted)
    redacted = _DSN.sub(rf"\1\2\3{SENTINEL_REPLACEMENT}\5", redacted)
    redacted = _URL_USERINFO.sub(rf"\1\2\3{SENTINEL_REPLACEMENT}\5", redacted)
    redacted = _PEM.sub(SENTINEL_REPLACEMENT, redacted)
    if len(redacted) > MAX_EVENT_DETAIL_CHARS:
        return redacted[: MAX_EVENT_DETAIL_CHARS - 1] + "…"
    return redacted


def _secret_key(key: str) -> bool:
    folded = re.sub(r"[-_\s]", "", key.casefold())
    return folded in {
        "token",
        "accesstoken",
        "authtoken",
        "cookie",
        "password",
        "passwd",
        "pwd",
        "secret",
        "secretkey",
        "apikey",
        "accesskey",
        "clientsecret",
        "authorization",
        "idtoken",
        "privatekey",
        "refreshtoken",
        "sessioncookie",
        "sessiontoken",
        "setcookie",
        "credential",
        "credentials",
        "dsn",
        "ciphertext",
    } or folded.endswith(
        ("token", "secret", "password", "credential", "cookie", "apikey", "accesskey", "privatekey")
    )


def _redact_value(value: Any) -> Any:
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, Mapping):
        return redact_mapping(value)
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray, str)):
        return [_redact_value(item) for item in value]
    return value


def redact_mapping(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    if not payload:
        return {}
    redacted: dict[str, Any] = {}
    for key, value in payload.items():
        if _secret_key(str(key)):
            redacted[key] = SENTINEL_REPLACEMENT
        else:
            redacted[key] = _redact_value(value)
    return redacted
