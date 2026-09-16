"""Infrastructure adapter for the value-only pipeline authoring operation."""

from __future__ import annotations

from sqlalchemy import select

from app.application.dto import ActorContext, PipelineSummary
from app.application.pipelines import PipelineAuthoringOperation, SavePipelineAuthoringCommand
from app.application.ports import RequestMetadata
from app.connectors.locators import postgres_table
from app.connectors.registry import capabilities_for, route_allowed, writer_enabled
from app.domain.pipelines import PipelineDraft, PipelinePolicy, PipelinePolicyError
from app.infrastructure.catalog_factory import build_user_catalog_with_metadata
from app.infrastructure.runtime import ExecutionRuntime
from app.services.catalogs import CREATE_TABLE_VALUE
from app.services.pipelines import save_pipeline
from app.services.secrets import list_user_secrets


class SqlAlchemyPipelineAuthoringOperation(PipelineAuthoringOperation):
    """Execute authoring validation and persistence in one owned session."""

    def __init__(self, runtime: ExecutionRuntime) -> None:
        self._runtime = runtime

    def __call__(
        self, actor: ActorContext, command: SavePipelineAuthoringCommand
    ) -> PipelineSummary:
        with self._runtime.operation_scope():
            with self._runtime.context():
                with self._runtime.sessions() as db:
                    from app.models import User

                    user = db.get(User, actor.user_id)
                    if user is None:
                        raise PermissionError("The requested user does not exist.")
                    if not user.is_active:
                        raise PermissionError("The requested user is not active.")
                    settings = self._runtime.execution_settings
                    available_providers = {
                        provider.name
                        for provider, secret in list_user_secrets(db, user)
                        if secret is not None and secret.validation_status == "connected"
                    }
                    try:
                        validated = PipelinePolicy(
                            route_allowed=route_allowed,
                            writer_enabled=lambda provider: writer_enabled(
                                provider, settings=settings
                            ),
                            write_modes_for=lambda provider: capabilities_for(provider).write_modes,
                        ).validate(
                            PipelineDraft(
                                name=command.name,
                                source_provider=command.source_provider,
                                destination_provider=command.destination_provider,
                                write_mode=command.write_mode,
                                available_providers=frozenset(available_providers),
                            )
                        )
                    except PipelinePolicyError as exc:
                        raise ValueError(str(exc)) from exc

                    source_provider = validated.source_provider
                    destination_provider = validated.destination_provider
                    write_mode = validated.write_mode
                    if command.pipeline_id:
                        from app.models import PipelineDefinition

                        owned_pipeline = db.scalar(
                            select(PipelineDefinition).where(
                                PipelineDefinition.id == command.pipeline_id,
                                PipelineDefinition.user_id == user.id,
                            )
                        )
                        if owned_pipeline is None:
                            raise ValueError("That saved pipeline is no longer available.")
                    if source_provider == "csv":
                        from app.models import PipelineUpload

                        if not command.source_upload_id:
                            raise ValueError(
                                "Upload and inspect a CSV file before saving this pipeline."
                            )
                        owned_upload = db.scalar(
                            select(PipelineUpload).where(
                                PipelineUpload.id == command.source_upload_id,
                                PipelineUpload.user_id == user.id,
                            )
                        )
                        if owned_upload is None:
                            raise ValueError("That CSV upload is no longer available.")

                    metadata = RequestMetadata(actor.request_id, actor.source_ip)
                    catalog = build_user_catalog_with_metadata(db, settings, user, metadata)
                    source_branch = (
                        catalog.default_branch(source_provider)
                        if source_provider != "csv" and source_provider in available_providers
                        else ""
                    )
                    destination_branch = (
                        catalog.branch_for_namespace(
                            destination_provider, command.destination_schema
                        )
                        if destination_provider in available_providers
                        else ""
                    )
                    selected_conflict_columns = command.conflict_columns
                    if (
                        destination_provider == "postgres"
                        and destination_provider in available_providers
                        and write_mode == "upsert"
                    ):
                        if command.destination_table == CREATE_TABLE_VALUE:
                            raise ValueError(
                                "Create the PostgreSQL table before configuring an upsert."
                            )
                        details = catalog.inspect_object(
                            "postgres",
                            postgres_table(command.destination_schema, command.destination_table),
                        )
                        eligible_keys = {
                            tuple(details.primary_key),
                            *map(tuple, details.unique_constraints),
                        }
                        eligible_keys.discard(())
                        selected_key = tuple(
                            item.strip()
                            for item in command.conflict_columns.split(",")
                            if item.strip()
                        )
                        if not selected_key and details.primary_key:
                            selected_key = tuple(details.primary_key)
                        if not selected_key or selected_key not in eligible_keys:
                            raise ValueError(
                                "Select a current primary or unique key for the PostgreSQL upsert."
                            )
                        selected_conflict_columns = ",".join(selected_key)

                    saved = save_pipeline(
                        db,
                        user=user,
                        name=validated.name,
                        source_provider=source_provider,
                        source_schema=command.source_schema,
                        source_table=command.source_table,
                        source_branch=source_branch,
                        destination_provider=destination_provider,
                        destination_schema=command.destination_schema,
                        destination_table=command.destination_table,
                        destination_branch=destination_branch,
                        destination_table_new=command.destination_table_new,
                        source_upload_id=command.source_upload_id,
                        write_mode=write_mode,
                        conflict_columns=selected_conflict_columns,
                        available_providers=available_providers,
                        pipeline_id=command.pipeline_id,
                        request=metadata,
                    )
                    return PipelineSummary(
                        id=saved.id,
                        name=saved.name,
                        source_provider=saved.source_provider,
                        destination_provider=saved.destination_provider,
                        write_mode=saved.write_mode,
                    )


def build_pipeline_authoring_operation(
    runtime: ExecutionRuntime,
) -> PipelineAuthoringOperation:
    return SqlAlchemyPipelineAuthoringOperation(runtime)
