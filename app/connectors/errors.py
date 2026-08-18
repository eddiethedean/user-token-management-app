"""Stable transfer error codes. UI and audit rows receive only these plus a sanitized summary."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class TransferErrorCode(StrEnum):
    CREDENTIALS_MISSING = "credentials_missing"
    CREDENTIALS_STALE = "credentials_stale"
    AUTHENTICATION_FAILED = "authentication_failed"
    PERMISSION_DENIED = "permission_denied"
    ENDPOINT_BLOCKED = "endpoint_blocked"
    TLS_FAILED = "tls_failed"
    CONNECTION_TIMEOUT = "connection_timeout"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    RATE_LIMITED = "rate_limited"
    SOURCE_NOT_FOUND = "source_not_found"
    DESTINATION_NOT_FOUND = "destination_not_found"
    SCHEMA_DRIFT = "schema_drift"
    UNSUPPORTED_TYPE = "unsupported_type"
    SOURCE_LIMIT_EXCEEDED = "source_limit_exceeded"
    SPOOL_LIMIT_EXCEEDED = "spool_limit_exceeded"
    RUN_TIMEOUT = "run_timeout"
    DESTINATION_CONFLICT = "destination_conflict"
    PARTIAL_WRITE = "partial_write"
    PUBLISH_UNCERTAIN = "publish_uncertain"
    VERIFICATION_FAILED = "verification_failed"
    CANCELLED_BY_USER = "cancelled_by_user"
    WORKER_LOST = "worker_lost"
    INTERNAL_ERROR = "internal_error"


RETRYABLE_CODES = frozenset(
    {
        TransferErrorCode.PROVIDER_UNAVAILABLE,
        TransferErrorCode.RATE_LIMITED,
        TransferErrorCode.CONNECTION_TIMEOUT,
        TransferErrorCode.WORKER_LOST,
    }
)


@dataclass
class ConnectorError(Exception):
    code: TransferErrorCode
    summary: str
    retryable: bool | None = None
    provider_correlation_id: str = ""

    def __post_init__(self) -> None:
        if self.retryable is None:
            self.retryable = self.code in RETRYABLE_CODES

    def __str__(self) -> str:
        return self.summary
