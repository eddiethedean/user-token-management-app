"""HTTP registration for saved pipeline-definition mutations."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from fastapi import BackgroundTasks, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from hedron import Hedron
from starlette.responses import Response

from app.application.dto import ActorContext
from app.application.feedback import preflight_failure
from app.application.pipelines import PipelineAuthoringOperation, SavePipelineAuthoringCommand
from app.connectors.errors import ConnectorError
from app.dependencies import Auth, DbSession, RequireCsrf, SettingsDep
from app.domain.column_types import parse_column_type_override_values
from app.ui.params import (
    PipelineAutoIncrementPrimaryKeyForm,
    PipelineColumnTypeOverridesForm,
    PipelineConflictColumnsForm,
    PipelineIdForm,
    PipelineNameForm,
    PipelineOptionalTableForm,
    PipelinePrimaryKeyColumnsForm,
    PipelineProviderForm,
    PipelineSaveAndRunForm,
    PipelineSchemaForm,
    PipelineSourceProviderForm,
    PipelineTableForm,
    PipelineWriteModeForm,
)
from app.ui.routes.pipeline_context import request_metadata, run_owned_sync
from app.ui.urls import redirect_path

PipelineRunStarter = Callable[..., Awaitable[Response]]


def register_pipeline_save_routes(
    app: Hedron,
    *,
    authoring_operation_factory: Callable[[Request], PipelineAuthoringOperation],
    start_pipeline_run: PipelineRunStarter,
) -> None:
    """Translate the save form into a value-only authoring operation."""

    @app.action("/pipeline/save", include_in_schema=False)
    async def pipeline_save(
        request: Request,
        auth: Auth,
        db: DbSession,
        settings: SettingsDep,
        background_tasks: BackgroundTasks,
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
        column_type_overrides: PipelineColumnTypeOverridesForm = None,
        primary_key_columns: PipelinePrimaryKeyColumnsForm = "",
        auto_increment_primary_key: PipelineAutoIncrementPrimaryKeyForm = "",
        source_upload_id: PipelineIdForm = "",
        pipeline_id: PipelineIdForm = "",
        save_and_run: PipelineSaveAndRunForm = False,
    ) -> Response:
        if source_provider != "csv" and (not source_schema or not source_table):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="Select a source schema and object before saving.",
            )
        try:
            metadata = request_metadata(settings, request)
            summary = await run_owned_sync(
                request,
                authoring_operation_factory(request),
                ActorContext(
                    user_id=auth.user.id,
                    request_id=metadata.request_id,
                    source_ip=metadata.source_ip,
                ),
                SavePipelineAuthoringCommand(
                    name=pipeline_name,
                    source_provider=source_provider,
                    source_schema=source_schema,
                    source_table=source_table,
                    destination_provider=destination_provider,
                    destination_schema=destination_schema,
                    destination_table=destination_table,
                    write_mode=write_mode,
                    destination_table_new=destination_table_new,
                    conflict_columns=conflict_columns,
                    column_type_overrides=parse_column_type_override_values(
                        column_type_overrides or ()
                    ),
                    primary_key_columns=primary_key_columns,
                    auto_increment_primary_key=auto_increment_primary_key,
                    source_upload_id=source_upload_id,
                    pipeline_id=pipeline_id,
                ),
            )
            saved_pipeline_id = summary.id
        except (ConnectorError, PermissionError, ValueError) as exc:
            outcome = preflight_failure(
                reason=str(exc),
                field_errors=getattr(exc, "field_errors", {}),
                reference_id=getattr(request.state, "support_reference", ""),
                operation="pipeline_save",
            )
            detail = outcome.message
            if outcome.field_errors:
                detail += " " + " ".join(
                    f"{name}: {message}" for name, message in outcome.field_errors.items()
                )
            if outcome.reference_id:
                detail += f" Reference: {outcome.reference_id}."
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=detail
            ) from exc
        if save_and_run:
            try:
                return await start_pipeline_run(
                    request=request,
                    auth=auth,
                    db=db,
                    settings=settings,
                    background_tasks=background_tasks,
                    _csrf=_csrf,
                    pipeline_id=saved_pipeline_id,
                )
            except HTTPException as exc:
                raise HTTPException(
                    status_code=exc.status_code,
                    detail=f"Pipeline was saved, but the run could not start: {exc.detail}",
                    headers=exc.headers,
                ) from exc
        return RedirectResponse(
            redirect_path(
                request,
                f"/pipeline?notice=saved&pipeline_id={saved_pipeline_id}",
            ),
            status_code=status.HTTP_303_SEE_OTHER,
        )
