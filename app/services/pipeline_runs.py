"""Durable pipeline run state machine, leases, cancellation, and events."""

from __future__ import annotations

import json
import shutil
from datetime import timedelta
from pathlib import Path

from fastapi import Request
from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from app.connectors.errors import TransferErrorCode
from app.connectors.locators import DefinitionSnapshot
from app.connectors.redaction import redact_mapping, redact_text
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
) -> PipelineRun:
    if pipeline.legacy_unsupported:
        raise ValueError("That saved pipeline uses an unsupported provider and cannot be run.")
    if idempotency_token:
        existing = db.scalar(
            select(PipelineRun).where(
                PipelineRun.user_id == user.id,
                PipelineRun.idempotency_token == idempotency_token,
            )
        )
        if existing is not None:
            return existing
    run = PipelineRun(
        id=new_id(),
        pipeline_definition_id=pipeline.id,
        user_id=user.id,
        definition_snapshot_json=snapshot.model_dump_json(),
        status=PipelineRunStatus.QUEUED.value,
        stage=STAGE_FOR_STATUS[PipelineRunStatus.QUEUED.value],
        attempt=attempt,
        parent_run_id=parent_run_id,
        idempotency_token=idempotency_token,
    )
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
    db.commit()
    db.refresh(run)
    return run


def request_cancel(db: Session, *, user: User, run_id: str) -> PipelineRun:
    run = owned_run(db, user=user, run_id=run_id)
    if run.status in TERMINAL_STATUSES:
        return run
    run.cancel_requested_at = utcnow()
    if run.status == PipelineRunStatus.QUEUED.value:
        _set_status(run, PipelineRunStatus.CANCELLED.value, lease_token=run.lease_token)
        append_event(db, run, "Run cancelled before a worker claimed it.", stage="cancelled")
    else:
        append_event(db, run, "Cancellation requested.", stage=run.stage)
    db.commit()
    db.refresh(run)
    return run


def record_reconciliation_review(db: Session, *, user: User, run_id: str) -> PipelineRun:
    """Record that an operator reviewed an uncertain destination before retrying."""

    run = owned_run(db, user=user, run_id=run_id)
    if run.status != PipelineRunStatus.FAILED_NEEDS_RECONCILIATION.value:
        return run
    try:
        verification = json.loads(run.verification_json or "{}")
    except (TypeError, ValueError):
        verification = {}
    if not isinstance(verification, dict):
        verification = {}
    verification["reconciliation_reviewed_at"] = utcnow().isoformat()
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


def list_runs_for_pipeline(
    db: Session, *, user: User, pipeline_id: str, limit: int = 8
) -> list[PipelineRun]:
    return list(
        db.scalars(
            select(PipelineRun)
            .where(
                PipelineRun.user_id == user.id,
                PipelineRun.pipeline_definition_id == pipeline_id,
            )
            .order_by(PipelineRun.created_at.desc())
            .limit(limit)
        ).all()
    )


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
    last = db.scalar(
        select(PipelineRunEvent.sequence)
        .where(PipelineRunEvent.run_id == run.id)
        .order_by(PipelineRunEvent.sequence.desc())
        .limit(1)
    )
    event = PipelineRunEvent(
        run_id=run.id,
        sequence=(last or 0) + 1,
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
) -> tuple[PipelineRun, str] | None:
    now = utcnow()
    statement = (
        select(PipelineRun)
        .where(
            or_(
                PipelineRun.status == PipelineRunStatus.QUEUED.value,
                and_(
                    PipelineRun.status.in_(WORKER_OWNED_STATUSES),
                    PipelineRun.lease_expires_at.is_not(None),
                    PipelineRun.lease_expires_at <= now,
                ),
            )
        )
        .order_by(PipelineRun.queued_at, PipelineRun.id)
        .limit(1)
    )
    if db.get_bind().dialect.name == "postgresql":
        statement = statement.with_for_update(skip_locked=True)
    run = db.scalar(statement)
    if run is None:
        return None
    if run.status in WORKER_OWNED_STATUSES and run.lease_expires_at is not None:
        if run.stage in {"transfer", "verify"}:
            fail_run(
                db,
                run,
                code=TransferErrorCode.WORKER_LOST,
                summary="The worker lost the lease after destination writes began.",
                retryable=False,
                needs_reconciliation=True,
                lease_token=run.lease_token,
            )
            return None
        run.status = PipelineRunStatus.QUEUED.value
        run.stage = STAGE_FOR_STATUS[PipelineRunStatus.QUEUED.value]
        run.worker_id = None
        run.lease_token = None
        run.lease_expires_at = None
        append_event(db, run, "Stale worker lease recovered; run requeued.", stage="queued")
    if run.cancel_requested_at is not None:
        _set_status(run, PipelineRunStatus.CANCELLED.value, lease_token=None)
        append_event(db, run, "Run cancelled.", stage="cancelled")
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
    _require_lease(run, lease_token)
    now = utcnow()
    run.heartbeat_at = now
    run.lease_expires_at = now + timedelta(seconds=lease_seconds)
    db.commit()


def transition(
    db: Session,
    run: PipelineRun,
    status: str,
    *,
    lease_token: str,
    message: str | None = None,
) -> None:
    _require_lease(run, lease_token)
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
    _require_lease(run, lease_token)
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
    _require_lease(run, lease_token)
    run.source_manifest_json = json.dumps(
        redact_mapping(source_manifest or {}), separators=(",", ":")
    )
    run.destination_manifest_json = json.dumps(
        redact_mapping(destination_manifest or {}), separators=(",", ":")
    )
    run.verification_json = json.dumps(redact_mapping(verification or {}), separators=(",", ":"))
    _set_status(run, PipelineRunStatus.SUCCEEDED.value, lease_token=lease_token)
    append_event(db, run, "Transfer succeeded.", stage="verify")
    db.commit()


def fail_run(
    db: Session,
    run: PipelineRun,
    *,
    code: TransferErrorCode | str,
    summary: str,
    retryable: bool = False,
    needs_reconciliation: bool = False,
    lease_token: str | None = None,
) -> None:
    if lease_token:
        _require_lease(run, lease_token)
    run.error_code = str(code)
    run.error_summary = redact_text(summary)[:500]
    run.retryable = retryable and not needs_reconciliation
    status = (
        PipelineRunStatus.FAILED_NEEDS_RECONCILIATION.value
        if needs_reconciliation
        else PipelineRunStatus.FAILED.value
    )
    _set_status(run, status, lease_token=lease_token)
    append_event(
        db, run, run.error_summary or "The transfer failed.", stage=run.stage, level="error"
    )
    db.commit()


def cancel_claimed_run(db: Session, run: PipelineRun, *, lease_token: str) -> None:
    _require_lease(run, lease_token)
    _set_status(run, PipelineRunStatus.CANCELLED.value, lease_token=lease_token)
    append_event(db, run, "Run cancelled.", stage="cancelled")
    db.commit()


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
    for event in db.scalars(
        select(PipelineRunEvent).where(PipelineRunEvent.occurred_at < event_cutoff)
    ).all():
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
