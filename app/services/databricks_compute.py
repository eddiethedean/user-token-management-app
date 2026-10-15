"""Durable Databricks compute-start coordination and readiness polling."""

from __future__ import annotations

import hashlib
import json
import secrets
from collections.abc import Callable, Mapping
from datetime import timedelta
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import Settings
from app.connectors.errors import ConnectorError, TransferErrorCode
from app.connectors.locators import databricks_workspace_id
from app.models import DatabricksComputeAttempt, PipelineRun, User, UserSecret, utcnow

_READY_STATES = {"RUNNING", "RESIZING"}
_TERMINAL_STATES = {"INTERNAL_ERROR", "ERROR", "FAILED", "DELETED"}


def create_compute_attempt(
    db: Session,
    *,
    user: User,
    credentials: dict[str, str],
    settings: Settings,
) -> tuple[DatabricksComputeAttempt, bool]:
    """Reserve one durable start observation before issuing a provider request."""

    workspace_id = databricks_workspace_id(credentials.get("workspace_url", ""))
    compute_kind = credentials.get("compute_kind", "")
    compute_id = credentials.get("compute_id", "")
    generation = credentials.get("connection_generation", "")
    if not workspace_id or not compute_kind or not compute_id or not generation:
        raise ConnectorError(
            TransferErrorCode.CREDENTIALS_STALE,
            "The saved Databricks connection must be reviewed again before starting compute.",
            retryable=False,
        )
    compute_key = _hash_key([workspace_id, compute_kind, compute_id])
    active = db.scalar(
        select(DatabricksComputeAttempt).where(
            DatabricksComputeAttempt.active_key == compute_key,
        )
    )
    if active is not None:
        return active, False

    now = utcnow()
    attempt = DatabricksComputeAttempt(
        user_id=user.id,
        workspace_id=workspace_id,
        compute_key=compute_key,
        active_key=compute_key,
        compute_kind=compute_kind,
        compute_id=compute_id,
        connection_generation=generation,
        status="starting",
        last_state="UNKNOWN",
        requested_at=now,
        deadline_at=now + timedelta(seconds=settings.pipeline_databricks_startup_timeout_seconds),
        next_poll_at=now + timedelta(seconds=settings.pipeline_databricks_startup_poll_seconds),
    )
    try:
        with db.begin_nested():
            db.add(attempt)
            db.flush()
    except IntegrityError:
        # The unique active key is the cross-process fence. A concurrent caller
        # may have won after our read; retain the outer transaction and return
        # only generic in-progress state to this owner.
        active = db.scalar(
            select(DatabricksComputeAttempt).where(
                DatabricksComputeAttempt.active_key == compute_key,
            )
        )
        if active is None:
            raise ConnectorError(
                TransferErrorCode.PROVIDER_UNAVAILABLE,
                "Databricks startup coordination is temporarily unavailable. Retry the status check.",
                retryable=True,
            ) from None
        return active, False
    return attempt, True


def request_compute_start(
    db: Session,
    *,
    settings: Settings,
    user: User,
    credentials: dict[str, str],
    connector_resolver: Callable[[str], object],
    observed_state: str = "",
    pipeline_run: PipelineRun | None = None,
    lease_token: str = "",
    require_opt_in: bool = False,
) -> dict[str, str]:
    """Persist and submit one explicit or consented, deduplicated start request."""

    if not user.is_active:
        raise ConnectorError(
            TransferErrorCode.PERMISSION_DENIED,
            "The Databricks connection owner is no longer active.",
            retryable=False,
        )
    if require_opt_in and (
        not settings.pipeline_databricks_startup_contract_verified
        or credentials.get("startup_policy") != "allow_start"
    ):
        raise ConnectorError(
            TransferErrorCode.PERMISSION_DENIED,
            "Automatic Databricks startup is not authorized for this connection.",
            retryable=False,
        )
    if pipeline_run is not None:
        from app.services.pipeline_runs import _refresh_and_require_lease
        from app.services.pipeline_state import RunConflictError

        if not lease_token:
            raise RunConflictError("A Databricks startup request requires the current run lease.")
        _refresh_and_require_lease(db, pipeline_run, lease_token)
        if pipeline_run.user_id != user.id:
            raise ConnectorError(
                TransferErrorCode.PERMISSION_DENIED,
                "The Databricks startup request is not owned by this run.",
                retryable=False,
            )
        if pipeline_run.stage != "authenticate":
            raise ConnectorError(
                TransferErrorCode.COMPUTE_NOT_READY,
                "Databricks compute stopped after source processing began. Review the run before retrying.",
                retryable=False,
            )
        if pipeline_run.databricks_compute_start_deadline_at is not None:
            now = utcnow()
            if now >= pipeline_run.databricks_compute_start_deadline_at:
                raise ConnectorError(
                    TransferErrorCode.COMPUTE_NOT_READY,
                    "Databricks compute did not become ready before the startup deadline. Retry after checking compute status.",
                    retryable=False,
                )
            if pipeline_run.databricks_compute_attempt_id:
                existing = db.get(
                    DatabricksComputeAttempt, pipeline_run.databricks_compute_attempt_id
                )
                if (
                    existing is None
                    or existing.user_id != user.id
                    or existing.connection_generation
                    != credentials.get("connection_generation", "")
                ):
                    raise ConnectorError(
                        TransferErrorCode.CREDENTIALS_STALE,
                        "The Databricks startup attempt no longer matches this connection.",
                        retryable=False,
                    )
                if existing.status not in {"starting", "ready"}:
                    raise ConnectorError(
                        TransferErrorCode.COMPUTE_NOT_READY,
                        "The Databricks startup attempt ended before compute became ready. Check status and start it explicitly before retrying.",
                        retryable=False,
                    )
            return {
                "outcome": "already_starting",
                "state": observed_state or "UNKNOWN",
                "kind": credentials.get("compute_kind", ""),
            }

    attempt, created = create_compute_attempt(
        db,
        user=user,
        credentials=credentials,
        settings=settings,
    )
    if pipeline_run is not None:
        pipeline_run.databricks_compute_start_deadline_at = attempt.deadline_at
        if attempt.user_id == user.id:
            pipeline_run.databricks_compute_attempt_id = attempt.id
    if not created:
        if pipeline_run is not None:
            db.commit()
        if attempt.status != "starting":
            raise ConnectorError(
                TransferErrorCode.COMPUTE_NOT_READY,
                "Databricks compute has an unresolved prior startup attempt. Check its status before retrying.",
                retryable=False,
            )
        return {
            "outcome": "already_starting",
            "state": attempt.last_state,
            "kind": attempt.compute_kind,
        }
    # The attempt, including its run association, is durable before a provider
    # side effect. A process restart can resume only the observation lease.
    db.commit()

    active_revision = db.scalar(
        select(UserSecret.updated_at).where(
            UserSecret.user_id == user.id,
            UserSecret.provider == "databricks",
        )
    )
    active_generation = (
        active_revision.isoformat(timespec="microseconds") if active_revision is not None else ""
    )
    if active_generation != attempt.connection_generation:
        error = ConnectorError(
            TransferErrorCode.CREDENTIALS_STALE,
            "The Databricks connection changed before compute could be started. Save the connection and try again.",
            retryable=False,
        )
        record_start_failure(db, attempt_id=attempt.id, error=error)
        raise error

    if observed_state and observed_state.upper() not in {"TERMINATED", "STOPPED"}:
        result = {
            "outcome": "start_submitted",
            "state": observed_state.upper(),
            "kind": attempt.compute_kind,
        }
    else:
        try:
            connector = connector_resolver("databricks")
            start = getattr(connector, "start_compute", None)
            if not callable(start):
                raise ConnectorError(
                    TransferErrorCode.INTERNAL_ERROR,
                    "The Databricks connector does not support compute startup.",
                    retryable=False,
                )
            from typing import cast

            result = cast(dict[str, str], start(credentials))
        except ConnectorError as exc:
            _require_current_start_connection(db, attempt_id=attempt.id)
            if exc.code in {
                TransferErrorCode.COMPUTE_NOT_READY,
                TransferErrorCode.CONNECTION_TIMEOUT,
                TransferErrorCode.PROVIDER_UNAVAILABLE,
                TransferErrorCode.RATE_LIMITED,
            }:
                record_start_uncertain(db, attempt_id=attempt.id, error=exc)
                return {
                    "outcome": "start_uncertain",
                    "state": attempt.last_state,
                    "kind": attempt.compute_kind,
                }
            record_start_failure(db, attempt_id=attempt.id, error=exc)
            raise

    record_start_submission(
        db,
        attempt_id=attempt.id,
        result=result,
        settings=settings,
    )
    attempt = _require_current_start_connection(db, attempt_id=attempt.id)
    stored = db.scalar(
        select(UserSecret)
        .where(
            UserSecret.user_id == user.id,
            UserSecret.provider == "databricks",
        )
        .execution_options(populate_existing=True)
    )
    if (
        stored is not None
        and stored.updated_at.isoformat(timespec="microseconds") == attempt.connection_generation
    ):
        updated = db.execute(
            update(UserSecret)
            .where(UserSecret.id == stored.id, UserSecret.updated_at == stored.updated_at)
            .values(
                runtime_status="RUNNING" if attempt.status == "ready" else "STARTING",
                runtime_updated_at=utcnow(),
                updated_at=stored.updated_at,
            )
        )
        if int(getattr(updated, "rowcount", 0) or 0) != 1:
            raise _start_connection_changed()
    else:
        raise _start_connection_changed()
    from app.services.audit import record_event

    record_event(
        db,
        "databricks.compute_start_requested",
        actor=user,
        target=user,
        detail={
            "kind": result.get("kind", attempt.compute_kind),
            "outcome": result.get("outcome", "start_submitted"),
            "compute_id": credentials.get("compute_id", ""),
            "attempt_id": attempt.id,
            "automatic": require_opt_in,
            "run_id": pipeline_run.id if pipeline_run is not None else "",
        },
    )
    db.commit()
    return result


def _start_connection_changed() -> ConnectorError:
    return ConnectorError(
        TransferErrorCode.CREDENTIALS_STALE,
        "The Databricks connection changed while compute was being started. Review the saved connection and try again.",
        retryable=False,
    )


def _require_current_start_connection(db: Session, *, attempt_id: str) -> DatabricksComputeAttempt:
    # A provider call can outlive credential rotation or observation cancellation.
    # Reload persisted state before accepting its result.
    attempt = db.get(DatabricksComputeAttempt, attempt_id, populate_existing=True)
    if attempt is None:
        raise _start_connection_changed()
    revision = db.scalar(
        select(UserSecret.updated_at).where(
            UserSecret.user_id == attempt.user_id,
            UserSecret.provider == "databricks",
        )
    )
    if (
        attempt.status == "stale"
        or revision is None
        or revision.isoformat(timespec="microseconds") != attempt.connection_generation
    ):
        error = _start_connection_changed()
        record_start_failure(db, attempt_id=attempt.id, error=error)
        raise error
    if attempt.status not in {"starting", "ready"}:
        raise ConnectorError(
            TransferErrorCode.COMPUTE_NOT_READY,
            "The Databricks startup attempt is no longer active. Check compute status before retrying.",
            retryable=False,
        )
    return attempt


def record_start_submission(
    db: Session,
    *,
    attempt_id: str,
    result: dict[str, str],
    settings: Settings,
) -> DatabricksComputeAttempt | None:
    attempt = db.get(
        DatabricksComputeAttempt, attempt_id, populate_existing=True, with_for_update=True
    )
    if attempt is None or attempt.status != "starting":
        return attempt
    now = utcnow()
    attempt.last_state = str(result.get("state", "UNKNOWN")).upper()[:32]
    attempt.checked_at = now
    if result.get("outcome") == "already_running":
        _finish(attempt, status="ready", state="RUNNING", now=now)
    else:
        attempt.next_poll_at = now + timedelta(
            seconds=settings.pipeline_databricks_startup_poll_seconds
        )
    db.commit()
    return attempt


def record_start_uncertain(
    db: Session,
    *,
    attempt_id: str,
    error: ConnectorError,
) -> None:
    """Keep an ambiguous provider start fenced until observation resolves it."""

    attempt = db.get(
        DatabricksComputeAttempt, attempt_id, populate_existing=True, with_for_update=True
    )
    if attempt is None or attempt.status != "starting":
        return
    attempt.error_code = str(error.code)[:64]
    attempt.error_summary = error.summary[:240]
    attempt.next_poll_at = utcnow()
    db.commit()


def record_start_failure(
    db: Session,
    *,
    attempt_id: str,
    error: ConnectorError,
) -> None:
    attempt = db.get(DatabricksComputeAttempt, attempt_id, populate_existing=True)
    if attempt is None or attempt.status != "starting":
        return
    now = utcnow()
    _finish(
        attempt,
        status="stale" if error.code is TransferErrorCode.CREDENTIALS_STALE else "failed",
        state=attempt.last_state,
        now=now,
        error_code=str(error.code),
        error_summary=error.summary,
    )
    stored = db.scalar(
        select(UserSecret).where(
            UserSecret.user_id == attempt.user_id,
            UserSecret.provider == "databricks",
        )
    )
    if (
        stored is not None
        and stored.updated_at.isoformat(timespec="microseconds") == attempt.connection_generation
    ):
        from sqlalchemy import update

        db.execute(
            update(UserSecret)
            .where(UserSecret.id == stored.id, UserSecret.updated_at == stored.updated_at)
            .values(
                runtime_status="FAILED",
                runtime_updated_at=now,
                updated_at=UserSecret.updated_at,
            )
        )
    db.commit()


def cancel_compute_observation(
    db: Session,
    *,
    user_id: str,
    attempt_id: str,
) -> bool:
    """Stop Data Mover polling for an owner's attempt without stopping compute."""

    attempt = db.scalar(
        select(DatabricksComputeAttempt)
        .where(
            DatabricksComputeAttempt.id == attempt_id,
            DatabricksComputeAttempt.user_id == user_id,
            DatabricksComputeAttempt.status == "starting",
        )
        .with_for_update()
    )
    if attempt is None:
        return False
    now = utcnow()
    attempt.status = "cancelled"
    attempt.completed_at = now
    attempt.checked_at = now
    attempt.next_poll_at = now
    attempt.lease_token = None
    attempt.lease_expires_at = None
    attempt.error_code = "startup_observation_cancelled"
    attempt.error_summary = "Data Mover readiness checks were stopped by the connection owner."

    stored = db.scalar(
        select(UserSecret).where(
            UserSecret.user_id == user_id,
            UserSecret.provider == "databricks",
        )
    )
    if (
        stored is not None
        and stored.updated_at.isoformat(timespec="microseconds") == attempt.connection_generation
    ):
        from sqlalchemy import update

        db.execute(
            update(UserSecret)
            .where(UserSecret.id == stored.id, UserSecret.updated_at == stored.updated_at)
            .values(
                runtime_status="OBSERVATION_CANCELLED",
                runtime_updated_at=now,
                updated_at=UserSecret.updated_at,
            )
        )
    owner = db.get(User, user_id)
    if owner is not None:
        from app.services.audit import record_event

        record_event(
            db,
            "databricks.compute_start_observation_cancelled",
            actor=owner,
            target=owner,
            detail={"kind": attempt.compute_kind, "attempt_id": attempt.id},
        )
    db.commit()
    return True


def resume_compute_observation(
    db: Session,
    *,
    user_id: str,
    attempt_id: str,
) -> str | None:
    """Resume polling an explicit start while preserving its original deadline."""

    attempt = db.scalar(
        select(DatabricksComputeAttempt)
        .where(
            DatabricksComputeAttempt.id == attempt_id,
            DatabricksComputeAttempt.user_id == user_id,
            DatabricksComputeAttempt.status == "cancelled",
            DatabricksComputeAttempt.active_key.is_not(None),
        )
        .with_for_update()
    )
    if attempt is None:
        return None
    now = utcnow()
    if now >= attempt.deadline_at:
        _finish(
            attempt,
            status="timed_out",
            state=attempt.last_state,
            now=now,
            error_code="compute_start_timeout",
            error_summary="Databricks compute did not become SQL ready before the startup deadline.",
        )
        db.commit()
        return "timed_out"
    attempt.status = "starting"
    attempt.next_poll_at = now
    attempt.completed_at = None
    attempt.error_code = None
    attempt.error_summary = ""
    stored = db.scalar(
        select(UserSecret).where(
            UserSecret.user_id == user_id,
            UserSecret.provider == "databricks",
        )
    )
    if (
        stored is not None
        and stored.updated_at.isoformat(timespec="microseconds") == attempt.connection_generation
    ):
        from sqlalchemy import update

        db.execute(
            update(UserSecret)
            .where(UserSecret.id == stored.id, UserSecret.updated_at == stored.updated_at)
            .values(
                runtime_status="STARTING", runtime_updated_at=now, updated_at=UserSecret.updated_at
            )
        )
    owner = db.get(User, user_id)
    if owner is not None:
        from app.services.audit import record_event

        record_event(
            db,
            "databricks.compute_start_observation_resumed",
            actor=owner,
            target=owner,
            detail={"kind": attempt.compute_kind, "attempt_id": attempt.id},
        )
    db.commit()
    return "resumed"


def claim_due_attempt(
    db: Session,
    *,
    now=None,
    lease_seconds: int = 60,
) -> tuple[str, str] | None:
    """Claim one due startup poll using a renewable database lease."""

    current = now or utcnow()
    attempt = db.scalar(
        select(DatabricksComputeAttempt)
        .where(
            DatabricksComputeAttempt.status == "starting",
            DatabricksComputeAttempt.active_key.is_not(None),
            DatabricksComputeAttempt.next_poll_at <= current,
            (DatabricksComputeAttempt.lease_expires_at.is_(None))
            | (DatabricksComputeAttempt.lease_expires_at <= current),
        )
        .order_by(DatabricksComputeAttempt.next_poll_at, DatabricksComputeAttempt.requested_at)
        .with_for_update(skip_locked=True)
        .limit(1)
    )
    if attempt is None:
        return None
    token = secrets.token_hex(32)
    attempt.lease_token = token
    attempt.lease_expires_at = current + timedelta(seconds=max(15, lease_seconds))
    db.commit()
    return attempt.id, token


def observe_attempt(
    db: Session,
    *,
    attempt_id: str,
    lease_token: str,
    state: str | None = None,
    error: ConnectorError | None = None,
    settings: Settings,
    now=None,
) -> bool:
    """Persist one state observation if this poller still owns the lease."""

    attempt = db.scalar(
        select(DatabricksComputeAttempt)
        .where(DatabricksComputeAttempt.id == attempt_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if attempt is None or attempt.status != "starting" or attempt.lease_token != lease_token:
        return False
    current = now or utcnow()
    attempt.checked_at = current
    attempt.lease_token = None
    attempt.lease_expires_at = None
    attempt.poll_count += 1
    if error is not None:
        attempt.error_code = str(error.code)[:64]
        attempt.error_summary = error.summary[:240]
        if error.code in {
            TransferErrorCode.AUTHENTICATION_FAILED,
            TransferErrorCode.CREDENTIALS_MISSING,
            TransferErrorCode.CREDENTIALS_STALE,
            TransferErrorCode.ENDPOINT_BLOCKED,
            TransferErrorCode.PERMISSION_DENIED,
            TransferErrorCode.UNSUPPORTED_TYPE,
        }:
            _finish(
                attempt,
                status=("stale" if error.code is TransferErrorCode.CREDENTIALS_STALE else "failed"),
                state=attempt.last_state,
                now=current,
                error_code=str(error.code),
                error_summary=error.summary,
            )
            db.commit()
            return True
    if state:
        current_state = state.upper()[:32]
        attempt.last_state = current_state
        attempt.error_code = None
        attempt.error_summary = ""
        if current_state in _READY_STATES:
            _finish(attempt, status="ready", state=current_state, now=current)
            db.commit()
            return True
        if current_state in _TERMINAL_STATES:
            _finish(
                attempt,
                status="failed",
                state=current_state,
                now=current,
                error_code="compute_start_failed",
                error_summary="Databricks reported that the selected compute could not start.",
            )
            db.commit()
            return True
    if current >= attempt.deadline_at:
        _finish(
            attempt,
            status="timed_out",
            state=attempt.last_state,
            now=current,
            error_code="compute_start_timeout",
            error_summary="Databricks compute did not become SQL ready before the startup deadline.",
        )
        db.commit()
        return True
    base = max(1, settings.pipeline_databricks_startup_poll_seconds)
    maximum = max(base, settings.pipeline_databricks_startup_max_poll_seconds)
    delay = min(maximum, base * (2 ** min(attempt.poll_count, 8)))
    # Stable bounded jitter prevents several app workers from polling a shared
    # workspace in lockstep while keeping the deadline observable.
    jitter = 0.9 + int(attempt.compute_key[:2], 16) / 2550
    attempt.next_poll_at = current + timedelta(seconds=max(1, int(delay * jitter)))
    db.commit()
    return True


def _finish(
    attempt: DatabricksComputeAttempt,
    *,
    status: str,
    state: str,
    now,
    error_code: str | None = None,
    error_summary: str = "",
) -> None:
    attempt.status = status
    attempt.last_state = state[:32]
    attempt.active_key = None
    attempt.completed_at = now
    attempt.checked_at = now
    attempt.next_poll_at = now
    attempt.lease_token = None
    attempt.lease_expires_at = None
    attempt.error_code = error_code
    attempt.error_summary = error_summary[:240]


def process_due_compute_attempt(
    *,
    settings: Settings,
    session_factory: Callable[[], Any],
    connector_resolver: Callable[[str], Any],
) -> bool:
    """Claim, observe, and persist one provider readiness check."""

    from app.services.audit import record_event
    from app.services.secrets import SecretStorageError, decrypt_user_credentials_for_run

    with session_factory() as db:
        claimed = claim_due_attempt(db)
    if claimed is None:
        return False
    attempt_id, lease_token = claimed
    credentials: dict[str, str] = {}
    request_error: ConnectorError | None = None
    with session_factory() as db:
        attempt = db.get(DatabricksComputeAttempt, attempt_id)
        if attempt is None or attempt.lease_token != lease_token:
            return True
        user = db.get(User, attempt.user_id)
        if user is None or not user.is_active:
            request_error = ConnectorError(
                TransferErrorCode.PERMISSION_DENIED,
                "The Databricks connection owner is no longer active.",
                retryable=False,
            )
        else:
            try:
                credentials = decrypt_user_credentials_for_run(
                    db,
                    settings,
                    user=user,
                    provider="databricks",
                    purpose="compute_start_observation",
                )
            except (ConnectorError, SecretStorageError, ValueError) as exc:
                request_error = (
                    exc
                    if isinstance(exc, ConnectorError)
                    else ConnectorError(
                        TransferErrorCode.PERMISSION_DENIED
                        if isinstance(exc, ValueError)
                        else TransferErrorCode.CREDENTIALS_MISSING,
                        "The Databricks provider is unavailable or its saved connection is no longer available.",
                        retryable=False,
                    )
                )
            if not request_error and (
                credentials.get("connection_generation") != attempt.connection_generation
                or credentials.get("compute_id") != attempt.compute_id
                or credentials.get("compute_kind") != attempt.compute_kind
            ):
                request_error = ConnectorError(
                    TransferErrorCode.CREDENTIALS_STALE,
                    "The saved Databricks workspace or compute selection changed during startup.",
                    retryable=False,
                )

    observed_state = ""
    if request_error is None:
        try:
            connector = connector_resolver("databricks")
            status_reader = getattr(connector, "compute_status", None)
            if not callable(status_reader):
                raise ConnectorError(
                    TransferErrorCode.INTERNAL_ERROR,
                    "The Databricks connector cannot check compute readiness.",
                    retryable=False,
                )
            status = status_reader(credentials)
            if not isinstance(status, Mapping):
                raise ConnectorError(
                    TransferErrorCode.PROVIDER_UNAVAILABLE,
                    "Databricks returned an invalid compute readiness response.",
                    retryable=True,
                )
            observed_state = _normalize_state(status.get("state"))
        except ConnectorError as exc:
            request_error = exc
        except Exception:
            request_error = ConnectorError(
                TransferErrorCode.PROVIDER_UNAVAILABLE,
                "Databricks compute readiness could not be checked. The startup attempt will retry.",
                retryable=True,
            )

    terminal_status = ""
    with session_factory() as db:
        changed = observe_attempt(
            db,
            attempt_id=attempt_id,
            lease_token=lease_token,
            state=observed_state,
            error=request_error,
            settings=settings,
        )
        if not changed:
            return True
        attempt = db.get(DatabricksComputeAttempt, attempt_id)
        if attempt is None:
            return True
        terminal_status = attempt.status
        now = utcnow()
        runtime_status = (
            "RUNNING"
            if terminal_status == "ready"
            else "TIMED_OUT"
            if terminal_status == "timed_out"
            else "FAILED"
            if terminal_status in {"failed", "stale"}
            else "STARTING"
        )
        stored = db.scalar(
            select(UserSecret).where(
                UserSecret.user_id == attempt.user_id,
                UserSecret.provider == "databricks",
            )
        )
        if (
            stored is not None
            and stored.updated_at.isoformat(timespec="microseconds")
            == attempt.connection_generation
        ):
            from sqlalchemy import update

            db.execute(
                update(UserSecret)
                .where(
                    UserSecret.id == stored.id,
                    UserSecret.updated_at == stored.updated_at,
                )
                .values(
                    runtime_status=runtime_status,
                    runtime_updated_at=now,
                    updated_at=UserSecret.updated_at,
                )
            )
        if terminal_status in {"ready", "failed", "stale", "timed_out"}:
            owner = db.get(User, attempt.user_id)
            if owner is not None:
                record_event(
                    db,
                    f"databricks.compute_start_{terminal_status}",
                    actor=owner,
                    target=owner,
                    detail={
                        "kind": attempt.compute_kind,
                        "state": attempt.last_state,
                        "attempt_id": attempt.id,
                    },
                )
        db.commit()
    return True


def _hash_key(parts: list[str]) -> str:
    return hashlib.sha256(
        json.dumps(parts, ensure_ascii=True, separators=(",", ":")).encode("ascii")
    ).hexdigest()


def _normalize_state(value: Any) -> str:
    state = str(value or "UNKNOWN").upper()
    return state if re_full_state(state) else "UNKNOWN"


def re_full_state(value: str) -> bool:
    return bool(value) and len(value) <= 32 and value.replace("_", "").isalnum()
