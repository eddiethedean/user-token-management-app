"""Small, framework-neutral ports used by application services."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

Clock = Callable[[], datetime]
Sleeper = Callable[[float], None]


@dataclass(frozen=True)
class RequestMetadata:
    """Request facts that application services may safely record or propagate."""

    request_id: str = ""
    source_ip: str = ""
    reference_id: str = ""


class CredentialResolver(Protocol):
    """Resolve one owner-authorized bundle for a specific execution purpose."""

    def __call__(self, *, user_id: str, provider: str, purpose: str) -> dict[str, str]: ...


class RunControl(Protocol):
    """Small execution port for lease and cancellation checks."""

    def cancellation_requested(self, run_id: str) -> bool: ...

    def lease_lost(self, run_id: str, lease_token: str) -> bool: ...


class CatalogCache(Protocol):
    """Owner-scoped catalog cache used by the catalog application service."""

    def get(self, provider: str, namespace: str) -> dict[str, Any] | None: ...

    def put(self, provider: str, namespace: str, payload: dict[str, Any]) -> None: ...


def system_clock() -> datetime:
    """Return the current UTC time through an injectable application port."""

    return datetime.now(UTC).replace(tzinfo=None)


def system_sleep(seconds: float) -> None:
    """Sleep through an injectable application port."""

    time.sleep(seconds)
