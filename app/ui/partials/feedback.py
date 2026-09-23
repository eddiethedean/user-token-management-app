"""Reusable persistent feedback surfaces."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Literal

from hedron import Alert, Badge, ClipboardCopy, Inline, Stack, Text, html
from hedron_core import NodeLike

from app.domain.feedback import DataImpact, FeedbackAction, FeedbackOutcome, FeedbackSeverity

_FeedbackTone = Literal["info", "success", "warning", "danger"]


def _tone(outcome: FeedbackOutcome) -> _FeedbackTone:
    tones: dict[FeedbackSeverity, _FeedbackTone] = {
        FeedbackSeverity.INFO: "info",
        FeedbackSeverity.SUCCESS: "success",
        FeedbackSeverity.WARNING: "warning",
        FeedbackSeverity.ERROR: "danger",
    }
    return tones[outcome.severity]


def _impact_label(impact: DataImpact) -> str:
    return {
        DataImpact.NOT_APPLICABLE: "",
        DataImpact.UNCHANGED: "Destination unchanged",
        DataImpact.ROLLED_BACK: "Destination changes rolled back",
        DataImpact.CHANGED: "Destination changed",
        DataImpact.UNCERTAIN: "Destination requires review",
        DataImpact.VERIFIED: "Destination verified",
    }[impact]


def feedback_panel(
    outcome: FeedbackOutcome | None, *, label: str = "Operation feedback"
) -> NodeLike:
    """Render durable, screen-reader-friendly feedback for a page or fragment."""

    if outcome is None:
        return html.div()
    impact = _impact_label(outcome.data_impact)
    reference = (
        Inline(
            Text("Reference", role="caption", effect="subtle"),
            html.code(outcome.reference_id),
            ClipboardCopy(outcome.reference_id, label="Copy reference"),
            gap="xs",
        )
        if outcome.reference_id
        else None
    )
    action = Badge(outcome.action_label, tone="info") if outcome.action_label else None
    steps: list[str] = []
    if outcome.action == FeedbackAction.RECONCILE:
        steps = [
            "Inspect the destination with the provider's native tools.",
            "Compare the destination with the persisted run facts.",
            "Record reconciliation review before starting another run.",
        ]
    elif outcome.action == FeedbackAction.RECONFIGURE:
        steps = ["Test the connection or route again before retrying."]
    return html.div(
        Stack(
            Alert(outcome.message, title=outcome.title, tone=_tone(outcome)),
            field_feedback(outcome.field_errors),
            recovery_steps(steps),
            Inline(
                Text(impact, role="caption", effect="subtle") if impact else None,
                action,
                reference,
                gap="sm",
            ),
            gap="xs",
        ),
        role="region",
        aria={"label": label},
    )


def field_feedback(field_errors: Mapping[str, str]) -> NodeLike:
    """Render safe field-specific validation without echoing submitted values."""

    errors = [(name, message) for name, message in field_errors.items() if message]
    if not errors:
        return html.div()
    return Stack(
        Alert(
            "Review the highlighted fields before continuing.",
            title="Some details need attention",
            tone="warning",
        ),
        *[Text(f"{name}: {message}", role="caption", overflow="wrap") for name, message in errors],
        gap="xs",
    )


def recovery_steps(steps: Sequence[str]) -> NodeLike:
    """Render ordered recovery guidance as persistent text."""

    clean_steps = [step for step in steps if step]
    if not clean_steps:
        return html.div()
    return Stack(
        Text("Next steps", role="label"),
        *[
            html.p(f"{index}. {step}", role="caption")
            for index, step in enumerate(clean_steps, start=1)
        ],
        gap="xs",
    )
