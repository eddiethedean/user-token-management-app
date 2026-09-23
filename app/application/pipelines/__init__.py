"""Framework-neutral pipeline commands and use cases.

The lazy compatibility exports point old route code at infrastructure only
when that code is used. Keeping the import path avoids a flag-day migration
without making ORM or HTTP types part of this package's module imports.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from app.application.pipelines.authoring import (
    PipelineAuthoringOperation,
    SavePipelineAuthoringCommand,
)
from app.application.pipelines.use_cases import PipelineUseCases, SavePipelineInput


def _route_allowed(source: str, destination: str) -> bool:
    from app.connectors.registry import route_allowed

    return route_allowed(source, destination)


def _writer_enabled(provider: str) -> bool:
    from app.connectors.registry import writer_enabled

    return writer_enabled(provider)


@dataclass(frozen=True)
class PipelineDependencies:
    route_policy: Callable[[str, str], bool] = _route_allowed
    writer_policy: Callable[[str], bool] = _writer_enabled


@dataclass(frozen=True)
class SavePipelineCommand:
    user: Any
    name: str
    source_provider: str
    destination_provider: str
    write_mode: str
    available_providers: set[str]
    source_schema: str = ""
    source_table: str = ""
    source_branch: str = "master"
    destination_schema: str = ""
    destination_table: str = ""
    destination_branch: str = "master"
    destination_table_new: str = ""
    source_upload_id: str = ""
    conflict_columns: str = ""
    column_type_overrides: dict[str, str] | None = None
    primary_key_columns: str = ""
    auto_increment_primary_key: str = ""
    pipeline_id: str = ""
    request: Any = None


@dataclass(frozen=True)
class EnqueuePipelineCommand:
    user: Any
    pipeline: Any
    snapshot: Any
    attempt: int = 1
    parent_run_id: str | None = None
    idempotency_token: str | None = None
    request: Any = None


class PipelineCommands:
    """Compatibility adapter with policy dependencies supplied by callers."""

    def __init__(self, dependencies: PipelineDependencies | None = None) -> None:
        self.dependencies = dependencies or PipelineDependencies()

    def save(self, db: Any, command: SavePipelineCommand) -> Any:
        save_fn = globals().get("save_pipeline")
        if save_fn is None:
            from app.infrastructure.persistence.legacy_pipeline import save_pipeline as save_fn

        return save_fn(
            db,
            route_policy=self.dependencies.route_policy,
            writer_policy=self.dependencies.writer_policy,
            user=command.user,
            name=command.name,
            source_provider=command.source_provider,
            source_schema=command.source_schema,
            source_table=command.source_table,
            source_branch=command.source_branch,
            destination_provider=command.destination_provider,
            destination_schema=command.destination_schema,
            destination_table=command.destination_table,
            destination_branch=command.destination_branch,
            destination_table_new=command.destination_table_new,
            source_upload_id=command.source_upload_id,
            write_mode=command.write_mode,
            conflict_columns=command.conflict_columns,
            column_type_overrides=command.column_type_overrides,
            primary_key_columns=command.primary_key_columns,
            auto_increment_primary_key=command.auto_increment_primary_key,
            available_providers=command.available_providers,
            pipeline_id=command.pipeline_id,
            request=command.request,
        )

    def enqueue(self, db: Any, command: EnqueuePipelineCommand) -> Any:
        enqueue_fn = globals().get("enqueue_run")
        if enqueue_fn is None:
            from app.infrastructure.persistence.legacy_pipeline import enqueue_run as enqueue_fn

        return enqueue_fn(
            db,
            route_policy=self.dependencies.route_policy,
            writer_policy=self.dependencies.writer_policy,
            user=command.user,
            pipeline=command.pipeline,
            snapshot=command.snapshot,
            attempt=command.attempt,
            parent_run_id=command.parent_run_id,
            idempotency_token=command.idempotency_token,
            request=command.request,
        )


def __getattr__(name: str):
    if name in {"save_pipeline", "enqueue_run"}:
        from app.infrastructure.persistence import legacy_pipeline

        return getattr(legacy_pipeline, name)
    raise AttributeError(name)


__all__ = [
    "EnqueuePipelineCommand",
    "PipelineCommands",
    "PipelineDependencies",
    "PipelineAuthoringOperation",
    "PipelineUseCases",
    "SavePipelineCommand",
    "SavePipelineAuthoringCommand",
    "SavePipelineInput",
]
