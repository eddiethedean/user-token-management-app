"""Transport-neutral command and result values used by application services."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ActorContext:
    """Facts an application use case may record without depending on a request."""

    user_id: str
    request_id: str = ""
    source_ip: str = ""


@dataclass(frozen=True)
class PipelineSummary:
    id: str
    name: str
    source_provider: str
    destination_provider: str
    write_mode: str


@dataclass(frozen=True)
class RunSummary:
    id: str
    pipeline_id: str
    status: str
    stage: str
