"""Small, framework-neutral ports used by application services."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

Clock = Callable[[], datetime]
Sleeper = Callable[[float], None]


@dataclass(frozen=True)
class RequestMetadata:
    """Request facts that application services may safely record or propagate."""

    request_id: str = ""
    source_ip: str = ""


def system_clock() -> datetime:
    """Return the current UTC time through an injectable application port."""

    return datetime.now(UTC).replace(tzinfo=None)


def system_sleep(seconds: float) -> None:
    """Sleep through an injectable application port."""

    time.sleep(seconds)
