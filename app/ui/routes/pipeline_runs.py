"""HTTP registration for pipeline run lifecycle interactions."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Protocol

from fastapi import BackgroundTasks, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from hedron import Hedron, HedronRouter, InteractionResult
from hedron.htmx import is_htmx_request
from hedron_core import NodeLike
from sqlalchemy.orm import Session
from starlette.responses import Response

import app.services.pipeline_runs as pipeline_run_service
from app.application.feedback import preflight_failure
from app.application.pipelines import (
    EnqueuePipelineCommand,
    PipelineCommands,
    PipelineDependencies,
)
from app.connectors.base import ColumnSchema
from app.connectors.decimal_validation import validate_decimal_destination_schema
from app.connectors.errors import ConnectorError, TransferErrorCode
from app.connectors.locators import CsvUploadLocator, PostgresReplacePolicy
from app.connectors.registry import writer_enabled
from app.dependencies import Auth, DbSession, RequireCsrf, SettingsDep
from app.models import PipelineDefinition, PipelineRun, PipelineRunEvent, PipelineUpload
from app.services.pipeline_runs import (
    owned_run,
    record_reconciliation_review,
    request_cancel,
    snapshot_from_definition,
)
from app.services.pipeline_tasks import schedule_pipeline_run
from app.ui.interactions import (
    interaction_response,
    ok_fragment,
    pipeline_run_feedback_clear_oob,
    pipeline_save_notice_clear_oob,
)
from app.ui.params import PipelineIdForm
from app.ui.partials.feedback import feedback_panel
from app.ui.presenters.run_status import run_action_metadata, run_status_toasts
from app.ui.regions import (
    PIPELINE_RUN_FEEDBACK,
    PIPELINE_RUN_MONITOR,
    PIPELINE_SAVE_NOTICE,
    TOAST_HOST,
)
from app.ui.routes.pipeline_context import WithUserCatalog, run_owned_sync
from app.ui.urls import mounted_path, redirect_path


class StatusFragment(Protocol):
    def __call__(
        self,
        request: Request,
        db: Session,
        run: PipelineRun,
        *,
        csrf_token: str = "",
        events: list[PipelineRunEvent] | None = None,
    ) -> NodeLike: ...


class EventsLoader(Protocol):
    def __call__(
        self, db: Session, *, run: PipelineRun, after_sequence: int = 0
    ) -> list[PipelineRunEvent]: ...


def register_pipeline_run_routes(
    app: Hedron,
    fragment_router: HedronRouter,
    *,
    status_fragment: StatusFragment,
    events_loader: EventsLoader | None = None,
    with_user_catalog: WithUserCatalog,
) -> Callable[..., Awaitable[Response]]:
    """Register start, status, cancel, and reconciliation endpoints."""

    def _events_after(db, *, run, after_sequence=0):
        loader = events_loader or pipeline_run_service.events_after
        return loader(db, run=run, after_sequence=after_sequence)

    async def _start_pipeline_run(
        request: Request,
        auth: Auth,
        db: DbSession,
        settings: SettingsDep,
        _csrf: RequireCsrf,
        background_tasks: BackgroundTasks,
        pipeline_id: str,
        idempotency_token: PipelineIdForm = "",
    ) -> Response:
        pipeline = db.get(PipelineDefinition, pipeline_id)
        if pipeline is None or pipeline.user_id != auth.user.id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Pipeline not found")
        try:
            snapshot = snapshot_from_definition(pipeline)
            source_decimal_columns = (
                _csv_decimal_columns_before_run(
                    db,
                    upload_id=snapshot.source_upload_id,
                    source=snapshot.source,
                    user_id=auth.user.id,
                )
                if snapshot.source_provider.casefold() == "csv"
                and snapshot.destination_provider.casefold() == "postgres"
                else ()
            )
            if source_decimal_columns and not (
                isinstance(snapshot.write_policy, PostgresReplacePolicy)
                and snapshot.write_policy.schema_policy == "recreate"
            ):
                await run_owned_sync(
                    request,
                    with_user_catalog,
                    settings,
                    auth.user.id,
                    request,
                    lambda catalog: _validate_csv_decimal_destination(
                        catalog, snapshot.destination, source_decimal_columns
                    ),
                )
            commands = PipelineCommands(
                PipelineDependencies(
                    writer_policy=lambda provider: writer_enabled(provider, settings=settings)
                )
            )
            run = commands.enqueue(
                db,
                EnqueuePipelineCommand(
                    user=auth.user,
                    pipeline=pipeline,
                    snapshot=snapshot,
                    idempotency_token=idempotency_token or None,
                    request=request,
                ),
            )
        except (ConnectorError, ValueError, LookupError) as exc:
            outcome = preflight_failure(
                reason=str(exc), reference_id=getattr(request.state, "support_reference", "")
            )
            if is_htmx_request(request):
                return await interaction_response(
                    request,
                    ok_fragment(
                        feedback_panel(outcome, label="Pipeline preflight feedback"),
                        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                        retarget="#pipeline-run-feedback",
                        reswap="innerHTML",
                    ),
                )
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=outcome.message,
            ) from exc
        if settings.is_demo_mode and settings.app_env == "test":
            from app.worker import process_one

            process_one(db, settings, run_id=run.id)
            db.refresh(run)
        else:
            execution = getattr(request.state, "execution", None)
            schedule_pipeline_run(
                background_tasks,
                execution.execution_settings if execution is not None else settings,
                run.id,
                (
                    None
                    if execution is not None
                    else getattr(request.app.state, "pipeline_stop_event", None)
                ),
                execution,
            )
        if is_htmx_request(request):
            run_events = _events_after(db, run=run, after_sequence=0)
            action_state, action_trace = run_action_metadata(run, run_events)
            response = await interaction_response(
                request,
                ok_fragment(
                    status_fragment(
                        request,
                        db,
                        run,
                        csrf_token=auth.session.csrf_token,
                        events=run_events,
                    ),
                    status_code=status.HTTP_202_ACCEPTED,
                    action_state=action_state,
                    action_trace=action_trace,
                    oob=(pipeline_run_feedback_clear_oob(), pipeline_save_notice_clear_oob()),
                    push_url=mounted_path(request, f"/pipeline?pipeline_id={pipeline.id}"),
                ),
            )
            return response
        return RedirectResponse(
            redirect_path(
                request,
                f"/pipeline?notice=queued&pipeline_id={pipeline.id}&run_id={run.id}",
            ),
            status_code=status.HTTP_303_SEE_OTHER,
        )

    @app.action(
        "/pipeline/runs",
        fragment_regions=(
            PIPELINE_RUN_FEEDBACK,
            PIPELINE_RUN_MONITOR,
            PIPELINE_SAVE_NOTICE,
            TOAST_HOST,
        ),
        include_in_schema=False,
    )
    async def pipeline_run_start(
        request: Request,
        auth: Auth,
        db: DbSession,
        settings: SettingsDep,
        background_tasks: BackgroundTasks,
        _csrf: RequireCsrf,
        pipeline_id: PipelineIdForm,
        idempotency_token: PipelineIdForm = "",
    ) -> Response:
        if not pipeline_id:
            outcome = preflight_failure(
                reason="pipeline_id is required to start a pipeline run",
                reference_id=getattr(request.state, "support_reference", ""),
            )
            if is_htmx_request(request):
                return await interaction_response(
                    request,
                    ok_fragment(
                        feedback_panel(outcome, label="Pipeline preflight feedback"),
                        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                        retarget="#pipeline-run-feedback",
                        reswap="innerHTML",
                    ),
                )
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=outcome.message,
            )
        return await _start_pipeline_run(
            request=request,
            auth=auth,
            db=db,
            settings=settings,
            background_tasks=background_tasks,
            _csrf=_csrf,
            pipeline_id=pipeline_id,
            idempotency_token=idempotency_token,
        )

    @fragment_router.view(
        "/pipeline/runs/{run_id}/status",
        fragment_regions=(PIPELINE_RUN_MONITOR,),
        include_in_schema=False,
    )
    async def pipeline_run_status(
        request: Request,
        auth: Auth,
        db: DbSession,
        run_id: str,
    ) -> Response | InteractionResult:
        request.state.hedron_authenticated = True
        try:
            run = owned_run(db, user=auth.user, run_id=run_id)
        except LookupError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
        if not is_htmx_request(request):
            return RedirectResponse(
                redirect_path(request, f"/pipeline?run_id={run.id}"),
                status_code=status.HTTP_303_SEE_OTHER,
            )
        run_events = _events_after(db, run=run, after_sequence=0)
        action_state, action_trace = run_action_metadata(run, run_events)
        toast, toast_tone = run_status_toasts(run)
        return ok_fragment(
            status_fragment(
                request,
                db,
                run,
                csrf_token=auth.session.csrf_token,
                events=run_events,
            ),
            toast=toast,
            toast_tone=toast_tone,
            action_state=action_state,
            action_trace=action_trace,
        )

    @app.action(
        "/pipeline/runs/{run_id}/cancel",
        fragment_regions=(PIPELINE_RUN_MONITOR, TOAST_HOST),
        include_in_schema=False,
    )
    async def pipeline_run_cancel(
        request: Request,
        auth: Auth,
        db: DbSession,
        _csrf: RequireCsrf,
        run_id: str,
    ) -> Response:
        try:
            run = request_cancel(db, user=auth.user, run_id=run_id)
        except LookupError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
        run_events = _events_after(db, run=run, after_sequence=0)
        action_state, action_trace = run_action_metadata(run, run_events)
        toast, toast_tone = run_status_toasts(run)
        return await interaction_response(
            request,
            ok_fragment(
                status_fragment(
                    request,
                    db,
                    run,
                    csrf_token=auth.session.csrf_token,
                    events=run_events,
                ),
                toast=toast,
                toast_tone=toast_tone,
                action_state=action_state,
                action_trace=action_trace,
            ),
        )

    @app.action(
        "/pipeline/runs/{run_id}/reconcile",
        fragment_regions=(PIPELINE_RUN_MONITOR, TOAST_HOST),
        include_in_schema=False,
    )
    async def pipeline_run_reconcile(
        request: Request,
        auth: Auth,
        db: DbSession,
        _csrf: RequireCsrf,
        run_id: str,
    ) -> Response:
        try:
            run = record_reconciliation_review(db, user=auth.user, run_id=run_id)
        except LookupError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
        run_events = _events_after(db, run=run, after_sequence=0)
        action_state, action_trace = run_action_metadata(run, run_events)
        return await interaction_response(
            request,
            ok_fragment(
                status_fragment(
                    request,
                    db,
                    run,
                    csrf_token=auth.session.csrf_token,
                    events=run_events,
                ),
                toast="Reconciliation review recorded.",
                toast_tone="info",
                action_state=action_state,
                action_trace=action_trace,
            ),
        )

    return _start_pipeline_run


def _csv_decimal_columns_before_run(
    db, *, upload_id, source, user_id: str
) -> tuple[ColumnSchema, ...]:
    """Load the trusted decimal profile needed for the pre-enqueue destination check."""

    if not isinstance(source, CsvUploadLocator):
        return ()
    from app.services.csv_uploads import inspection_from_upload

    upload = db.get(PipelineUpload, upload_id or source.upload_id)
    if upload is None or upload.user_id != user_id:
        raise ValueError("The CSV upload is no longer available.")
    if upload.checksum_sha256 != source.checksum_sha256:
        raise ValueError("The CSV upload no longer matches the saved pipeline.")
    inspection = inspection_from_upload(upload)
    return tuple(
        ColumnSchema(
            name=column.name,
            data_type=f"Decimal(precision={column.decimal_precision}, scale={column.decimal_scale})",
        )
        for column in inspection.columns
        if column.inferred_type == "decimal"
    )


def _validate_csv_decimal_destination(catalog, destination, source_columns) -> None:
    """Fail before queuing when the saved PostgreSQL table would round decimals."""

    try:
        destination_schema = catalog.inspect_object("postgres", destination)
    except ConnectorError as exc:
        if exc.code == TransferErrorCode.SOURCE_NOT_FOUND:
            return
        raise
    validate_decimal_destination_schema(source_columns, destination_schema.columns)
