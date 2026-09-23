"""Durable pipeline run state machine, leases, cancellation, and events."""

from __future__ import annotations

import json
import logging
import re
import shutil
from collections.abc import Callable
from datetime import timedelta
from pathlib import Path

from fastapi import Request
from sqlalchemy import and_, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.connectors.errors import TransferErrorCode
from app.connectors.locators import DefinitionSnapshot
from app.connectors.redaction import redact_mapping, redact_text
from app.connectors.registry import route_allowed, writer_enabled
from app.domain.feedback import DataImpact
from app.logging_config import log_event
from app.models import (
    PipelineDefinition,
    PipelineRun,
    PipelineRunEvent,
    PipelineRunStatus,
    User,
    new_id,
    utcnow,
)
from app.services.audit import record_event
from app.services.pipeline_state import (
    ACTIVE_STATUSES,
    ALLOWED_TRANSITIONS,
    STAGE_FOR_STATUS,
    TERMINAL_STATUSES,
    WORKER_OWNED_STATUSES,
    PipelineRunStateMachine,
    RunConflictError,
)

_STATE_MACHINE = PipelineRunStateMachine()
log = logging.getLogger(__name__)

__all__ = [
    "ACTIVE_STATUSES",
    "ALLOWED_TRANSITIONS",
    "STAGE_FOR_STATUS",
    "TERMINAL_STATUSES",
    "WORKER_OWNED_STATUSES",
    "RunConflictError",
    "enqueue_run",
    "janitor",
    "request_cancel",
    "record_reconciliation_review",
    "snapshot_from_definition",
]


def enqueue_run(
    db: Session,
    *,
    user: User,
    pipeline: PipelineDefinition,
    snapshot: DefinitionSnapshot,
    attempt: int = 1,
    parent_run_id: str | None = None,
    idempotency_token: str | None = None,
    request: Request | None = None,
    route_policy: Callable[[str, str], bool] | None = None,
    writer_policy: Callable[[str], bool] | None = None,
) -> PipelineRun:
    # Serialize this safety decision with the pipeline row so every enqueue
    # path observes the same unresolved-destination guard.
    locked_pipeline = db.scalar(
        select(PipelineDefinition).where(PipelineDefinition.id == pipeline.id).with_for_update()
    )
    if locked_pipeline is None or locked_pipeline.user_id != user.id:
        raise LookupError("That pipeline is no longer available.")
    safety_candidates = list(
        db.scalars(
            select(PipelineRun)
            .where(
                PipelineRun.user_id == user.id,
                PipelineRun.pipeline_definition_id == pipeline.id,
                PipelineRun.status.in_(
                    (
                        PipelineRunStatus.FAILED.value,
                        PipelineRunStatus.FAILED_NEEDS_RECONCILIATION.value,
                        PipelineRunStatus.CANCELLED.value,
                    )
                ),
            )
            .order_by(PipelineRun.created_at.desc())
        ).all()
    )
    unresolved = [
        candidate
        for candidate in safety_candidates
        if _run_requires_reconciliation_review(candidate)
        and not _reconciliation_reviewed(candidate)
    ]
    if unresolved:
        raise ValueError(
            "The previous transfer has uncertain destination state. Record reconciliation review before retrying."
        )
    latest_pipeline_run = db.scalar(
        select(PipelineRun)
        .where(
            PipelineRun.user_id == user.id,
            PipelineRun.pipeline_definition_id == pipeline.id,
        )
        .order_by(PipelineRun.created_at.desc(), PipelineRun.id.desc())
        .limit(1)
    )
    reviewed_parent = next(
        (
            candidate
            for candidate in safety_candidates
            if _run_requires_reconciliation_review(candidate)
            and _reconciliation_reviewed(candidate)
            and latest_pipeline_run is not None
            and candidate.id == latest_pipeline_run.id
        ),
        None,
    )
    if pipeline.legacy_unsupported:
        raise ValueError("That saved pipeline uses an unsupported provider and cannot be run.")
    is_route_allowed = route_policy or route_allowed
    is_writer_enabled = writer_policy or writer_enabled
    if not is_route_allowed(snapshot.source_provider, snapshot.destination_provider):
        raise ValueError("That saved pipeline uses an unsupported transfer route.")
    if not is_writer_enabled(snapshot.destination_provider):
        raise ValueError("That saved pipeline's destination writer is not enabled.")
    run = PipelineRun(
        id=new_id(),
        pipeline_definition_id=pipeline.id,
        user_id=user.id,
        definition_snapshot_json=snapshot.model_dump_json(),
        status=PipelineRunStatus.QUEUED.value,
        stage=STAGE_FOR_STATUS[PipelineRunStatus.QUEUED.value],
        attempt=max(attempt, (reviewed_parent.attempt + 1) if reviewed_parent else attempt),
        parent_run_id=parent_run_id or (reviewed_parent.id if reviewed_parent else None),
        idempotency_token=idempotency_token,
    )
    if idempotency_token:
        dialect_name = db.get_bind().dialect.name
        values = {
            "id": run.id,
            "pipeline_definition_id": run.pipeline_definition_id,
            "user_id": run.user_id,
            "definition_snapshot_json": run.definition_snapshot_json,
            "status": run.status,
            "stage": run.stage,
            "attempt": run.attempt,
            "parent_run_id": run.parent_run_id,
            "idempotency_token": run.idempotency_token,
            "reconciliation_required": False,
        }
        if dialect_name == "postgresql":
            from sqlalchemy.dialects.postgresql import insert
        elif dialect_name == "sqlite":
            from sqlalchemy.dialects.sqlite import insert
        else:
            insert = None
        if insert is not None:
            inserted_id = db.scalar(
                insert(PipelineRun)
                .values(**values)
                .on_conflict_do_nothing(
                    index_elements=[PipelineRun.user_id, PipelineRun.idempotency_token]
                )
                .returning(PipelineRun.id)
            )
            if inserted_id is None:
                existing = db.scalar(
                    select(PipelineRun).where(
                        PipelineRun.user_id == user.id,
                        PipelineRun.idempotency_token == idempotency_token,
                    )
                )
                if existing is None:
                    raise RunConflictError("The idempotent pipeline run could not be resolved.")
                return existing
            run = db.get(PipelineRun, inserted_id)
            if run is None:
                raise RunConflictError("The queued pipeline run could not be loaded.")
        else:
            try:
                with db.begin_nested():
                    db.add(run)
                    db.flush()
            except IntegrityError:
                existing = db.scalar(
                    select(PipelineRun).where(
                        PipelineRun.user_id == user.id,
                        PipelineRun.idempotency_token == idempotency_token,
                    )
                )
                if existing is None:
                    raise
                return existing
    else:
        db.add(run)
        db.flush()
    append_event(db, run, "Run queued.", stage="queued")
    record_event(
        db,
        "pipeline.run_queued",
        request=request,
        actor=user,
        target=user,
        detail={"run_id": run.id, "pipeline_id": pipeline.id},
    )
    log_event(
        log,
        "pipeline.run.queued",
        outcome="success",
        reference_id=run.id,
        run_id=run.id,
        pipeline_id=pipeline.id,
        user_id=user.id,
        attempt=run.attempt,
        operation="enqueue",
        stage=run.stage,
        provider=_run_provider_summary(run),
    )
    db.commit()
    db.refresh(run)
    return run


def _run_provider_summary(run: PipelineRun) -> str:
    """Return safe source/destination provider context for diagnostic events."""

    try:
        snapshot = json.loads(getattr(run, "definition_snapshot_json", "") or "{}")
    except (TypeError, ValueError):
        return ""
    if not isinstance(snapshot, dict):
        return ""
    source = str(snapshot.get("source_provider") or "")
    destination = str(snapshot.get("destination_provider") or "")
    if source and destination:
        return f"{source}->{destination}"
    return destination or source


def _run_duration_ms(run: PipelineRun) -> int | None:
    started_at = getattr(run, "started_at", None)
    finished_at = getattr(run, "finished_at", None)
    if started_at is None or finished_at is None:
        return None
    return max(0, int((finished_at - started_at).total_seconds() * 1000))


def _run_diagnostic_detail(
    run: PipelineRun,
    *,
    stage: str | None = None,
    provider_correlation_id: str = "",
    http_status: int | None = None,
    sqlstate: str = "",
    exception_type: str = "",
) -> dict[str, object]:
    """Build a useful run summary without copying locators, schemas, or payload data."""
    try:
        snapshot = json.loads(getattr(run, "definition_snapshot_json", "") or "{}")
    except (TypeError, ValueError):
        snapshot = {}
    if not isinstance(snapshot, dict):
        snapshot = {}

    try:
        verification = json.loads(getattr(run, "verification_json", "") or "{}")
    except (TypeError, ValueError):
        verification = {}
    if not isinstance(verification, dict):
        verification = {}

    detail: dict[str, object] = {
        "user_id": run.user_id,
        "run_id": run.id,
        "pipeline_id": run.pipeline_definition_id or "",
        "run_status": run.status,
        "stage": stage or run.stage,
        "attempt": int(run.attempt or 1),
        "queued_at": run.queued_at.isoformat() if run.queued_at else None,
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
        "duration_ms": _run_duration_ms(run),
        "source_provider": str(snapshot.get("source_provider") or ""),
        "destination_provider": str(snapshot.get("destination_provider") or ""),
        "source_rows": int(run.source_rows or 0),
        "source_bytes": int(run.source_bytes or 0),
        "loaded_rows": int(run.loaded_rows or 0),
        "loaded_bytes": int(run.loaded_bytes or 0),
        "data_impact": run.data_impact or "",
        "last_safe_stage": run.last_safe_stage or "",
        "retryable": bool(run.retryable),
        "reconciliation_required": bool(run.reconciliation_required),
    }
    for key in ("destination_rows_before", "destination_rows_after", "destination_row_delta"):
        value = verification.get(key)
        detail[key] = value if isinstance(value, int) and not isinstance(value, bool) else None
    verification_level = verification.get("verification_level")
    if isinstance(verification_level, str):
        detail["verification_level"] = verification_level[:64]

    if provider_correlation_id and re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", provider_correlation_id):
        detail["provider_correlation_id"] = provider_correlation_id
    if isinstance(http_status, int) and not isinstance(http_status, bool):
        detail["http_status"] = http_status
    if sqlstate and re.fullmatch(r"[0-9A-Z]{5}", sqlstate.upper()):
        detail["sqlstate"] = sqlstate.upper()
    if exception_type and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.]{0,127}", exception_type):
        detail["exception_type"] = exception_type

    if run.error_code:
        detail["error_code"] = run.error_code
    if run.error_summary:
        detail["error_summary"] = redact_text(run.error_summary)
    return redact_mapping(detail)


def _cancel_before_destination_work(
    db: Session,
    run: PipelineRun,
    *,
    last_safe_stage: str,
    message: str,
) -> None:
    """Persist a cancellation known to have occurred before destination writes."""
    resolved_impact = DataImpact.UNCHANGED
    run.last_safe_stage = last_safe_stage
    run.data_impact = resolved_impact.value
    run.reconciliation_required = False
    run.reconciliation_reviewed_at = None
    run.error_code = TransferErrorCode.CANCELLED_BY_USER.value
    run.error_summary = message
    run.retryable = False
    _set_status(run, PipelineRunStatus.CANCELLED.value, lease_token=run.lease_token)
    append_event(db, run, message, stage="cancelled")
    run.verification_json = json.dumps(
        _failure_facts(
            run,
            TransferErrorCode.CANCELLED_BY_USER.value,
            resolved_impact,
            reconciliation_required=False,
            last_safe_stage=last_safe_stage,
        ),
        separators=(",", ":"),
    )
    log_event(
        log,
        "pipeline.run.cancelled",
        outcome="cancelled",
        reference_id=run.id,
        run_id=run.id,
        pipeline_id=run.pipeline_definition_id or "",
        user_id=run.user_id,
        attempt=run.attempt,
        operation="transfer",
        stage="cancelled",
        provider=_run_provider_summary(run),
        duration_ms=_run_duration_ms(run),
        data_impact=resolved_impact.value,
        cause=run.error_summary,
    )


def request_cancel(db: Session, *, user: User, run_id: str) -> PipelineRun:
    run = owned_run(db, user=user, run_id=run_id)
    if run.status in TERMINAL_STATUSES:
        return run
    run.cancel_requested_at = utcnow()
    if run.status == PipelineRunStatus.QUEUED.value:
        _cancel_before_destination_work(
            db,
            run,
            last_safe_stage=run.stage,
            message="Run cancelled before a worker claimed it.",
        )
    else:
        append_event(db, run, "Cancellation requested.", stage=run.stage)
    db.commit()
    db.refresh(run)
    return run


def record_reconciliation_review(db: Session, *, user: User, run_id: str) -> PipelineRun:
    """Record that an operator reviewed an uncertain destination before retrying."""

    run = owned_run(db, user=user, run_id=run_id)
    if not _run_requires_reconciliation_review(run):
        raise ValueError("This pipeline run does not require reconciliation review.")
    try:
        verification = json.loads(run.verification_json or "{}")
    except (TypeError, ValueError):
        verification = {}
    if not isinstance(verification, dict):
        verification = {}
    reviewed_at = utcnow()
    verification["reconciliation_reviewed_at"] = reviewed_at.isoformat()
    run.reconciliation_reviewed_at = reviewed_at
    run.verification_json = json.dumps(redact_mapping(verification), separators=(",", ":"))
    append_event(db, run, "Operator recorded reconciliation review.", stage="reconcile")
    db.commit()
    db.refresh(run)
    return run


def owned_run(db: Session, *, user: User, run_id: str) -> PipelineRun:
    run = db.get(PipelineRun, run_id)
    if run is None or run.user_id != user.id:
        raise LookupError("That pipeline run is no longer available.")
    return run


def latest_run_map(db: Session, *, user: User, pipeline_ids: list[str]) -> dict[str, PipelineRun]:
    if not pipeline_ids:
        return {}
    runs = list(
        db.scalars(
            select(PipelineRun)
            .where(
                PipelineRun.user_id == user.id,
                PipelineRun.pipeline_definition_id.in_(pipeline_ids),
            )
            .order_by(PipelineRun.created_at.desc())
        ).all()
    )
    latest: dict[str, PipelineRun] = {}
    for run in runs:
        if run.pipeline_definition_id and run.pipeline_definition_id not in latest:
            latest[run.pipeline_definition_id] = run
    return latest


def events_after(
    db: Session, *, run: PipelineRun, after_sequence: int = 0
) -> list[PipelineRunEvent]:
    return list(
        db.scalars(
            select(PipelineRunEvent)
            .where(
                PipelineRunEvent.run_id == run.id,
                PipelineRunEvent.sequence > after_sequence,
            )
            .order_by(PipelineRunEvent.sequence.asc())
        ).all()
    )


def append_event(
    db: Session,
    run: PipelineRun,
    message: str,
    *,
    stage: str | None = None,
    level: str = "info",
    detail: dict | None = None,
) -> PipelineRunEvent:
    sequence = db.scalar(
        update(PipelineRun)
        .where(PipelineRun.id == run.id)
        .values(next_event_sequence=PipelineRun.next_event_sequence + 1)
        .returning(PipelineRun.next_event_sequence)
        .execution_options(synchronize_session=False)
    )
    if sequence is None:
        raise RunConflictError("The pipeline run no longer exists.")
    run.next_event_sequence = int(sequence)
    event = PipelineRunEvent(
        run_id=run.id,
        sequence=int(sequence),
        level=level,
        stage=stage or run.stage,
        message=redact_text(message)[:500],
        detail_json=json.dumps(redact_mapping(detail), separators=(",", ":")) if detail else "",
    )
    db.add(event)
    return event


def claim_run(
    db: Session,
    *,
    worker_id: str,
    lease_seconds: int,
    run_id: str | None = None,
) -> tuple[PipelineRun, str] | None:
    now = utcnow()
    conditions = [
        or_(
            PipelineRun.status == PipelineRunStatus.QUEUED.value,
            and_(
                PipelineRun.status.in_(WORKER_OWNED_STATUSES),
                PipelineRun.lease_expires_at.is_not(None),
                PipelineRun.lease_expires_at <= now,
            ),
        )
    ]
    if run_id:
        conditions.append(PipelineRun.id == run_id)
    statement = (
        select(PipelineRun)
        .join(User, User.id == PipelineRun.user_id)
        .where(*conditions)
        .where(User.status == "active")
        .order_by(PipelineRun.queued_at, PipelineRun.id)
        .limit(1)
    )
    if db.get_bind().dialect.name == "postgresql":
        statement = statement.with_for_update(skip_locked=True)
    run = db.scalar(statement)
    if run is None:
        return None
    cancellation_stage = run.stage
    if run.status in WORKER_OWNED_STATUSES and run.lease_expires_at is not None:
        if cancellation_stage in {"transfer", "verify"}:
            fail_run(
                db,
                run,
                code=TransferErrorCode.WORKER_LOST,
                summary="The worker lost the lease after destination writes began.",
                retryable=False,
                needs_reconciliation=True,
                # claim_run holds the current database row lock; the expired
                # worker must not be treated as an authorized lease holder.
                lease_token=None,
            )
            return None
        run.status = PipelineRunStatus.QUEUED.value
        run.stage = STAGE_FOR_STATUS[PipelineRunStatus.QUEUED.value]
        run.worker_id = None
        run.lease_token = None
        run.lease_expires_at = None
        append_event(db, run, "Stale worker lease recovered; run requeued.", stage="queued")
    if run.cancel_requested_at is not None:
        _cancel_before_destination_work(
            db,
            run,
            last_safe_stage=cancellation_stage,
            message="Run cancelled before destination writes began.",
        )
        db.commit()
        return None
    lease_token = new_id()
    run.worker_id = worker_id
    run.lease_token = lease_token
    run.lease_expires_at = now + timedelta(seconds=lease_seconds)
    run.heartbeat_at = now
    if run.started_at is None:
        run.started_at = now
    _transition(run, PipelineRunStatus.VALIDATING.value, lease_token=lease_token)
    append_event(db, run, "Worker claimed the run.", stage="authenticate")
    db.commit()
    db.refresh(run)
    return run, lease_token


def heartbeat(db: Session, run: PipelineRun, *, lease_token: str, lease_seconds: int) -> None:
    _refresh_and_require_lease(db, run, lease_token)
    now = utcnow()
    run.heartbeat_at = now
    run.lease_expires_at = now + timedelta(seconds=lease_seconds)
    db.commit()


def renew_lease(db: Session, *, run_id: str, lease_token: str, lease_seconds: int) -> bool:
    """Renew a lease from a dedicated heartbeat session using a database CAS."""

    now = utcnow()
    result = db.execute(
        update(PipelineRun)
        .where(
            PipelineRun.id == run_id,
            PipelineRun.lease_token == lease_token,
            PipelineRun.status.in_(WORKER_OWNED_STATUSES),
            PipelineRun.lease_expires_at > now,
        )
        .values(
            heartbeat_at=now,
            lease_expires_at=now + timedelta(seconds=lease_seconds),
            updated_at=now,
        )
    )
    renewed = bool(getattr(result, "rowcount", 0))
    db.commit()
    return renewed


def transition(
    db: Session,
    run: PipelineRun,
    status: str,
    *,
    lease_token: str,
    message: str | None = None,
) -> None:
    _refresh_and_require_lease(db, run, lease_token)
    _transition(run, status, lease_token=lease_token)
    if message:
        append_event(db, run, message, stage=run.stage)
    db.commit()


def add_counters(
    db: Session,
    run: PipelineRun,
    *,
    lease_token: str,
    source_rows: int = 0,
    source_bytes: int = 0,
    loaded_rows: int = 0,
    loaded_bytes: int = 0,
) -> None:
    _refresh_and_require_lease(db, run, lease_token)
    run.source_rows += source_rows
    run.source_bytes += source_bytes
    run.loaded_rows += loaded_rows
    run.loaded_bytes += loaded_bytes
    db.commit()


def complete_run(
    db: Session,
    run: PipelineRun,
    *,
    lease_token: str,
    source_manifest: dict | None = None,
    destination_manifest: dict | None = None,
    verification: dict | None = None,
) -> None:
    _refresh_and_require_lease(db, run, lease_token)
    run.source_manifest_json = json.dumps(
        redact_mapping(source_manifest or {}), separators=(",", ":")
    )
    run.destination_manifest_json = json.dumps(
        redact_mapping(destination_manifest or {}), separators=(",", ":")
    )
    run.verification_json = json.dumps(redact_mapping(verification or {}), separators=(",", ":"))
    run.last_safe_stage = run.stage
    verification_level = str((verification or {}).get("verification_level", "")).casefold()
    completion_impact = DataImpact.VERIFIED if verification_level == "exact" else DataImpact.CHANGED
    run.data_impact = completion_impact.value
    run.reconciliation_required = False
    run.reconciliation_reviewed_at = None
    _set_status(run, PipelineRunStatus.SUCCEEDED.value, lease_token=lease_token)
    append_event(db, run, "Transfer succeeded.", stage="verify")
    run_detail = _run_diagnostic_detail(run)
    record_event(
        db,
        "pipeline.run.completed",
        target=db.get(User, run.user_id),
        outcome="success",
        detail=run_detail,
    )
    log_event(
        log,
        "pipeline.run.completed",
        outcome="success",
        reference_id=run.id,
        run_id=run.id,
        pipeline_id=run.pipeline_definition_id or "",
        user_id=run.user_id,
        attempt=run.attempt,
        operation="transfer",
        stage=run.stage,
        provider=_run_provider_summary(run),
        duration_ms=_run_duration_ms(run),
        data_impact=completion_impact.value,
        run_status=run.status,
        queued_at=run_detail["queued_at"],
        started_at=run_detail["started_at"],
        finished_at=run_detail["finished_at"],
        source_provider=run_detail["source_provider"],
        destination_provider=run_detail["destination_provider"],
        source_rows=run.source_rows,
        source_bytes=run.source_bytes,
        loaded_rows=run.loaded_rows,
        loaded_bytes=run.loaded_bytes,
        destination_rows_before=run_detail["destination_rows_before"],
        destination_rows_after=run_detail["destination_rows_after"],
        destination_row_delta=run_detail["destination_row_delta"],
        verification_level=run_detail.get("verification_level", ""),
        last_safe_stage=run.last_safe_stage or "",
        reconciliation_required=run.reconciliation_required,
    )
    db.commit()


def fail_run(
    db: Session,
    run: PipelineRun,
    *,
    code: TransferErrorCode | str,
    summary: str,
    retryable: bool = False,
    needs_reconciliation: bool = False,
    data_impact: DataImpact | None = None,
    lease_token: str | None = None,
    provider_correlation_id: str = "",
    http_status: int | None = None,
    sqlstate: str = "",
    exception_type: str = "",
    verification_facts: dict | None = None,
) -> None:
    if lease_token:
        _refresh_and_require_lease(db, run, lease_token)
    code_value = str(code)
    failure_stage = run.stage
    effective_needs_reconciliation = needs_reconciliation or _failure_requires_reconciliation(
        run, code_value
    )
    resolved_impact = data_impact or _failure_data_impact(
        run, code_value, needs_reconciliation=effective_needs_reconciliation
    )
    run.error_code = code_value
    run.error_summary = redact_text(summary)[:500]
    run.retryable = retryable and not effective_needs_reconciliation
    status = (
        PipelineRunStatus.FAILED_NEEDS_RECONCILIATION.value
        if effective_needs_reconciliation
        else PipelineRunStatus.FAILED.value
    )
    run.last_safe_stage = failure_stage
    run.data_impact = str(resolved_impact)
    run.reconciliation_required = effective_needs_reconciliation
    run.reconciliation_reviewed_at = None
    _set_status(run, status, lease_token=lease_token)
    failure_facts = _failure_facts(
        run,
        code_value,
        resolved_impact,
        reconciliation_required=effective_needs_reconciliation,
        last_safe_stage=failure_stage,
    )
    if verification_facts:
        failure_facts.update(redact_mapping(verification_facts))
    run.verification_json = json.dumps(failure_facts, separators=(",", ":"))
    append_event(
        db, run, run.error_summary or "The transfer failed.", stage=failure_stage, level="error"
    )
    run_detail = _run_diagnostic_detail(
        run,
        stage=failure_stage,
        provider_correlation_id=provider_correlation_id,
        http_status=http_status,
        sqlstate=sqlstate,
        exception_type=exception_type,
    )
    record_event(
        db,
        "pipeline.run.failed",
        target=db.get(User, run.user_id),
        outcome="failure",
        detail=run_detail,
    )
    log_event(
        log,
        "pipeline.run.failed",
        outcome="uncertain" if resolved_impact == DataImpact.UNCERTAIN else "failed",
        reference_id=run.id,
        error_code=code_value,
        run_id=run.id,
        pipeline_id=run.pipeline_definition_id or "",
        user_id=run.user_id,
        attempt=run.attempt,
        operation="transfer",
        stage=failure_stage,
        provider=_run_provider_summary(run),
        duration_ms=_run_duration_ms(run),
        retryable=run.retryable,
        data_impact=str(resolved_impact),
        cause=run.error_summary or "",
        run_status=run.status,
        queued_at=run_detail["queued_at"],
        started_at=run_detail["started_at"],
        finished_at=run_detail["finished_at"],
        source_provider=run_detail["source_provider"],
        destination_provider=run_detail["destination_provider"],
        source_rows=run.source_rows,
        source_bytes=run.source_bytes,
        loaded_rows=run.loaded_rows,
        loaded_bytes=run.loaded_bytes,
        destination_rows_before=run_detail["destination_rows_before"],
        destination_rows_after=run_detail["destination_rows_after"],
        destination_row_delta=run_detail["destination_row_delta"],
        verification_level=run_detail.get("verification_level", ""),
        last_safe_stage=run.last_safe_stage or "",
        reconciliation_required=run.reconciliation_required,
        provider_correlation_id=run_detail.get("provider_correlation_id", ""),
        http_status=run_detail.get("http_status"),
        sqlstate=run_detail.get("sqlstate", ""),
        exception_type=run_detail.get("exception_type", ""),
    )
    db.commit()


def cancel_claimed_run(
    db: Session,
    run: PipelineRun,
    *,
    lease_token: str,
    data_impact: DataImpact | None = None,
) -> None:
    _refresh_and_require_lease(db, run, lease_token)
    resolved_impact = data_impact or (
        DataImpact.UNCERTAIN if run.loaded_rows else DataImpact.UNCHANGED
    )
    failure_stage = run.stage
    reconciliation_required = resolved_impact == DataImpact.UNCERTAIN
    terminal_status = (
        PipelineRunStatus.FAILED_NEEDS_RECONCILIATION.value
        if reconciliation_required
        else PipelineRunStatus.CANCELLED.value
    )
    run.last_safe_stage = failure_stage
    run.data_impact = str(resolved_impact)
    run.reconciliation_required = reconciliation_required
    run.reconciliation_reviewed_at = None
    run.error_code = TransferErrorCode.CANCELLED_BY_USER.value
    run.error_summary = "The transfer was cancelled."
    _set_status(run, terminal_status, lease_token=lease_token)
    run.verification_json = json.dumps(
        _failure_facts(
            run,
            TransferErrorCode.CANCELLED_BY_USER.value,
            resolved_impact,
            reconciliation_required=reconciliation_required,
            last_safe_stage=failure_stage,
        ),
        separators=(",", ":"),
    )
    append_event(
        db,
        run,
        "Run cancelled; inspect the destination before retrying."
        if reconciliation_required
        else "Run cancelled.",
        stage="reconcile" if reconciliation_required else "cancelled",
        level="error" if reconciliation_required else "info",
    )
    log_event(
        log,
        "pipeline.run.cancelled",
        outcome="uncertain" if reconciliation_required else "cancelled",
        reference_id=run.id,
        run_id=run.id,
        pipeline_id=run.pipeline_definition_id or "",
        user_id=run.user_id,
        attempt=run.attempt,
        operation="transfer",
        stage="reconcile" if reconciliation_required else "cancelled",
        provider=_run_provider_summary(run),
        duration_ms=_run_duration_ms(run),
        data_impact=str(resolved_impact),
        cause=run.error_summary or "",
    )
    db.commit()


_RECONCILIATION_CODES = frozenset(
    {
        TransferErrorCode.PARTIAL_WRITE.value,
        TransferErrorCode.PUBLISH_UNCERTAIN.value,
        TransferErrorCode.VERIFICATION_FAILED.value,
        TransferErrorCode.WORKER_LOST.value,
    }
)


def _failure_requires_reconciliation(run: PipelineRun, code: str) -> bool:
    if code in _RECONCILIATION_CODES:
        return True
    if code == TransferErrorCode.RUN_TIMEOUT.value:
        return bool(run.loaded_rows or run.stage in {"transfer", "verify"})
    if code == TransferErrorCode.INTERNAL_ERROR.value:
        return bool(run.loaded_rows or run.stage in {"transfer", "verify"})
    # A future connector or deployment may introduce a code this version does
    # not understand. Once the run has reached destination work, the absence
    # of a mapping is itself uncertainty and must not become a safe retry.
    try:
        TransferErrorCode(code)
    except ValueError:
        return bool(run.loaded_rows or run.stage in {"loading", "transfer", "verifying", "verify"})
    return False


def _failure_data_impact(run: PipelineRun, code: str, *, needs_reconciliation: bool) -> DataImpact:
    if needs_reconciliation:
        return DataImpact.UNCERTAIN
    if code == TransferErrorCode.CANCELLED_BY_USER.value:
        return DataImpact.UNCERTAIN if run.loaded_rows else DataImpact.UNCHANGED
    if code in {
        TransferErrorCode.DESTINATION_CONFLICT.value,
        TransferErrorCode.SCHEMA_DRIFT.value,
    }:
        return DataImpact.UNCHANGED
    return DataImpact.UNCHANGED


def _failure_facts(
    run: PipelineRun,
    code: str,
    data_impact: DataImpact,
    *,
    reconciliation_required: bool,
    last_safe_stage: str | None = None,
) -> dict[str, object]:
    try:
        existing = json.loads(run.verification_json or "{}")
    except (TypeError, ValueError):
        existing = {}
    facts = dict(existing) if isinstance(existing, dict) else {}
    facts.update(
        {
            "failure_code": code,
            "data_impact": str(data_impact),
            "reconciliation_required": reconciliation_required,
            "last_safe_stage": last_safe_stage or run.last_safe_stage or run.stage,
            "loaded_rows": int(run.loaded_rows or 0),
        }
    )
    return redact_mapping(facts)


def _run_requires_reconciliation_review(run: PipelineRun) -> bool:
    if run.status == PipelineRunStatus.FAILED_NEEDS_RECONCILIATION.value:
        return True
    if run.reconciliation_required:
        return True
    try:
        facts = json.loads(run.verification_json or "{}")
    except (TypeError, ValueError):
        return run.status in {
            PipelineRunStatus.FAILED.value,
            PipelineRunStatus.CANCELLED.value,
        }
    if not isinstance(facts, dict):
        return run.status in {
            PipelineRunStatus.FAILED.value,
            PipelineRunStatus.CANCELLED.value,
        }
    if str(getattr(run, "data_impact", "") or "").casefold() == DataImpact.UNCERTAIN.value:
        return True
    if str(facts.get("data_impact") or "").casefold() == DataImpact.UNCERTAIN.value:
        return True
    if facts.get("reconciliation_required"):
        return True
    if run.status in {PipelineRunStatus.FAILED.value, PipelineRunStatus.CANCELLED.value}:
        known_safety_keys = {"data_impact", "reconciliation_required", "last_safe_stage"}
        if not known_safety_keys.intersection(facts):
            return True
    return False


def _reconciliation_reviewed(run: PipelineRun) -> bool:
    if run.reconciliation_reviewed_at is not None:
        return True
    try:
        facts = json.loads(run.verification_json or "{}")
    except (TypeError, ValueError):
        facts = {}
    return bool(isinstance(facts, dict) and facts.get("reconciliation_reviewed_at"))


def snapshot_from_definition(pipeline: PipelineDefinition) -> DefinitionSnapshot:
    return DefinitionSnapshot.model_validate_json(
        json.dumps(
            {
                "version": pipeline.definition_version,
                "name": pipeline.name,
                "source_provider": pipeline.source_provider,
                "destination_provider": pipeline.destination_provider,
                "source": json.loads(pipeline.source_locator_json),
                "destination": json.loads(pipeline.destination_locator_json),
                "write_policy": json.loads(pipeline.write_policy_json),
                "source_upload_id": pipeline.source_upload_id,
            }
        )
    )


def _require_lease(run: PipelineRun, lease_token: str) -> None:
    _STATE_MACHINE.require_lease(run, lease_token)


def _refresh_and_require_lease(db: Session, run: PipelineRun, lease_token: str) -> None:
    """Validate ownership against current database state, not the identity map."""

    db.refresh(run)
    _require_lease(run, lease_token)
    if run.lease_expires_at is not None and run.lease_expires_at <= utcnow():
        raise RunConflictError("This worker's run lease has expired.")


def _transition(run: PipelineRun, status: str, *, lease_token: str | None) -> None:
    _STATE_MACHINE.transition(run, status, lease_token=lease_token)


def _set_status(run: PipelineRun, status: str, *, lease_token: str | None) -> None:
    _STATE_MACHINE.set_status(run, status, lease_token=lease_token)


def janitor(db: Session, settings) -> dict[str, int]:
    """Purge expired run events, terminal runs, catalog cache, and spool files."""
    now = utcnow()
    event_cutoff = now - timedelta(days=settings.pipeline_event_retention_days)
    run_cutoff = now - timedelta(days=settings.pipeline_run_retention_days)
    events_deleted = 0
    protected_event_run_ids = {
        run.id
        for run in db.scalars(
            select(PipelineRun).where(PipelineRun.status.in_(TERMINAL_STATUSES))
        ).all()
        if _run_requires_reconciliation_review(run) and not _reconciliation_reviewed(run)
    }
    for event in db.scalars(
        select(PipelineRunEvent).where(PipelineRunEvent.occurred_at < event_cutoff)
    ).all():
        if event.run_id in protected_event_run_ids:
            continue
        db.delete(event)
        events_deleted += 1
    runs_deleted = 0
    for run in db.scalars(
        select(PipelineRun).where(
            PipelineRun.status.in_(TERMINAL_STATUSES),
            PipelineRun.finished_at.is_not(None),
            PipelineRun.finished_at < run_cutoff,
        )
    ).all():
        if _run_requires_reconciliation_review(run) and not _reconciliation_reviewed(run):
            continue
        db.delete(run)
        runs_deleted += 1
    from app.models import PipelineCatalogCache

    cache_deleted = 0
    for row in db.scalars(
        select(PipelineCatalogCache).where(PipelineCatalogCache.expires_at < now)
    ).all():
        db.delete(row)
        cache_deleted += 1
    db.commit()
    spool_deleted = 0
    spool_root = Path(settings.pipeline_spool_root) if settings.pipeline_spool_root else None
    if spool_root and spool_root.is_dir():
        for path in spool_root.iterdir():
            if not path.is_file() and not (path.is_dir() and path.name.endswith(".chunks")):
                continue
            age_days = (now.timestamp() - path.stat().st_mtime) / 86400
            if age_days >= settings.pipeline_event_retention_days:
                if path.is_dir():
                    shutil.rmtree(path, ignore_errors=True)
                else:
                    path.unlink(missing_ok=True)
                spool_deleted += 1
    return {
        "events": events_deleted,
        "runs": runs_deleted,
        "catalog_cache": cache_deleted,
        "spool_files": spool_deleted,
    }
