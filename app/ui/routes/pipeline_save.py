"""HTTP registration for saved pipeline-definition mutations."""

from __future__ import annotations

import asyncio

from fastapi import HTTPException, Request, status
from fastapi.responses import RedirectResponse
from hedron import Hedron
from starlette.responses import Response

from app.application.pipelines import (
    PipelineCommands,
    PipelineDependencies,
    SavePipelineCommand,
)
from app.config import Settings
from app.connectors.errors import ConnectorError
from app.connectors.locators import postgres_table
from app.connectors.registry import catalog_browser_for, writer_enabled
from app.dependencies import Auth, DbSession, RequireCsrf, SettingsDep
from app.services.catalogs import CREATE_TABLE_VALUE, UserCatalog
from app.services.secrets import list_user_secrets
from app.ui.params import (
    PipelineConflictColumnsForm,
    PipelineIdForm,
    PipelineNameForm,
    PipelineOptionalTableForm,
    PipelineProviderForm,
    PipelineSchemaForm,
    PipelineSourceProviderForm,
    PipelineTableForm,
    PipelineWriteModeForm,
)
from app.ui.routes.pipeline_context import WithUserSession
from app.ui.urls import redirect_path


def register_pipeline_save_routes(
    app: Hedron,
    *,
    with_user_session: WithUserSession,
) -> None:
    """Register pipeline definition save behavior behind a command callback."""

    @app.action("/pipeline/save", include_in_schema=False)
    async def pipeline_save(
        request: Request,
        auth: Auth,
        db: DbSession,
        settings: SettingsDep,
        _csrf: RequireCsrf,
        pipeline_name: PipelineNameForm,
        source_provider: PipelineSourceProviderForm,
        destination_provider: PipelineProviderForm,
        destination_schema: PipelineSchemaForm,
        destination_table: PipelineTableForm,
        write_mode: PipelineWriteModeForm,
        source_schema: PipelineOptionalTableForm = "",
        source_table: PipelineOptionalTableForm = "",
        destination_table_new: PipelineOptionalTableForm = "",
        conflict_columns: PipelineConflictColumnsForm = "",
        source_upload_id: PipelineIdForm = "",
        pipeline_id: PipelineIdForm = "",
    ) -> Response:
        if source_provider != "csv" and (not source_schema or not source_table):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="Select a source schema and object before saving.",
            )
        try:
            saved_pipeline_id = await asyncio.to_thread(
                save_pipeline_in_thread,
                with_user_session,
                settings,
                auth.user.id,
                request,
                pipeline_name,
                source_provider,
                source_schema,
                source_table,
                destination_provider,
                destination_schema,
                destination_table,
                write_mode,
                destination_table_new,
                conflict_columns,
                source_upload_id,
                pipeline_id,
            )
        except (ConnectorError, ValueError) as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
            ) from exc
        return RedirectResponse(
            redirect_path(
                request,
                f"/pipeline?notice=saved&pipeline_id={saved_pipeline_id}",
            ),
            status_code=status.HTTP_303_SEE_OTHER,
        )


def save_pipeline_in_thread(
    with_user_session: WithUserSession,
    settings: Settings,
    user_id: str,
    request: Request,
    pipeline_name: str,
    source_provider: str,
    source_schema: str,
    source_table: str,
    destination_provider: str,
    destination_schema: str,
    destination_table: str,
    write_mode: str,
    destination_table_new: str,
    conflict_columns: str,
    source_upload_id: str,
    pipeline_id: str,
) -> str:
    """Persist a pipeline using a session owned by the worker thread."""

    def save(thread_db, user):
        available_providers = {
            provider.name
            for provider, secret in list_user_secrets(thread_db, user)
            if secret is not None and secret.validation_status == "connected"
        }
        catalog_access = UserCatalog(
            thread_db,
            settings,
            user,
            request=request,
            browser_resolver=catalog_browser_for,
        )
        source_branch = (
            catalog_access.default_branch(source_provider)
            if source_provider != "csv" and source_provider in available_providers
            else ""
        )
        destination_branch = (
            catalog_access.branch_for_namespace(destination_provider, destination_schema)
            if destination_provider in available_providers
            else ""
        )
        selected_conflict_columns = conflict_columns
        if (
            destination_provider == "postgres"
            and destination_provider in available_providers
            and write_mode == "upsert"
        ):
            if destination_table == CREATE_TABLE_VALUE:
                raise ValueError("Create the PostgreSQL table before configuring an upsert.")
            destination_schema_details = catalog_access.inspect_object(
                "postgres", postgres_table(destination_schema, destination_table)
            )
            eligible_keys = {
                tuple(destination_schema_details.primary_key),
                *map(tuple, destination_schema_details.unique_constraints),
            }
            eligible_keys.discard(())
            selected_key = tuple(
                item.strip() for item in conflict_columns.split(",") if item.strip()
            )
            if not selected_key and destination_schema_details.primary_key:
                selected_key = tuple(destination_schema_details.primary_key)
            if not selected_key or selected_key not in eligible_keys:
                raise ValueError(
                    "Select a current primary or unique key for the PostgreSQL upsert."
                )
            selected_conflict_columns = ",".join(selected_key)
        commands = PipelineCommands(
            PipelineDependencies(
                writer_policy=lambda provider: writer_enabled(provider, settings=settings)
            )
        )
        saved_pipeline = commands.save(
            thread_db,
            SavePipelineCommand(
                user=user,
                name=pipeline_name,
                source_provider=source_provider,
                source_schema=source_schema,
                source_table=source_table,
                source_branch=source_branch,
                destination_provider=destination_provider,
                destination_schema=destination_schema,
                destination_table=destination_table,
                destination_branch=destination_branch,
                destination_table_new=destination_table_new,
                source_upload_id=source_upload_id,
                write_mode=write_mode,
                conflict_columns=selected_conflict_columns,
                available_providers=available_providers,
                pipeline_id=pipeline_id,
                request=request,
            ),
        )
        return saved_pipeline.id

    return with_user_session(settings, user_id, save)
