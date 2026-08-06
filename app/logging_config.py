"""Request-scoped logging helpers."""

from __future__ import annotations

import logging
import sys
from contextvars import ContextVar

request_id_var: ContextVar[str] = ContextVar("request_id", default="-")


class RequestIdFilter(logging.Filter):
    """Attach the current request id to every log record."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get()  # type: ignore[attr-defined]
        return True


def configure_logging(*, level: int = logging.INFO) -> None:
    """Configure root logging once with a compact request-id-aware format."""
    root = logging.getLogger()
    if getattr(root, "_access_registry_configured", False):
        return
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("%(request_id)s %(levelname)s %(name)s %(message)s"))
    handler.addFilter(RequestIdFilter())
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)
    root._access_registry_configured = True  # type: ignore[attr-defined]


def bind_request_id(request_id: str) -> None:
    request_id_var.set(request_id)


def clear_request_id() -> None:
    request_id_var.set("-")
