"""Find the next durable work deadline after a notification or recovery pass."""

from __future__ import annotations

from sqlalchemy import case, func, select, union_all

from app.infrastructure.runtime import ExecutionRuntime
from app.models import DatabricksComputeAttempt, DatabricksPublication, PipelineRun, User, utcnow
from app.services.pipeline_state import TERMINAL_STATUSES, WORKER_OWNED_STATUSES


def next_background_work_delay(runtime: ExecutionRuntime) -> float | None:
    """Return a timer for pending work, or None when all work queues are idle."""

    now = utcnow()
    run_due = case(
        (PipelineRun.status == "queued", func.coalesce(PipelineRun.next_attempt_at, now)),
        else_=PipelineRun.lease_expires_at,
    )
    run_deadline = (
        select(func.min(run_due).label("due"))
        .join(User, User.id == PipelineRun.user_id)
        .where(
            User.status == "active",
            (PipelineRun.status == "queued")
            | (
                PipelineRun.status.in_(WORKER_OWNED_STATUSES)
                & PipelineRun.lease_expires_at.is_not(None)
            ),
        )
    )
    compute_due = case(
        (
            DatabricksComputeAttempt.lease_expires_at > DatabricksComputeAttempt.next_poll_at,
            DatabricksComputeAttempt.lease_expires_at,
        ),
        else_=DatabricksComputeAttempt.next_poll_at,
    )
    compute_deadline = select(func.min(compute_due).label("due")).where(
        DatabricksComputeAttempt.status == "starting",
        DatabricksComputeAttempt.active_key.is_not(None),
    )
    recovery_due = func.coalesce(DatabricksPublication.recovery_expires_at, now)
    publication_due = case(
        (
            PipelineRun.status.not_in(TERMINAL_STATUSES)
            & (PipelineRun.lease_expires_at > recovery_due),
            PipelineRun.lease_expires_at,
        ),
        else_=recovery_due,
    )
    publication_deadline = (
        select(func.min(publication_due).label("due"))
        .select_from(DatabricksPublication)
        .join(PipelineRun, PipelineRun.id == DatabricksPublication.run_id)
        .where(
            DatabricksPublication.state.in_(
                ("preparing", "staging", "cleanup_pending", "publishing", "uncertain")
            )
        )
    )
    deadlines = union_all(run_deadline, compute_deadline, publication_deadline).subquery()
    with runtime.context(), runtime.sessions() as db:
        deadline = db.scalar(select(func.min(deadlines.c.due)))
    if deadline is None:
        return None
    # Due work may be row-locked by another process. Avoid a busy loop while
    # retaining bounded retries; its committed state change will also wake us.
    return max(0.5, (deadline - utcnow()).total_seconds())
