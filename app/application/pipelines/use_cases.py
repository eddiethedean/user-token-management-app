"""Pipeline use cases expressed in terms of small consumer-owned ports.

The SQLAlchemy adapter in ``app.services.pipelines`` remains available while
callers migrate. The use case itself only coordinates values and ports.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from app.application.dto import ActorContext, PipelineSummary, RunSummary
from app.domain.pipelines import PipelineDraft, PipelinePolicy


class PipelineStore(Protocol):
    def save_pipeline(self, *, actor: ActorContext, draft: PipelineDraft) -> PipelineSummary: ...

    def enqueue_pipeline(
        self, *, actor: ActorContext, pipeline_id: str, idempotency_token: str | None = None
    ) -> RunSummary: ...


@dataclass(frozen=True)
class SavePipelineInput:
    name: str
    source_provider: str
    destination_provider: str
    write_mode: str
    available_providers: frozenset[str] = frozenset()

    def as_draft(self) -> PipelineDraft:
        return PipelineDraft(
            name=self.name,
            source_provider=self.source_provider,
            destination_provider=self.destination_provider,
            write_mode=self.write_mode,
            available_providers=self.available_providers,
        )


class PipelineUseCases:
    """Authorize policy through an injected port, then persist the result."""

    def __init__(self, store: PipelineStore, policy: PipelinePolicy) -> None:
        self._store = store
        self._policy = policy

    def save(self, *, actor: ActorContext, command: SavePipelineInput) -> PipelineSummary:
        draft = self._policy.validate(command.as_draft())
        return self._store.save_pipeline(actor=actor, draft=draft)

    def enqueue(
        self,
        *,
        actor: ActorContext,
        pipeline_id: str,
        idempotency_token: str | None = None,
    ) -> RunSummary:
        if not pipeline_id.strip():
            raise ValueError("A pipeline is required.")
        return self._store.enqueue_pipeline(
            actor=actor,
            pipeline_id=pipeline_id,
            idempotency_token=idempotency_token,
        )
