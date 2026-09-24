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
from app.connectors.base import ColumnSchema, ObjectSchema
from app.connectors.csv_source import profiled_polars_type
from app.connectors.errors import ConnectorError, TransferErrorCode
from app.connectors.locators import (
    CsvUploadLocator,
    FoundryDatasetFilesLocator,
    FoundryUploadLocator,
    PostgresTableLocator,
)
from app.connectors.registry import writer_enabled
from app.dependencies import Auth, DbSession, RequireCsrf, SettingsDep
from app.models import PipelineDefinition, PipelineRun, PipelineRunEvent, PipelineUpload
from app.services.column_casting import apply_column_type_overrides_to_schema
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
    REQUEST_FEEDBACK,
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


class PipelineReadinessError(Exception):
    """Safe, field-targeted failure from the blocking read-only preflight."""

    def __init__(self, reason_code: str, field_errors: dict[str, str]) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code
        self.field_errors = field_errors


def _readiness_target_label(provider: str, role: str, locator: object) -> str:
    provider_label = {
        "postgres": "PostgreSQL",
        "mss": "Foundry MSS",
        "mcscop": "Foundry MCS-COP",
        "csv": "CSV",
    }.get(provider.casefold(), provider)
    if isinstance(locator, PostgresTableLocator):
        return f"{provider_label} {role} {locator.schema_name}.{locator.table}"
    if isinstance(locator, FoundryUploadLocator):
        return f"{provider_label} {role} {locator.dataset_rid} · {locator.file_name}"
    if isinstance(locator, FoundryDatasetFilesLocator):
        selected_file = (
            locator.file_paths[0]
            if isinstance(locator.file_paths, list) and len(locator.file_paths) == 1
            else "selected files"
        )
        return f"{provider_label} {role} {locator.dataset_rid} · {selected_file}"
    if isinstance(locator, CsvUploadLocator):
        return "CSV source upload"
    return f"{provider_label} {role}"


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
            csv_source_schema = (
                _csv_source_schema_before_run(
                    db,
                    upload_id=snapshot.source_upload_id,
                    source=snapshot.source,
                    user_id=auth.user.id,
                )
                if snapshot.source_provider.casefold() == "csv"
                else None
            )
            await run_owned_sync(
                request,
                with_user_catalog,
                settings,
                auth.user.id,
                request,
                lambda catalog: _read_only_pipeline_preflight(catalog, snapshot, csv_source_schema),
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
        except (PipelineReadinessError, ConnectorError, ValueError, LookupError) as exc:
            field_errors = exc.field_errors if isinstance(exc, PipelineReadinessError) else {}
            reason_code = exc.reason_code if isinstance(exc, PipelineReadinessError) else ""
            outcome = preflight_failure(
                reason=reason_code or str(exc),
                reason_code=reason_code,
                field_errors=field_errors,
                reference_id=getattr(request.state, "support_reference", ""),
                operation="pipeline_readiness" if reason_code else "enqueue",
            )
            action_href = None
            if outcome.action_label == "Review connection":
                action_href = mounted_path(request, "/security#connection-status-list")
            elif outcome.action_label in {"Review route", "Review source", "Review destination"}:
                action_href = mounted_path(request, f"/pipeline?pipeline_id={pipeline.id}")
            if is_htmx_request(request):
                return await interaction_response(
                    request,
                    ok_fragment(
                        feedback_panel(
                            outcome,
                            label="Pipeline preflight feedback",
                            action_href=action_href,
                        ),
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
            REQUEST_FEEDBACK,
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
                        feedback_panel(
                            outcome,
                            label="Pipeline preflight feedback",
                            action_href=(
                                mounted_path(request, "/pipeline")
                                if outcome.action_label == "Review route"
                                else None
                            ),
                        ),
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
        fragment_regions=(PIPELINE_RUN_MONITOR, REQUEST_FEEDBACK),
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
        fragment_regions=(PIPELINE_RUN_MONITOR, TOAST_HOST, REQUEST_FEEDBACK),
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
        fragment_regions=(PIPELINE_RUN_MONITOR, TOAST_HOST, REQUEST_FEEDBACK),
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


def _csv_source_schema_before_run(db, *, upload_id, source, user_id: str) -> ObjectSchema:
    """Build the current, trusted CSV schema for the blocking pre-enqueue check."""

    if not isinstance(source, CsvUploadLocator):
        raise PipelineReadinessError(
            "source_unavailable", {"Source": "Review the saved CSV source selection."}
        )
    from app.services.csv_uploads import inspection_from_upload

    upload = db.get(PipelineUpload, upload_id or source.upload_id)
    if upload is None or upload.user_id != user_id:
        raise PipelineReadinessError(
            "source_unavailable", {"Source": "Upload and inspect the CSV file again."}
        )
    if upload.checksum_sha256 != source.checksum_sha256:
        raise PipelineReadinessError(
            "source_unavailable", {"Source": "Upload and inspect the CSV file again."}
        )
    try:
        inspection = inspection_from_upload(upload)
    except ValueError as exc:
        raise PipelineReadinessError(
            "source_unavailable", {"Source": "Upload and inspect the CSV file again."}
        ) from exc
    if not inspection.columns:
        raise PipelineReadinessError(
            "source_unavailable", {"Source": "Choose a CSV with a valid inspected header."}
        )
    try:
        columns = tuple(
            ColumnSchema(
                name=column.name,
                data_type=str(profiled_polars_type(column)),
                nullable=True,
            )
            for column in inspection.columns
        )
    except ConnectorError as exc:
        raise PipelineReadinessError(
            "unsupported_conversion",
            {"CSV columns": "Choose source types within the supported precision and type limits."},
        ) from exc
    return ObjectSchema(
        locator=source,
        columns=columns,
    )


def _read_only_pipeline_preflight(catalog, snapshot, csv_source_schema) -> None:
    """Check current source and PostgreSQL destination readiness without writes."""

    try:
        source_preflight = getattr(catalog, "preflight_source", None)
        if csv_source_schema is not None:
            source_schema = csv_source_schema
        elif callable(source_preflight):
            raw_source_schema: object = source_preflight(snapshot.source_provider, snapshot.source)
            if not isinstance(raw_source_schema, ObjectSchema):
                raise ConnectorError(
                    TransferErrorCode.SCHEMA_DRIFT,
                    "The source connector returned invalid schema metadata.",
                    retryable=False,
                )
            source_schema = raw_source_schema
        else:
            source_schema = catalog.inspect_object(snapshot.source_provider, snapshot.source)
        if not source_schema.columns and snapshot.source_provider.casefold() not in {
            "mss",
            "mcscop",
        }:
            raise ConnectorError(
                TransferErrorCode.SOURCE_NOT_FOUND,
                "The selected source has no current schema.",
                retryable=False,
            )
        source_schema = apply_column_type_overrides_to_schema(
            source_schema, snapshot.write_policy.column_type_overrides
        )
    except Exception as exc:
        if isinstance(exc, ConnectorError) and exc.code == TransferErrorCode.PERMISSION_DENIED:
            reason_code = "source_permission_denied"
            source_message = "Check read access to the selected source object."
        else:
            reason_code = "source_unavailable"
            source_message = "Review the selected source and confirm the connection can read it."
        source_label = _readiness_target_label(snapshot.source_provider, "source", snapshot.source)
        source_fields = {source_label: source_message}
        source_fields.update(getattr(exc, "field_errors", {}))
        raise PipelineReadinessError(
            reason_code,
            source_fields,
        ) from exc

    try:
        destination_preflight = getattr(catalog, "preflight_destination", None)
        if callable(destination_preflight):
            destination_preflight(
                snapshot.destination_provider,
                snapshot.destination,
                source_schema,
                snapshot.write_policy,
            )
        elif snapshot.destination_provider.casefold() == "postgres" and isinstance(
            snapshot.destination, PostgresTableLocator
        ):
            catalog.inspect_object(snapshot.destination_provider, snapshot.destination)
        else:
            return
    except ConnectorError as exc:
        if exc.code == TransferErrorCode.PERMISSION_DENIED:
            reason_code = "destination_permission_denied"
            field_message = "Check write access to the selected destination schema and table."
        elif exc.code == TransferErrorCode.DESTINATION_CONFLICT:
            reason_code = "invalid_upsert_key"
            field_message = "Review the destination upsert key and current unique constraints."
        elif exc.code == TransferErrorCode.UNSUPPORTED_TYPE:
            reason_code = "unsupported_conversion"
            field_message = "Review source and destination column types for a safe conversion."
        elif exc.code == TransferErrorCode.SCHEMA_DRIFT:
            if "decimal" in str(exc).casefold():
                reason_code = "destination_precision"
                field_message = "The destination column cannot hold all source decimal places."
            else:
                reason_code = "destination_schema_incompatible"
                field_message = "Review the source and destination columns used by this route."
        else:
            reason_code = "destination_unavailable"
            field_message = "Review the destination connection, schema, and selected table."
        destination_label = _readiness_target_label(
            snapshot.destination_provider, "destination", snapshot.destination
        )
        destination_fields = {destination_label: field_message}
        for field_name, message in exc.field_errors.items():
            if field_name.startswith("Destination column "):
                column_name = field_name.removeprefix("Destination column ")
                field_name = f"{destination_label} · column {column_name}"
            destination_fields[field_name] = message
        raise PipelineReadinessError(reason_code, destination_fields) from exc
    except Exception as exc:
        destination_label = _readiness_target_label(
            snapshot.destination_provider, "destination", snapshot.destination
        )
        raise PipelineReadinessError(
            "destination_unavailable",
            {destination_label: "Review the destination connection, schema, and selected table."},
        ) from exc
