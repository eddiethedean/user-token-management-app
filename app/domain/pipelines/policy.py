"""Pure validation for the common portion of pipeline authoring.

Provider-specific locator construction remains in connector strategies. This
module deliberately has no SQLAlchemy, FastAPI, settings, or connector imports.
"""

from __future__ import annotations

from collections.abc import Callable, Collection
from dataclasses import dataclass


class PipelinePolicyError(ValueError):
    """An authoring draft violates a pipeline policy."""


@dataclass(frozen=True)
class PipelineDraft:
    name: str
    source_provider: str
    destination_provider: str
    write_mode: str
    available_providers: frozenset[str] = frozenset()

    def normalized(self) -> PipelineDraft:
        return PipelineDraft(
            name=normalize_pipeline_name(self.name),
            source_provider=self.source_provider.strip().casefold(),
            destination_provider=self.destination_provider.strip().casefold(),
            write_mode=self.write_mode.strip().casefold(),
            available_providers=frozenset(
                item.strip().casefold() for item in self.available_providers
            ),
        )


def normalize_pipeline_name(value: str) -> str:
    normalized = " ".join(value.split())
    if len(normalized) < 3:
        raise PipelinePolicyError("Give this pipeline a name with at least 3 characters.")
    return normalized[:120]


@dataclass(frozen=True)
class PipelinePolicy:
    """Capability and operator policy supplied by the composition root."""

    route_allowed: Callable[[str, str], bool]
    writer_enabled: Callable[[str], bool]
    write_modes_for: Callable[[str], Collection[str]]

    def validate(self, draft: PipelineDraft) -> PipelineDraft:
        normalized = draft.normalized()
        if not self.route_allowed(normalized.source_provider, normalized.destination_provider):
            raise PipelinePolicyError("Select a supported source and destination.")
        if (
            normalized.source_provider != "csv"
            and normalized.source_provider not in normalized.available_providers
        ):
            raise PipelinePolicyError(
                "Configure and validate the selected source connection before saving."
            )
        if normalized.destination_provider not in normalized.available_providers:
            raise PipelinePolicyError(
                "Configure and validate the selected destination connection before saving."
            )
        if not self.writer_enabled(normalized.destination_provider):
            raise PipelinePolicyError(
                "The selected destination writer is not enabled by the operator."
            )
        supported = tuple(self.write_modes_for(normalized.destination_provider))
        if normalized.write_mode not in supported:
            choices = ", ".join(supported) or "no write modes"
            raise PipelinePolicyError(f"The selected destination supports {choices}.")
        return normalized
