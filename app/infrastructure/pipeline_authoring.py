"""Infrastructure adapter for the value-only pipeline authoring operation."""

from __future__ import annotations

import json

from sqlalchemy import select

from app.application.dto import ActorContext, PipelineSummary
from app.application.pipelines import PipelineAuthoringOperation, SavePipelineAuthoringCommand
from app.application.ports import RequestMetadata
from app.connectors.errors import ConnectorError, TransferErrorCode
from app.connectors.locators import parse_write_policy, postgres_table
from app.connectors.registry import capabilities_for, route_allowed, writer_enabled
from app.domain.pipelines import PipelineDraft, PipelinePolicy, PipelinePolicyError
from app.infrastructure.catalog_factory import build_user_catalog_with_metadata
from app.infrastructure.runtime import ExecutionRuntime
from app.services.catalogs import CREATE_TABLE_VALUE, NEW_TABLE_VALUE_PREFIX
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
                    owned_pipeline = None
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
                    creates_postgres_table = destination_provider == "postgres" and (
                        command.destination_table == CREATE_TABLE_VALUE
                        or command.destination_table.startswith(NEW_TABLE_VALUE_PREFIX)
                    )
                    if creates_postgres_table:
                        new_table_name = (
                            command.destination_table.removeprefix(NEW_TABLE_VALUE_PREFIX)
                            if command.destination_table.startswith(NEW_TABLE_VALUE_PREFIX)
                            else command.destination_table_new.strip()
                        )
                        same_saved_target = (
                            owned_pipeline is not None
                            and owned_pipeline.destination_create
                            and owned_pipeline.destination_provider == "postgres"
                            and owned_pipeline.destination_schema == command.destination_schema
                            and owned_pipeline.destination_table == new_table_name
                        )
                        if new_table_name and not same_saved_target:
                            try:
                                catalog.inspect_object(
                                    "postgres",
                                    postgres_table(command.destination_schema, new_table_name),
                                )
                            except ConnectorError as exc:
                                if exc.code != TransferErrorCode.SOURCE_NOT_FOUND:
                                    raise
                            else:
                                raise ValueError(
                                    "A PostgreSQL table with that name already exists. "
                                    "Choose a different name for the new table."
                                )
                        if (
                            same_saved_target
                            and write_mode == "append"
                            and owned_pipeline is not None
                        ):
                            saved_policy = parse_write_policy(
                                json.loads(owned_pipeline.write_policy_json)
                            )
                            saved_keys = tuple(
                                getattr(saved_policy, "primary_key_columns", ()) or ()
                            )
                            chosen_keys = tuple(
                                item.strip()
                                for item in command.primary_key_columns.split(",")
                                if item.strip()
                            )
                            saved_generated_key = str(
                                getattr(saved_policy, "auto_increment_primary_key", "") or ""
                            )
                            if (
                                saved_keys != chosen_keys
                                or saved_generated_key != command.auto_increment_primary_key.strip()
                            ):
                                try:
                                    catalog.inspect_object(
                                        "postgres",
                                        postgres_table(command.destination_schema, new_table_name),
                                    )
                                except ConnectorError as exc:
                                    if exc.code != TransferErrorCode.SOURCE_NOT_FOUND:
                                        raise
                                else:
                                    raise ValueError(
                                        "Append runs cannot change an existing table's primary key. "
                                        "Choose Replace destination to rebuild the table."
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
                        column_type_overrides=command.column_type_overrides,
                        primary_key_columns=command.primary_key_columns,
                        auto_increment_primary_key=command.auto_increment_primary_key,
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
