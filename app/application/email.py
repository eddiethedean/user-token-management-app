"""Email delivery contracts independent of FastAPI and SMTP implementations."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class OutboundEmail:
    recipient: str
    subject: str
    body_text: str
    cc_recipient: str | None = None


class EmailTransport(Protocol):
    def send(self, message: OutboundEmail) -> None: ...
