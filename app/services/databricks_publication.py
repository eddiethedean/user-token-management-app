"""Persistence-neutral coordination rules for Databricks destination operations."""

from __future__ import annotations

import hashlib
import json
import secrets
from collections.abc import Callable
from contextlib import AbstractContextManager
from datetime import UTC, timedelta
from typing import Any, cast

from sqlalchemy import or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import Settings
from app.connectors.errors import ConnectorError, TransferErrorCode
from app.domain.locators import DatabricksTableLocator
from app.models import (
    DatabricksPublication,
    PipelineRun,
    PipelineRunStatus,
    User,
    utcnow,
)


def record_publication_event(
    db: Session,
    *,
    run_id: str,
    lease_token: str,
    user_id: str,
    locator: DatabricksTableLocator,
    connection_generation: str,
    write_mode: str,
    event: str,
    staging_name: str = "",
    rows: int = 0,
    bytes_written: int = 0,
    error: ConnectorError | None = None,
) -> DatabricksPublication:
    """Persist the state before/after each Databricks write side effect."""

    _require_run_lease(db, run_id=run_id, lease_token=lease_token, user_id=user_id)
    target_key = _target_key(locator)
    record = db.get(DatabricksPublication, run_id)
    if event == "prepare_started":
        if record is not None:
            raise ConnectorError(
                TransferErrorCode.WORKER_LOST,
                "A prior Databricks publication record exists for this run. Recover it before retrying.",
                retryable=False,
            )
        record = DatabricksPublication(
            run_id=run_id,
            user_id=user_id,
            workspace_id=locator.workspace_id,
            connection_generation=connection_generation,
            target_key=target_key,
            active_target_key=target_key,
            target_locator_json=json.dumps(
                locator.model_dump(mode="json"), ensure_ascii=False, separators=(",", ":")
            ),
            write_mode=write_mode[:24],
            staging_name=staging_name[:520],
            state="preparing",
            submission_id=run_id,
        )
        try:
            with db.begin_nested():
                db.add(record)
                db.flush()
        except IntegrityError:
            raise ConnectorError(
                TransferErrorCode.DESTINATION_CONFLICT,
                "Another Data Mover run is publishing to this Databricks table. Retry after it finishes.",
                retryable=True,
            ) from None
        db.commit()
        return record

    if record is None or record.target_key != target_key:
        raise ConnectorError(
            TransferErrorCode.WORKER_LOST,
            "The durable Databricks publication record is missing or does not match this target.",
            retryable=False,
        )
    if record.user_id != user_id or record.connection_generation != connection_generation:
        raise ConnectorError(
            TransferErrorCode.CREDENTIALS_STALE,
            "The Databricks connection changed during publication. Reconcile this run before retrying.",
            retryable=False,
        )
    if event == "staging_ready":
        record.state = "staging"
    elif event == "batch_written":
        record.staged_rows = max(record.staged_rows, rows)
        record.staged_bytes = max(record.staged_bytes, bytes_written)
    elif event == "publish_submitted":
        record.state = "publishing"
        record.submission_id = run_id
        record.submitted_at = utcnow()
    elif event == "committed":
        record.state = "committed"
        record.active_target_key = None
        record.confirmed_at = utcnow()
    elif event == "publish_uncertain":
        record.state = "uncertain"
        record.active_target_key = target_key
        record.last_error_code = str(error.code)[:64] if error else "publish_uncertain"
        record.last_error_summary = (
            error.summary[:240]
            if error
            else "The Databricks publication outcome could not be confirmed."
        )
    elif event == "cleanup_pending":
        record.state = "cleanup_pending"
        if record.confirmed_at is None:
            record.active_target_key = target_key
        record.cleanup_updated_at = utcnow()
    elif event == "cleanup_confirmed":
        record.state = "complete" if record.confirmed_at is not None else "unchanged"
        record.active_target_key = None
        record.cleanup_updated_at = utcnow()
        record.recovery_token = None
        record.recovery_expires_at = None
    elif event == "abort_confirmed":
        if record.submitted_at is not None:
            raise ConnectorError(
                TransferErrorCode.PUBLISH_UNCERTAIN,
                "Publication was already submitted; cleanup cannot establish that the destination is unchanged.",
                retryable=False,
            )
        record.state = "unchanged"
        record.active_target_key = None
        record.cleanup_updated_at = utcnow()
    elif event == "abort_uncertain":
        if record.submitted_at is not None:
            record.state = "uncertain"
        else:
            record.state = "cleanup_pending"
        record.active_target_key = target_key
        record.cleanup_updated_at = utcnow()
        record.last_error_code = str(error.code)[:64] if error else "cleanup_uncertain"
        record.last_error_summary = (
            error.summary[:240] if error else "Databricks staging cleanup could not be confirmed."
        )
    else:
        raise ValueError(f"Unsupported Databricks publication event: {event}")
    db.commit()
    return record


def _require_run_lease(db: Session, *, run_id: str, lease_token: str, user_id: str) -> None:
    current = db.execute(
        select(PipelineRun.user_id, PipelineRun.lease_token).where(PipelineRun.id == run_id)
    ).one_or_none()
    if current is None or current.user_id != user_id or current.lease_token != lease_token:
        raise ConnectorError(
            TransferErrorCode.WORKER_LOST,
            "This worker no longer holds the Databricks publication lease.",
            retryable=False,
        )


def _target_key(locator: DatabricksTableLocator) -> str:
    identity = [
        locator.workspace_id,
        locator.catalog.casefold(),
        locator.schema_name.casefold(),
        locator.table.casefold(),
    ]
    return hashlib.sha256(
        json.dumps(identity, ensure_ascii=True, separators=(",", ":")).encode("ascii")
    ).hexdigest()


def process_pending_publication_recovery(
    *,
    settings: Settings,
    session_factory: Callable[[], AbstractContextManager[Session]],
    connector_resolver: Callable[[str], object],
) -> bool:
    """Recover pre-publication staging or reconcile exact commit metadata."""

    from app.services.secrets import SecretStorageError, decrypt_user_credentials_for_run

    terminal = (
        PipelineRunStatus.BLOCKED.value,
        PipelineRunStatus.SUCCEEDED.value,
        PipelineRunStatus.FAILED.value,
        PipelineRunStatus.CANCELLED.value,
        PipelineRunStatus.FAILED_NEEDS_RECONCILIATION.value,
    )
    now = utcnow()
    with session_factory() as db:
        candidate = db.execute(
            select(DatabricksPublication.run_id)
            .join(PipelineRun, PipelineRun.id == DatabricksPublication.run_id)
            .where(
                DatabricksPublication.state.in_(
                    (
                        "preparing",
                        "staging",
                        "committed",
                        "cleanup_pending",
                        "publishing",
                        "uncertain",
                    )
                ),
                or_(
                    DatabricksPublication.recovery_expires_at.is_(None),
                    DatabricksPublication.recovery_expires_at <= now,
                ),
                or_(
                    PipelineRun.status.in_(terminal),
                    PipelineRun.lease_expires_at.is_(None),
                    PipelineRun.lease_expires_at <= now,
                ),
            )
            .order_by(DatabricksPublication.updated_at)
            .with_for_update(skip_locked=True)
            .limit(1)
        ).scalar_one_or_none()
        if candidate is None:
            return False
        token = secrets.token_hex(32)
        claimed = db.execute(
            update(DatabricksPublication)
            .where(
                DatabricksPublication.run_id == candidate,
                or_(
                    DatabricksPublication.recovery_expires_at.is_(None),
                    DatabricksPublication.recovery_expires_at <= now,
                ),
            )
            .values(
                recovery_token=token,
                recovery_expires_at=now + timedelta(seconds=60),
            )
        )
        if int(getattr(claimed, "rowcount", 0) or 0) != 1:
            db.rollback()
            return False
        db.commit()

    with session_factory() as db:
        record = db.get(DatabricksPublication, candidate)
        if record is None or record.recovery_token != token:
            return True
        user = db.get(User, record.user_id)
        if user is None or not user.is_active:
            _defer_recovery(
                record,
                error_code="owner_unavailable",
                summary="The connection owner is unavailable for Databricks recovery.",
                delay_seconds=300,
            )
            db.commit()
            return True
        try:
            credentials = decrypt_user_credentials_for_run(
                db,
                settings,
                user=user,
                provider="databricks",
                purpose="publication_recovery",
            )
        except (SecretStorageError, ValueError):
            record = db.get(DatabricksPublication, candidate)
            if record is not None and record.recovery_token == token:
                _defer_recovery(
                    record,
                    error_code="credentials_unavailable",
                    summary="The saved Databricks credentials are unavailable for staging recovery.",
                    delay_seconds=300,
                    state=("cleanup_required" if record.confirmed_at is None else "uncertain"),
                )
                db.commit()
            return True
        if credentials.get("connection_generation") != record.connection_generation:
            _defer_recovery(
                record,
                error_code="credentials_stale",
                summary="The Databricks connection changed; confirm the current identity before recovery.",
                delay_seconds=3600,
                state=("cleanup_required" if record.confirmed_at is None else "uncertain"),
            )
            db.commit()
            return True
        if credentials.get("startup_policy") == "allow_start":
            recovery_user_id = record.user_id

            def request_recovery_start(observed_state: str) -> dict[str, str]:
                from app.services.databricks_compute import request_compute_start

                with session_factory() as startup_db:
                    startup_user = startup_db.get(User, recovery_user_id)
                    if startup_user is None or not startup_user.is_active:
                        raise ConnectorError(
                            TransferErrorCode.PERMISSION_DENIED,
                            "The Databricks connection owner is no longer active.",
                            retryable=False,
                        )
                    return request_compute_start(
                        startup_db,
                        settings=settings,
                        user=startup_user,
                        credentials=credentials,
                        connector_resolver=connector_resolver,
                        observed_state=observed_state,
                        require_opt_in=True,
                    )

            cast(dict[str, Any], credentials)["_startup_request"] = request_recovery_start
        operation = {
            "run_id": record.run_id,
            "state": record.state,
            "target_locator_json": record.target_locator_json,
            "submission_id": record.submission_id,
            "submitted_at_ms": (
                int(
                    (
                        record.submitted_at.replace(tzinfo=UTC)
                        if record.submitted_at.tzinfo is None
                        else record.submitted_at.astimezone(UTC)
                    ).timestamp()
                    * 1000
                )
                if record.submitted_at is not None
                else 0
            ),
        }

    from app.domain.locators import DatabricksTableLocator

    try:
        locator = DatabricksTableLocator.model_validate_json(operation["target_locator_json"])
        connector = connector_resolver("databricks")
        if operation["state"] in {"publishing", "uncertain"}:
            reconciler = getattr(connector, "reconcile_publication", None)
            if not callable(reconciler):
                raise RuntimeError("The Databricks connector cannot reconcile publications.")
            result = reconciler(
                credentials,
                locator,
                submission_id=operation["submission_id"],
                submitted_at_ms=operation["submitted_at_ms"],
            )
            if result != "committed":
                with session_factory() as db:
                    record = db.get(DatabricksPublication, operation["run_id"])
                    if record is not None and record.recovery_token == token:
                        _defer_recovery(
                            record,
                            error_code="publish_unconfirmed",
                            summary="No exact Databricks commit marker was found; the result remains uncertain.",
                            delay_seconds=300,
                            state="uncertain",
                        )
                        db.commit()
                return True
            with session_factory() as db:
                record = db.get(DatabricksPublication, operation["run_id"])
                if record is None or record.recovery_token != token:
                    return True
                record.state = "cleanup_pending"
                record.active_target_key = None
                record.confirmed_at = utcnow()
                record.submission_id = operation["submission_id"]
                # Keep the recovery lease through cleanup, including its error
                # path. A confirmed commit does not finish the owned recovery.
                db.commit()
            operation["state"] = "cleanup_pending"

        cleanup = getattr(connector, "cleanup_staging", None)
        if not callable(cleanup):
            raise RuntimeError("The Databricks connector cannot clean run staging.")
        cleanup(credentials, run_id=operation["run_id"])
        with session_factory() as db:
            record = db.scalar(
                select(DatabricksPublication)
                .where(
                    DatabricksPublication.run_id == operation["run_id"],
                    DatabricksPublication.recovery_token == token,
                )
                .with_for_update()
            )
            if record is None:
                return True
            record.state = "complete" if record.confirmed_at is not None else "unchanged"
            record.active_target_key = None
            record.cleanup_updated_at = utcnow()
            record.recovery_token = None
            record.recovery_expires_at = None
            record.last_error_code = None
            record.last_error_summary = ""
            db.commit()
    except ConnectorError as exc:
        with session_factory() as db:
            record = db.get(DatabricksPublication, operation["run_id"])
            if record is not None and record.recovery_token == token:
                _defer_recovery(
                    record,
                    error_code=str(exc.code),
                    summary=exc.summary,
                    delay_seconds=300,
                    state=(
                        "uncertain"
                        if operation["state"] in {"publishing", "uncertain"}
                        else "cleanup_pending"
                    ),
                )
                db.commit()
    except Exception:
        with session_factory() as db:
            record = db.get(DatabricksPublication, operation["run_id"])
            if record is not None and record.recovery_token == token:
                _defer_recovery(
                    record,
                    error_code="recovery_unavailable",
                    summary="Databricks recovery could not be confirmed; the publication record remains available for retry.",
                    delay_seconds=300,
                    state=(
                        "uncertain"
                        if operation["state"] in {"publishing", "uncertain"}
                        else "cleanup_pending"
                    ),
                )
                db.commit()
    return True


def _defer_recovery(
    record: DatabricksPublication,
    *,
    error_code: str,
    summary: str,
    delay_seconds: int,
    state: str | None = None,
) -> None:
    now = utcnow()
    if state is not None:
        record.state = state
    record.last_error_code = error_code[:64]
    record.last_error_summary = summary[:240]
    record.cleanup_updated_at = now
    record.recovery_token = None
    record.recovery_expires_at = now + timedelta(seconds=max(1, delay_seconds))
