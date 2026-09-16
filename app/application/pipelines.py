"""Pipeline application commands with explicit policy dependencies."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from sqlalchemy.orm import Session

from app.connectors.locators import DefinitionSnapshot
from app.connectors.registry import route_allowed, writer_enabled
from app.models import PipelineDefinition, PipelineRun, User
from app.services.pipeline_runs import enqueue_run
from app.services.pipelines import save_pipeline

if TYPE_CHECKING:
    from fastapi import Request

RoutePolicy = Callable[[str, str], bool]
WriterPolicy = Callable[[str], bool]


@dataclass(frozen=True)
class PipelineDependencies:
    """External policy authorities used by pipeline commands."""

    route_policy: RoutePolicy = route_allowed
    writer_policy: WriterPolicy = writer_enabled


@dataclass(frozen=True)
class SavePipelineCommand:
    """Validated inputs for saving a pipeline definition."""

    user: User
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
    pipeline_id: str = ""
    request: Request | None = None


@dataclass(frozen=True)
class EnqueuePipelineCommand:
    """Validated inputs for enqueuing a pipeline run."""

    user: User
    pipeline: PipelineDefinition
    snapshot: DefinitionSnapshot
    attempt: int = 1
    parent_run_id: str | None = None
    idempotency_token: str | None = None
    request: Request | None = None


class PipelineCommands:
    """Application boundary for pipeline mutations.

    The web layer supplies policy authorities once, while the underlying
    service functions remain available as compatibility adapters during the
    repository migration.
    """

    def __init__(self, dependencies: PipelineDependencies | None = None) -> None:
        self.dependencies = dependencies or PipelineDependencies()

    def save(self, db: Session, command: SavePipelineCommand) -> PipelineDefinition:
        return save_pipeline(
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
            available_providers=command.available_providers,
            pipeline_id=command.pipeline_id,
            request=command.request,
        )

    def enqueue(self, db: Session, command: EnqueuePipelineCommand) -> PipelineRun:
        return enqueue_run(
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
