"""Pure projections for the persisted pipeline-run status surface."""

from __future__ import annotations

import json
from typing import Any, Literal

from hedron import ActionPhase, ActionState, ActionTrace, FlowStep, Metric, OperationIdentity

_RUN_PROGRESS = {
    "queued": 4,
    "validating": 16,
    "extracting": 42,
    "loading": 72,
    "verifying": 92,
    "succeeded": 100,
}

_RUN_STAGE_INDEX = {
    "queued": 0,
    "validating": 0,
    "extracting": 1,
    "loading": 2,
    "verifying": 3,
}

_RUN_STAGE_COPY = {
    "queued": ("Queued", "Waiting for an available worker."),
    "validating": ("Validating", "Checking the route and connection handshakes."),
    "extracting": ("Extracting", "Reading source batches and counting rows."),
    "loading": ("Loading", "Writing batches to the destination."),
    "verifying": ("Verifying", "Comparing persisted results with the source."),
    "succeeded": ("Complete", "Transfer verified and ready for review."),
    "cancelled": ("Cancelled", "The transfer was stopped before completion."),
    "failed": ("Failed", "The worker stopped and recorded a failure."),
    "failed_needs_reconciliation": (
        "Needs review",
        "The worker stopped; reconcile the destination before retrying.",
    ),
}

EVENT_STAGE_LABELS = {
    "queued": "Queue",
    "authenticate": "Validate",
    "inspect": "Extract",
    "transfer": "Load",
    "verify": "Verify",
    "cancelled": "Cancelled",
    "failed": "Failed",
    "reconcile": "Reconcile",
}


ToastTone = Literal["info", "success", "warning", "danger"]


def run_status_toasts(run: Any) -> tuple[str | None, ToastTone]:
    if run.status == "succeeded":
        return "Transfer completed.", "success"
    if run.status in {"failed", "cancelled", "failed_needs_reconciliation"}:
        return "Transfer ended.", "warning"
    return None, "success"


def run_action_phase(status: str) -> str:
    if status in {"queued", "validating", "extracting", "transforming", "loading", "verifying"}:
        return "pending"
    if status == "succeeded":
        return "success"
    if status == "cancelled":
        return "cancelled"
    if status == "failed_needs_reconciliation":
        return "conflict"
    if status == "failed":
        return "error"
    return "idle"


def run_stage_copy(status: str) -> tuple[str, str]:
    return _RUN_STAGE_COPY.get(status, (status.replace("_", " ").title(), ""))


def run_progress(status: str) -> int:
    return _RUN_PROGRESS.get(status, 0)


def run_operation(run: Any, *, revision: int | None = None) -> OperationIdentity:
    """Project a persisted run into Hedron's bounded operation identity."""

    attempt = max(int(run.attempt or 1) - 1, 0)
    return OperationIdentity(
        str(run.id),
        generation=attempt,
        target="#pipeline-run-monitor",
        correlation_id=run.pipeline_definition_id,
        attempt=attempt,
        revision=revision,
    )


def run_action_state(
    run: Any,
    *,
    progress: int | None = None,
    revision: int | None = None,
) -> ActionState:
    status = str(run.status or "idle").lower()
    phase = run_action_phase(status)
    operation = run_operation(run, revision=revision)
    message = run.error_summary if phase in {"error", "conflict"} else run_stage_copy(status)[0]
    return ActionState(
        phase=ActionPhase(phase),
        operation=operation,
        message=(message or None),
        retryable=bool(phase == "error" and run.retryable),
        progress=progress,
        revision=revision,
    )


def run_action_trace(run: Any, events: Any, state: ActionState) -> ActionTrace:
    trace = ActionTrace()
    for event in events:
        trace = trace.append(
            "pending",
            operation=state.operation,
            facts={"stage": event.stage, "sequence": event.sequence, "message": event.message},
        )
    return trace.append(state.phase, operation=state.operation, facts={"status": run.status})


def run_action_metadata(run: Any, events: Any) -> tuple[ActionState, ActionTrace]:
    revision = events[-1].sequence if events else None
    status = str(run.status or "idle").lower()
    state = run_action_state(run, progress=_RUN_PROGRESS.get(status), revision=revision)
    return state, run_action_trace(run, events, state)


def run_flow_statuses(run_status: str) -> tuple[str, str, str, str]:
    if run_status == "succeeded":
        return ("complete", "complete", "complete", "complete")
    current = _RUN_STAGE_INDEX.get(run_status, 0)
    failed = run_status in {"failed", "failed_needs_reconciliation", "cancelled"}
    return tuple(
        "complete"
        if index < current
        else "blocked"
        if failed and index == current
        else "current"
        if index == current
        else "pending"
        for index in range(4)
    )  # type: ignore[return-value]


def run_flow_steps(flow_statuses: tuple[str, str, str, str]) -> tuple[FlowStep, ...]:
    labels = ("Validate", "Extract", "Load", "Verify")
    descriptions = (
        "Check credentials and route settings.",
        "Read source batches.",
        "Write destination batches.",
        "Confirm row counts and checksums.",
    )
    status_text = {
        "complete": "Complete",
        "current": "In progress",
        "blocked": "Stopped",
        "pending": "Waiting",
    }
    return tuple(
        FlowStep(
            label,
            status=step_status,
            description=description,
            status_text=status_text[step_status],
        )
        for label, description, step_status in zip(labels, descriptions, flow_statuses, strict=True)
    )  # type: ignore[return-value]


def destination_count_metric(run: Any) -> Metric:
    """Build a before/after destination count metric from persisted verification data."""

    try:
        verification = json.loads(run.verification_json or "{}")
    except (TypeError, ValueError):
        verification = {}
    before = verification.get("destination_rows_before")
    after = verification.get("destination_rows_after")
    delta = verification.get("destination_row_delta")
    if isinstance(before, int) and isinstance(after, int):
        delta = after - before if not isinstance(delta, int) else delta
        tone = "up" if delta > 0 else "down" if delta < 0 else "neutral"
        return Metric(
            "Destination table",
            f"{before:,} → {after:,} rows",
            delta=f"{delta:+,} rows",
            delta_tone=tone,
        )
    if isinstance(after, int):
        return Metric(
            "Destination table",
            f"{after:,} rows after run",
            delta="Before count unavailable",
            delta_tone="neutral",
        )
    return Metric(
        "Destination table",
        "Count unavailable",
        delta="Provider does not expose counts",
        delta_tone="neutral",
    )
