"""HTTP registration for saved pipeline-definition mutations."""

from __future__ import annotations

from collections.abc import Callable

from fastapi import HTTPException, Request, status
from fastapi.responses import RedirectResponse
from hedron import Hedron
from starlette.responses import Response

from app.application.dto import ActorContext
from app.application.pipelines import PipelineAuthoringOperation, SavePipelineAuthoringCommand
from app.connectors.errors import ConnectorError
from app.dependencies import Auth, RequireCsrf, SettingsDep
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
from app.ui.routes.pipeline_context import request_metadata, run_owned_sync
from app.ui.urls import redirect_path


def register_pipeline_save_routes(
    app: Hedron,
    *,
    authoring_operation_factory: Callable[[Request], PipelineAuthoringOperation],
) -> None:
    """Translate the save form into a value-only authoring operation."""

    @app.action("/pipeline/save", include_in_schema=False)
    async def pipeline_save(
        request: Request,
        auth: Auth,
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
                    source_upload_id=source_upload_id,
                    pipeline_id=pipeline_id,
                ),
            )
            saved_pipeline_id = summary.id
        except (ConnectorError, PermissionError, ValueError) as exc:
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
