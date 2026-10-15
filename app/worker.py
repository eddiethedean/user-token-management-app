"""Durable pipeline execution used by the Hedron app runtime."""

from __future__ import annotations

import json
import logging
import threading
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from typing import Any, cast

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.connectors.errors import ConnectorError, TransferErrorCode
from app.connectors.locators import DatabricksTableLocator, parse_snapshot
from app.connectors.registry import (
    connection_tester_for,
    destination_writer_for,
    source_reader_for,
)
from app.logging_config import (
    bind_reference,
    clear_request_id,
    log_event,
    safe_exception_traceback,
)
from app.models import PipelineRun, User, UserSecret
from app.services import pipeline_runs
from app.services.pipeline_state import RunConflictError
from app.services.secrets import decrypt_user_credentials_for_run
from app.services.transfer_engine import execute_transfer

log = logging.getLogger(__name__)


@dataclass
class LeaseKeeper:
    """Renew one claimed run from an independent database session."""

    settings: Settings
    run_id: str
    lease_token: str
    session_factory: Callable[[], AbstractContextManager[Session]] | None = None
    stopped: threading.Event = field(default_factory=threading.Event)
    lost: threading.Event = field(default_factory=threading.Event)
    _thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(
            target=self._run,
            name=f"pipeline-lease-{self.run_id[:8]}",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self.stopped.set()
        if self._thread is not None:
            self._thread.join()

    def _run(self) -> None:
        bind_reference(reference_id=self.run_id, run_id=self.run_id)
        if self.session_factory is None:
            from app.database import SessionLocal

            session_factory = SessionLocal
        else:
            session_factory = self.session_factory

        interval = max(1.0, self.settings.pipeline_lease_seconds / 3)
        while not self.stopped.wait(interval):
            try:
                with session_factory() as heartbeat_db:
                    renewed = pipeline_runs.renew_lease(
                        heartbeat_db,
                        run_id=self.run_id,
                        lease_token=self.lease_token,
                        lease_seconds=self.settings.pipeline_lease_seconds,
                    )
            except Exception as exc:
                log_event(
                    log,
                    "pipeline.lease.heartbeat_failed",
                    outcome="failed",
                    error_code=str(TransferErrorCode.WORKER_LOST),
                    reference_id=self.run_id,
                    run_id=self.run_id,
                    operation="lease_heartbeat",
                    exception_type=type(exc).__name__,
                    traceback=safe_exception_traceback((type(exc), exc, exc.__traceback__)),
                )
                self.lost.set()
                clear_request_id()
                return
            if not renewed:
                self.lost.set()
                clear_request_id()
                return


def worker_id(settings: Settings) -> str:
    """Return the lease owner identifier for this Hedron app process."""
    import os
    import socket

    return settings.pipeline_worker_id or f"{socket.gethostname()}-{os.getpid()}"


def process_one(
    db: Session,
    settings: Settings,
    *,
    run_id: str | None = None,
    stop_event: threading.Event | None = None,
    session_factory: Callable[[], AbstractContextManager[Session]] | None = None,
    credential_resolver: Callable[..., dict[str, str]] | None = None,
) -> bool:
    if stop_event is not None and stop_event.is_set():
        return False
    claimed = pipeline_runs.claim_run(
        db,
        worker_id=worker_id(settings),
        lease_seconds=settings.pipeline_lease_seconds,
        run_id=run_id,
    )
    if claimed is None:
        return False
    run, lease_token = claimed
    run_id = run.id
    user = db.get(User, run.user_id)
    if user is None:
        pipeline_runs.fail_run(
            db,
            run,
            code=TransferErrorCode.INTERNAL_ERROR,
            summary="The run owner no longer exists.",
            lease_token=lease_token,
        )
        return True
    if not user.is_active:
        pipeline_runs.cancel_claimed_run(db, run, lease_token=lease_token)
        return True
    bind_reference(reference_id=run_id, run_id=run_id)
    keeper = LeaseKeeper(
        settings=settings,
        run_id=run_id,
        lease_token=lease_token,
        session_factory=session_factory,
    )
    keeper.start()
    try:
        snapshot = parse_snapshot(run.definition_snapshot_json)
        resolve_credentials = credential_resolver or _credentials_for
        source_credentials = resolve_credentials(
            db, settings, user=user, provider=snapshot.source_provider, snapshot=snapshot
        )
        destination_credentials = resolve_credentials(
            db,
            settings,
            user=user,
            provider=snapshot.destination_provider,
            snapshot=snapshot,
        )
        if (
            snapshot.source_provider == "databricks"
            or snapshot.destination_provider == "databricks"
        ):

            def require_current_databricks_generation(expected_generation: str) -> None:
                if session_factory is None:
                    from app.database import SessionLocal

                    revision_factory = SessionLocal
                else:
                    revision_factory = session_factory
                with revision_factory() as revision_db:
                    revision = revision_db.scalar(
                        select(UserSecret.updated_at).where(
                            UserSecret.user_id == user.id,
                            UserSecret.provider == "databricks",
                        )
                    )
                active_generation = (
                    revision.isoformat(timespec="microseconds") if revision is not None else ""
                )
                if not active_generation or active_generation != expected_generation:
                    raise ConnectorError(
                        TransferErrorCode.CREDENTIALS_STALE,
                        "The Databricks connection changed during this transfer. Reconnect and review the route before retrying.",
                        retryable=False,
                    )

            expected_generations = []
            if snapshot.source_provider == "databricks":
                expected_generations.append(
                    (source_credentials, getattr(snapshot.source, "connection_generation", ""))
                )
            if snapshot.destination_provider == "databricks":
                expected_generations.append(
                    (
                        destination_credentials,
                        getattr(snapshot.destination, "connection_generation", ""),
                    )
                )
            for credentials, expected_generation in expected_generations:
                credentials["_authorization_check"] = lambda generation=expected_generation: (
                    require_current_databricks_generation(generation)
                )
            for provider, credentials in (
                (snapshot.source_provider, source_credentials),
                (snapshot.destination_provider, destination_credentials),
            ):
                if provider == "databricks" and credentials.get("startup_policy") == "allow_start":

                    def request_databricks_start(
                        observed_state: str, *, bundle=credentials
                    ) -> dict[str, str]:
                        from app.services.databricks_compute import request_compute_start

                        return request_compute_start(
                            db,
                            settings=settings,
                            user=user,
                            credentials=bundle,
                            connector_resolver=connection_tester_for,
                            observed_state=observed_state,
                            pipeline_run=run,
                            lease_token=lease_token,
                            require_opt_in=True,
                        )

                    cast(dict[str, Any], credentials)["_startup_request"] = request_databricks_start
            if snapshot.destination_provider == "databricks":
                from app.services.databricks_publication import record_publication_event

                target_locator = snapshot.destination
                if not isinstance(target_locator, DatabricksTableLocator):
                    raise ConnectorError(
                        TransferErrorCode.SCHEMA_DRIFT,
                        "The Databricks publication target is invalid.",
                        retryable=False,
                    )
                target_generation = str(getattr(target_locator, "connection_generation", ""))

                def persist_databricks_publication(event: str, **facts: object) -> None:
                    if (
                        str(facts.get("run_id", "")) != run.id
                        or facts.get("target") != target_locator
                    ):
                        raise ConnectorError(
                            TransferErrorCode.SCHEMA_DRIFT,
                            "The Databricks publication target changed during this transfer.",
                            retryable=False,
                        )
                    rows_value = facts.get("rows")
                    bytes_value = facts.get("bytes_written")
                    error_value = facts.get("error")
                    record_publication_event(
                        db,
                        run_id=run.id,
                        lease_token=lease_token,
                        user_id=run.user_id,
                        locator=target_locator,
                        connection_generation=target_generation,
                        write_mode=snapshot.write_policy.kind,
                        event=event,
                        staging_name=str(facts.get("staging_name", "")),
                        rows=(
                            rows_value
                            if isinstance(rows_value, int) and not isinstance(rows_value, bool)
                            else 0
                        ),
                        bytes_written=(
                            bytes_value
                            if isinstance(bytes_value, int) and not isinstance(bytes_value, bool)
                            else 0
                        ),
                        error=error_value if isinstance(error_value, ConnectorError) else None,
                    )

                cast(dict[str, Any], destination_credentials)["_publication_event"] = (
                    persist_databricks_publication
                )
        execute_transfer(
            db,
            run=run,
            lease_token=lease_token,
            snapshot=snapshot,
            source_credentials=source_credentials,
            destination_credentials=destination_credentials,
            settings=settings,
            cancel_requested=lambda: (
                _cancel_flag(db, run_id) or (stop_event is not None and stop_event.is_set())
            ),
            lease_lost=keeper.lost.is_set,
            source_resolver=source_reader_for,
            destination_resolver=destination_writer_for,
        )
    except ConnectorError as exc:
        db.rollback()
        failed = db.get(PipelineRun, run_id)
        if failed is None:
            return True
        try:
            if exc.code is TransferErrorCode.COMPUTE_NOT_READY:
                if exc.retryable and pipeline_runs.defer_run_for_databricks_compute(
                    db,
                    failed,
                    lease_token=lease_token,
                    settings=settings,
                ):
                    return True
                if not exc.retryable and failed.stage == "authenticate" and failed.loaded_rows == 0:
                    pipeline_runs.block_compute_readiness(
                        db,
                        failed,
                        lease_token=lease_token,
                        summary=str(exc),
                    )
                    return True
            pipeline_runs.fail_run(
                db,
                failed,
                code=exc.code,
                summary=str(exc),
                retryable=bool(exc.retryable),
                lease_token=lease_token,
                data_impact=exc.data_impact,
                provider_correlation_id=exc.provider_correlation_id,
                http_status=exc.http_status,
                sqlstate=exc.sqlstate,
                exception_type=type(exc).__name__,
            )
        except RunConflictError:
            log.warning(
                "pipeline lease was lost before failure could be recorded", extra={"run_id": run_id}
            )
    except RunConflictError:
        db.rollback()
        log.warning("pipeline worker stopped after losing its lease", extra={"run_id": run_id})
    except Exception as exc:
        exception_type = type(exc).__name__
        log.error(
            "pipeline run %s failed unexpectedly (%s)",
            run_id,
            exception_type,
            extra={"exception_type": exception_type},
        )
        log_event(
            log,
            "pipeline.run.unexpected_failure",
            outcome="failed",
            run_id=run_id,
            reference_id=run_id,
            user_id=run.user_id,
            operation="transfer",
            exception_type=exception_type,
            traceback=safe_exception_traceback((type(exc), exc, exc.__traceback__)),
        )
        db.rollback()
        failed = db.get(PipelineRun, run_id)
        if failed is not None:
            try:
                pipeline_runs.fail_run(
                    db,
                    failed,
                    code=TransferErrorCode.INTERNAL_ERROR,
                    summary="The transfer failed unexpectedly.",
                    lease_token=lease_token,
                )
            except RunConflictError:
                log.warning(
                    "pipeline lease was lost before an unexpected failure could be recorded",
                    extra={"run_id": run_id},
                )
    finally:
        keeper.stop()
        clear_request_id()
    return True


def _credentials_for(db, settings, *, user, provider, snapshot) -> dict[str, str]:
    if not user.is_active:
        raise ConnectorError(
            TransferErrorCode.PERMISSION_DENIED,
            "The run owner account is disabled.",
            retryable=False,
        )
    if provider == "csv":
        from app.models import PipelineUpload

        upload_id = snapshot.source_upload_id or getattr(snapshot.source, "upload_id", "")
        upload = db.get(PipelineUpload, upload_id) if upload_id else None
        if upload is None or upload.user_id != user.id:
            raise ConnectorError(
                TransferErrorCode.SOURCE_NOT_FOUND, "The CSV upload is no longer available."
            )
        if upload.checksum_sha256 != getattr(
            snapshot.source, "checksum_sha256", upload.checksum_sha256
        ):
            raise ConnectorError(
                TransferErrorCode.SCHEMA_DRIFT, "The CSV upload checksum no longer matches."
            )
        from app.services.csv_uploads import inspection_from_upload

        inspection = inspection_from_upload(upload)
        return {
            "content": upload.content.decode("utf-8") if isinstance(upload.content, bytes) else "",
            "delimiter": inspection.delimiter,
            "quote_char": inspection.quote_char,
            "columns": json.dumps([column.name for column in inspection.columns]),
            "column_types": json.dumps([column.inferred_type for column in inspection.columns]),
            "column_timezones": json.dumps(
                [column.timezone_aware for column in inspection.columns]
            ),
            "column_decimal_specs": json.dumps(
                [
                    {
                        "precision": column.decimal_precision,
                        "scale": column.decimal_scale,
                    }
                    for column in inspection.columns
                ]
            ),
        }
    credentials = decrypt_user_credentials_for_run(db, settings, user=user, provider=provider)
    if provider == "databricks":
        revision = db.scalar(
            select(UserSecret.updated_at).where(
                UserSecret.user_id == user.id,
                UserSecret.provider == provider,
            )
        )
        active_generation = (
            revision.isoformat(timespec="microseconds") if revision is not None else ""
        )
        locators = []
        if snapshot.source_provider == provider:
            locators.append(snapshot.source)
        if snapshot.destination_provider == provider:
            locators.append(snapshot.destination)
        if not active_generation or any(
            getattr(locator, "connection_generation", "") != active_generation
            for locator in locators
        ):
            raise ConnectorError(
                TransferErrorCode.CREDENTIALS_STALE,
                "The Databricks connection changed after this pipeline was reviewed. Update the route and review its sensitive-data actions again.",
                retryable=False,
            )
        credentials["connection_generation"] = active_generation
    return credentials


def _cancel_flag(db: Session, run_id: str) -> bool:
    # ``db.get`` may return the worker's already-loaded PipelineRun from the
    # identity map. Cancellation is requested by a different transaction, so
    # read this one scalar directly from the database on every check.
    return (
        db.scalar(select(PipelineRun.cancel_requested_at).where(PipelineRun.id == run_id))
        is not None
    )
