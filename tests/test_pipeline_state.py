"""Focused tests for the persistence-independent run lifecycle policy."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.models import PipelineRun, PipelineRunStatus
from app.services.pipeline_state import PipelineRunStateMachine, RunConflictError


def test_state_machine_injects_clock_and_clears_terminal_lease() -> None:
    now = datetime(2026, 8, 24, 12, 0, tzinfo=UTC)
    machine = PipelineRunStateMachine(clock=lambda: now)
    run = PipelineRun(
        id="run-1",
        user_id="user-1",
        status=PipelineRunStatus.QUEUED.value,
        stage="queued",
        lease_token="lease-1",
        worker_id="worker-1",
    )

    machine.transition(run, PipelineRunStatus.VALIDATING.value, lease_token="lease-1")
    assert run.stage == "authenticate"
    assert run.lease_token == "lease-1"

    machine.set_status(run, PipelineRunStatus.SUCCEEDED.value, lease_token="lease-1")
    assert run.finished_at == now
    assert run.worker_id is None
    assert run.lease_token is None


def test_state_machine_rejects_stale_leases_and_illegal_transitions() -> None:
    machine = PipelineRunStateMachine(clock=lambda: datetime.now(UTC))
    run = PipelineRun(
        id="run-1",
        user_id="user-1",
        status=PipelineRunStatus.QUEUED.value,
        stage="queued",
        lease_token="lease-1",
    )

    with pytest.raises(RunConflictError, match="no longer holds"):
        machine.require_lease(run, "stale-lease")
    with pytest.raises(RunConflictError, match="Cannot move"):
        machine.transition(run, PipelineRunStatus.SUCCEEDED.value, lease_token="lease-1")
