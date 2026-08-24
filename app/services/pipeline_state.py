"""Pure pipeline-run lifecycle policy, separate from database orchestration."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from app.models import PipelineRun, PipelineRunStatus, utcnow

ACTIVE_STATUSES = {
    PipelineRunStatus.QUEUED.value,
    PipelineRunStatus.VALIDATING.value,
    PipelineRunStatus.EXTRACTING.value,
    PipelineRunStatus.LOADING.value,
    PipelineRunStatus.VERIFYING.value,
}

WORKER_OWNED_STATUSES = {
    PipelineRunStatus.VALIDATING.value,
    PipelineRunStatus.EXTRACTING.value,
    PipelineRunStatus.LOADING.value,
    PipelineRunStatus.VERIFYING.value,
}

TERMINAL_STATUSES = {
    PipelineRunStatus.SUCCEEDED.value,
    PipelineRunStatus.FAILED.value,
    PipelineRunStatus.CANCELLED.value,
    PipelineRunStatus.FAILED_NEEDS_RECONCILIATION.value,
}

ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {
    PipelineRunStatus.QUEUED.value: frozenset(
        {
            PipelineRunStatus.VALIDATING.value,
            PipelineRunStatus.CANCELLED.value,
        }
    ),
    PipelineRunStatus.VALIDATING.value: frozenset(
        {
            PipelineRunStatus.EXTRACTING.value,
            PipelineRunStatus.FAILED.value,
            PipelineRunStatus.CANCELLED.value,
        }
    ),
    PipelineRunStatus.EXTRACTING.value: frozenset(
        {
            PipelineRunStatus.LOADING.value,
            PipelineRunStatus.FAILED.value,
            PipelineRunStatus.CANCELLED.value,
        }
    ),
    PipelineRunStatus.LOADING.value: frozenset(
        {
            PipelineRunStatus.VERIFYING.value,
            PipelineRunStatus.FAILED.value,
            PipelineRunStatus.FAILED_NEEDS_RECONCILIATION.value,
            PipelineRunStatus.CANCELLED.value,
        }
    ),
    PipelineRunStatus.VERIFYING.value: frozenset(
        {
            PipelineRunStatus.SUCCEEDED.value,
            PipelineRunStatus.FAILED.value,
            PipelineRunStatus.CANCELLED.value,
        }
    ),
}

STAGE_FOR_STATUS = {
    PipelineRunStatus.QUEUED.value: "queued",
    PipelineRunStatus.VALIDATING.value: "authenticate",
    PipelineRunStatus.EXTRACTING.value: "inspect",
    PipelineRunStatus.LOADING.value: "transfer",
    PipelineRunStatus.VERIFYING.value: "verify",
    PipelineRunStatus.SUCCEEDED.value: "verify",
    PipelineRunStatus.FAILED.value: "failed",
    PipelineRunStatus.CANCELLED.value: "cancelled",
    PipelineRunStatus.FAILED_NEEDS_RECONCILIATION.value: "reconcile",
}


class RunConflictError(ValueError):
    """Raised when a worker tries to mutate a run it no longer owns."""


class PipelineRunStateMachine:
    """Apply lifecycle rules without knowing how runs are persisted."""

    def __init__(self, *, clock: Callable[[], datetime] = utcnow) -> None:
        self._clock = clock

    def require_lease(self, run: PipelineRun, lease_token: str) -> None:
        if run.lease_token != lease_token:
            raise RunConflictError("This worker no longer holds the run lease.")

    def transition(self, run: PipelineRun, status: str, *, lease_token: str | None) -> None:
        allowed = ALLOWED_TRANSITIONS.get(run.status, frozenset())
        if status not in allowed:
            raise RunConflictError(f"Cannot move a {run.status} run to {status}.")
        self.set_status(run, status, lease_token=lease_token)

    def set_status(self, run: PipelineRun, status: str, *, lease_token: str | None) -> None:
        run.status = status
        run.stage = STAGE_FOR_STATUS[status]
        now = self._clock()
        run.updated_at = now
        if status in TERMINAL_STATUSES:
            run.finished_at = now
            run.worker_id = None
            run.lease_token = None
            run.lease_expires_at = None
        elif lease_token:
            run.lease_token = lease_token
