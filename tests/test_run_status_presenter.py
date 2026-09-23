"""Unit tests for pipeline run-status presentation projections."""

from __future__ import annotations

from types import SimpleNamespace

from app.ui.presenters.run_status import (
    run_action_phase,
    run_action_state,
    run_flow_statuses,
    run_progress,
    run_stage_copy,
)


def test_run_status_projection_covers_terminal_and_active_states() -> None:
    assert run_action_phase("loading") == "pending"
    assert run_action_phase("succeeded") == "success"
    assert run_action_phase("failed_needs_reconciliation") == "conflict"
    assert run_progress("verifying") == 92
    assert run_stage_copy("succeeded")[0] == "Complete"


def test_run_flow_marks_failed_stage_as_blocked() -> None:
    assert run_flow_statuses("loading") == ("complete", "complete", "current", "pending")
    assert run_flow_statuses("failed") == ("blocked", "pending", "pending", "pending")
    assert run_flow_statuses("failed", "inspect") == (
        "complete",
        "blocked",
        "pending",
        "pending",
    )
    assert run_flow_statuses("failed_needs_reconciliation", "transfer") == (
        "complete",
        "complete",
        "blocked",
        "pending",
    )
    assert run_flow_statuses("cancelled", "verify") == (
        "complete",
        "complete",
        "complete",
        "blocked",
    )
    assert run_flow_statuses("succeeded") == ("complete", "complete", "complete", "complete")


def test_run_action_state_uses_persisted_error_and_progress() -> None:
    run = SimpleNamespace(
        id="run-1",
        attempt=2,
        pipeline_definition_id="pipeline-1",
        status="failed",
        error_summary="Destination rejected the write.",
        retryable=True,
    )

    state = run_action_state(run, progress=72, revision=4)

    assert state.phase == "error"
    assert state.message == "Destination rejected the write."
    assert state.retryable is True
    assert state.progress == 72
    assert state.revision == 4
