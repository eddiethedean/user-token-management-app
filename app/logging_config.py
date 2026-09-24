"""Request-scoped structured logging helpers."""

from __future__ import annotations

import json
import logging
import os
import re
import sys
import traceback
from collections.abc import Mapping, Sequence
from contextvars import ContextVar
from typing import Any

from app.connectors.redaction import redact_mapping, redact_text

request_id_var: ContextVar[str] = ContextVar("request_id", default="-")
reference_id_var: ContextVar[str] = ContextVar("reference_id", default="-")
run_id_var: ContextVar[str] = ContextVar("run_id", default="-")

_DIAGNOSTIC_FIELDS = frozenset(
    {
        "event",
        "outcome",
        "error_code",
        "cause",
        "request_id",
        "reference_id",
        "user_id",
        "pipeline_id",
        "run_id",
        "attempt",
        "provider",
        "operation",
        "stage",
        "retryable",
        "data_impact",
        "duration_ms",
        "queued_at",
        "started_at",
        "finished_at",
        "run_status",
        "source_provider",
        "destination_provider",
        "source_rows",
        "source_bytes",
        "loaded_rows",
        "loaded_bytes",
        "destination_rows_before",
        "destination_rows_after",
        "destination_row_delta",
        "verification_level",
        "last_safe_stage",
        "reconciliation_required",
        "http_status",
        "provider_correlation_id",
        "sqlstate",
        "exception_type",
        "traceback",
        "method",
        "path",
        "status",
        "retry_after_seconds",
        "limit_dimension",
    }
)
_EVENT_REQUIRED_FIELDS: dict[str, tuple[str, ...]] = {
    "http.request.completed": (
        "request_id",
        "reference_id",
        "method",
        "path",
        "status",
        "duration_ms",
    ),
    "http.request.failed": (
        "request_id",
        "reference_id",
        "method",
        "path",
        "status",
        "duration_ms",
        "exception_type",
        "traceback",
    ),
    "pipeline.run.unexpected_failure": (
        "reference_id",
        "run_id",
        "user_id",
        "operation",
        "exception_type",
        "traceback",
    ),
    "auth.login.rejected": (
        "error_code",
        "reference_id",
        "operation",
        "exception_type",
    ),
    "auth.federated.rejected": (
        "error_code",
        "reference_id",
        "operation",
        "exception_type",
    ),
    "auth.registration.rejected": (
        "error_code",
        "reference_id",
        "operation",
        "exception_type",
    ),
    "auth.registration_verification.rejected": (
        "error_code",
        "reference_id",
        "operation",
        "exception_type",
    ),
    "auth.invitation.rejected": (
        "error_code",
        "reference_id",
        "operation",
        "exception_type",
    ),
    "auth.password_reset.rejected": (
        "error_code",
        "reference_id",
        "operation",
        "exception_type",
    ),
    "security.rate_limited": (
        "error_code",
        "reference_id",
        "operation",
        "retryable",
        "retry_after_seconds",
        "limit_dimension",
    ),
    "connection.test.completed": (
        "reference_id",
        "provider",
        "operation",
        "duration_ms",
    ),
    "connection.test.failed": (
        "error_code",
        "reference_id",
        "provider",
        "operation",
        "retryable",
        "duration_ms",
    ),
    "connection.save.failed": (
        "error_code",
        "reference_id",
        "provider",
        "operation",
        "exception_type",
    ),
    "pipeline.run.queued": (
        "reference_id",
        "run_id",
        "pipeline_id",
        "user_id",
        "attempt",
        "operation",
        "stage",
    ),
    "pipeline.run.completed": (
        "reference_id",
        "run_id",
        "pipeline_id",
        "user_id",
        "attempt",
        "operation",
        "stage",
        "provider",
        "duration_ms",
        "data_impact",
        "run_status",
        "queued_at",
        "started_at",
        "finished_at",
        "source_provider",
        "destination_provider",
        "source_rows",
        "source_bytes",
        "loaded_rows",
        "loaded_bytes",
        "destination_rows_before",
        "destination_rows_after",
        "destination_row_delta",
        "verification_level",
        "last_safe_stage",
        "reconciliation_required",
    ),
    "pipeline.run.failed": (
        "error_code",
        "reference_id",
        "run_id",
        "pipeline_id",
        "user_id",
        "attempt",
        "operation",
        "stage",
        "provider",
        "duration_ms",
        "retryable",
        "data_impact",
        "cause",
        "run_status",
        "queued_at",
        "started_at",
        "finished_at",
        "source_provider",
        "destination_provider",
        "source_rows",
        "source_bytes",
        "loaded_rows",
        "loaded_bytes",
        "destination_rows_before",
        "destination_rows_after",
        "destination_row_delta",
        "verification_level",
        "last_safe_stage",
        "reconciliation_required",
    ),
    "pipeline.run.cancelled": (
        "reference_id",
        "run_id",
        "pipeline_id",
        "attempt",
        "operation",
        "stage",
        "provider",
        "duration_ms",
        "data_impact",
        "cause",
    ),
    "pipeline.lease.heartbeat_failed": (
        "error_code",
        "reference_id",
        "run_id",
        "operation",
        "exception_type",
        "traceback",
    ),
    "feedback.mapping.unknown": (
        "error_code",
        "reference_id",
        "operation",
    ),
}
_NUMERIC_EVENT_FIELDS = frozenset(
    {
        "attempt",
        "duration_ms",
        "http_status",
        "retry_after_seconds",
        "status",
        "source_rows",
        "source_bytes",
        "loaded_rows",
        "loaded_bytes",
        "destination_rows_before",
        "destination_rows_after",
        "destination_row_delta",
    }
)
_BOOLEAN_EVENT_FIELDS = frozenset({"retryable", "reconciliation_required"})
_SAFE_CORRELATION = re.compile(r"[A-Za-z0-9._:-]{1,128}\Z")
_SAFE_SQLSTATE = re.compile(r"[0-9A-Z]{5}\Z")


class RequestIdFilter(logging.Filter):
    """Attach safe request and operation context to every log record."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get()  # type: ignore[attr-defined]
        if not getattr(record, "reference_id", ""):
            record.reference_id = reference_id_var.get()  # type: ignore[attr-defined]
        if not getattr(record, "run_id", ""):
            record.run_id = run_id_var.get()  # type: ignore[attr-defined]
        return True


class SafeFormatter(logging.Formatter):
    """Render allowlisted diagnostic fields and redact message text."""

    def __init__(self, *, json_mode: bool) -> None:
        super().__init__()
        self.json_mode = json_mode

    def format(self, record: logging.LogRecord) -> str:
        message = redact_text(record.getMessage())
        if not self.json_mode:
            values = [
                str(getattr(record, "request_id", "-")),
                record.levelname,
                record.name,
                message,
            ]
            event = getattr(record, "event", None)
            if event:
                values.append(f"event={event}")
            for name in sorted(_DIAGNOSTIC_FIELDS - {"event", "request_id"}):
                value = getattr(record, name, None)
                if value not in (None, "", "-"):
                    values.append(f"{name}={redact_text(str(_redact_log_value(value)))}")
            if record.exc_info:
                values.append(f"exception={redact_text(_safe_traceback(record))}")
            return " ".join(values)

        payload: dict[str, Any] = {
            "timestamp": self.formatTime(record, self.datefmt),
            "level": record.levelname,
            "logger": record.name,
            "message": message,
            "request_id": getattr(record, "request_id", "-"),
        }
        for name in _DIAGNOSTIC_FIELDS:
            if hasattr(record, name):
                value = getattr(record, name)
                payload[name] = _redact_log_value(value)
        if record.exc_info:
            payload["exception_type"] = type(record.exc_info[1]).__name__
            payload["traceback"] = redact_text(_safe_traceback(record))
        return json.dumps(payload, separators=(",", ":"), default=str)


def _safe_traceback(record: logging.LogRecord) -> str:
    """Keep stack locations while excluding arbitrary exception messages."""

    if not record.exc_info or record.exc_info[1] is None:
        return ""
    _, exception, tb = record.exc_info
    return safe_exception_traceback((type(exception), exception, tb))


def safe_exception_traceback(exc_info: tuple[type[BaseException], BaseException, Any]) -> str:
    """Render stack locations with the arbitrary exception message removed."""

    exception_type, _exception, tb = exc_info
    return "".join(traceback.format_tb(tb)) + f"{exception_type.__name__}: [redacted]"


def _redact_log_value(value: object) -> object:
    """Recursively redact structured diagnostic values before serialization."""

    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, Mapping):
        return redact_mapping(value)
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray, str)):
        return [_redact_log_value(item) for item in value]
    return value


def configure_logging(*, level: int | str | None = None, log_format: str | None = None) -> None:
    """Configure root logging once with a compact or JSON diagnostic format."""
    root = logging.getLogger()
    if getattr(root, "_access_registry_configured", False):
        return
    handler = logging.StreamHandler(sys.stderr)
    json_mode = (log_format or os.environ.get("LOG_FORMAT", "text")).casefold() == "json"
    handler.setFormatter(SafeFormatter(json_mode=json_mode))
    handler.addFilter(RequestIdFilter())
    root.handlers.clear()
    root.addHandler(handler)
    configured_level = os.environ.get("LOG_LEVEL", "INFO").upper() if level is None else level
    if isinstance(configured_level, str):
        configured_level = getattr(logging, configured_level, logging.INFO)
    root.setLevel(configured_level)
    root._access_registry_configured = True  # type: ignore[attr-defined]


def bind_request_id(request_id: str) -> None:
    request_id_var.set(request_id)


def bind_reference(*, reference_id: str = "", run_id: str = "") -> None:
    reference_id_var.set(reference_id or "-")
    run_id_var.set(run_id or "-")


def log_event(
    logger: logging.Logger,
    event: str,
    *,
    outcome: str,
    level: int | None = None,
    **fields: object,
) -> None:
    """Emit one allowlisted, redacted diagnostic event."""

    safe_fields: dict[str, object] = {}
    for key, value in fields.items():
        if key not in _DIAGNOSTIC_FIELDS:
            continue
        if key in {"provider_correlation_id", "reference_id", "request_id", "run_id"}:
            value = str(value) if value not in (None, "") else ""
            if value and not _SAFE_CORRELATION.fullmatch(value):
                value = "[invalid]"
        elif key == "sqlstate":
            value = str(value).upper() if value not in (None, "") else ""
            if value and not _SAFE_SQLSTATE.fullmatch(value):
                value = "[invalid]"
        safe_fields[key] = _redact_log_value(value)
    for key in _EVENT_REQUIRED_FIELDS.get(event, ()):
        if key in safe_fields:
            continue
        if key == "request_id":
            safe_fields[key] = request_id_var.get()
        elif key == "reference_id":
            safe_fields[key] = reference_id_var.get()
        elif key == "run_id":
            safe_fields[key] = run_id_var.get()
        elif key in _NUMERIC_EVENT_FIELDS:
            safe_fields[key] = 0
        elif key in _BOOLEAN_EVENT_FIELDS:
            safe_fields[key] = False
        else:
            safe_fields[key] = ""
    safe_fields["event"] = event
    safe_fields["outcome"] = outcome
    if level is None:
        if event == "connection.test.completed" or (
            event.startswith("auth.") and event.endswith(".rejected")
        ):
            level = logging.INFO if event == "connection.test.completed" else logging.ERROR
        else:
            level = (
                logging.ERROR
                if outcome.casefold() in {"failed", "error", "rejected"}
                else logging.WARNING
                if outcome.casefold() in {"uncertain", "incomplete", "rate_limited"}
                else logging.INFO
            )
    logger.log(level, "%s", event, extra=safe_fields)


def clear_request_id() -> None:
    request_id_var.set("-")
    reference_id_var.set("-")
    run_id_var.set("-")
