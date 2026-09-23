"""Pure projections for user-facing operational feedback."""

from __future__ import annotations

import json
from typing import Any

from app.application.feedback import connection_failure, pipeline_failure
from app.connectors.errors import TransferErrorCode
from app.domain.feedback import (
    DataImpact,
    FeedbackAction,
    FeedbackCode,
    FeedbackOutcome,
    FeedbackSeverity,
    ReferenceKind,
)


def connection_outcome(secret: Any) -> FeedbackOutcome | None:
    """Project persisted connection readiness into a safe persistent outcome."""

    if secret is None:
        return None
    status = str(secret.validation_status or "untested")
    reference = str(secret.validation_reference or "")
    code = str(secret.validation_code or "")
    if status == "connected":
        return FeedbackOutcome(
            code=code or "connection_test_succeeded",
            severity=FeedbackSeverity.SUCCESS,
            title="Connection is ready.",
            message=secret.validation_message or "The connection passed its health check.",
            data_impact=DataImpact.NOT_APPLICABLE,
            reference_kind=ReferenceKind.CONNECTION_TEST,
            reference_id=reference,
        )
    if status == "failed":
        return connection_failure(
            code or TransferErrorCode.INTERNAL_ERROR,
            reference_id=reference,
            message=secret.validation_message,
        )
    if getattr(secret, "validated_at", None) is not None:
        return FeedbackOutcome(
            code=code or FeedbackCode.CONNECTION_TEST_INCOMPLETE,
            severity=FeedbackSeverity.WARNING,
            title="Connection needs one more setup step.",
            message=secret.validation_message
            or "The provider responded, but the connection is not ready for browsing or transfers.",
            action=FeedbackAction.RECONFIGURE,
            action_label="Update connection",
            data_impact=DataImpact.NOT_APPLICABLE,
            reference_kind=ReferenceKind.CONNECTION_TEST,
            reference_id=reference,
        )
    return FeedbackOutcome(
        code=code or "connection_saved_untested",
        severity=FeedbackSeverity.WARNING,
        title="Connection is saved but not tested.",
        message="The connection check is incomplete. Test the connection before browsing objects or running a transfer.",
        data_impact=DataImpact.NOT_APPLICABLE,
        reference_kind=ReferenceKind.CONNECTION_TEST,
        reference_id=reference,
    )


def run_data_impact(run: Any) -> DataImpact:
    """Derive a conservative data-impact label from persisted run facts."""

    persisted_impact = getattr(run, "data_impact", None)
    if persisted_impact:
        try:
            return DataImpact(str(persisted_impact))
        except ValueError:
            pass
    facts_valid = True
    try:
        facts = json.loads(run.verification_json or "{}")
    except (TypeError, ValueError):
        facts = {}
        facts_valid = False
    if not isinstance(facts, dict):
        facts = {}
        facts_valid = False
    if isinstance(facts, dict):
        persisted_impact = facts.get("data_impact")
        try:
            return DataImpact(str(persisted_impact))
        except ValueError:
            pass
    status = str(run.status or "")
    if status == "succeeded":
        return DataImpact.CHANGED
    if status in {"failed", "failed_needs_reconciliation", "cancelled"}:
        safety_keys = {"data_impact", "reconciliation_required", "last_safe_stage"}
        if not facts_valid or not safety_keys.intersection(facts):
            return DataImpact.UNCERTAIN
    if status == "cancelled":
        return DataImpact.UNCERTAIN if int(run.loaded_rows or 0) else DataImpact.UNCHANGED
    if status == "failed_needs_reconciliation":
        return DataImpact.UNCERTAIN
    if str(run.stage or "") in {"transfer", "verify"} and int(run.loaded_rows or 0):
        return DataImpact.UNCERTAIN
    return DataImpact.UNCHANGED


def run_outcome(run: Any) -> FeedbackOutcome | None:
    status = str(run.status or "")
    if status == "succeeded":
        facts = _run_verification(run)
        exact = facts.get("verification_level", "") == "exact"
        return FeedbackOutcome(
            code="pipeline_succeeded",
            severity=FeedbackSeverity.SUCCESS,
            title="Transfer completed and was verified." if exact else "Transfer completed.",
            message=(
                "The persisted destination results match the verification available from the selected providers."
                if exact
                else "The provider recorded the transfer successfully; exact destination comparison was unavailable."
            ),
            data_impact=DataImpact.VERIFIED if exact else DataImpact.CHANGED,
            reference_kind=ReferenceKind.RUN,
            reference_id=str(run.id),
        )
    if status == "cancelled":
        impact = run_data_impact(run)
        return FeedbackOutcome(
            code="pipeline_cancelled",
            severity=FeedbackSeverity.WARNING,
            title="Transfer cancelled.",
            message=(
                "The transfer stopped before destination writes were recorded."
                if impact == DataImpact.UNCHANGED
                else "The transfer stopped and the staged destination changes were rolled back."
                if impact == DataImpact.ROLLED_BACK
                else "The transfer stopped after destination work began. Review the destination before retrying."
            ),
            data_impact=impact,
            reference_kind=ReferenceKind.RUN,
            reference_id=str(run.id),
        )
    if status not in {"failed", "failed_needs_reconciliation"}:
        return None
    return pipeline_failure(
        code=getattr(run, "error_code", None) or TransferErrorCode.INTERNAL_ERROR,
        summary=getattr(run, "error_summary", None)
        or "The transfer ended without a recoverable summary.",
        reference_id=str(run.id),
        data_impact=run_data_impact(run),
        retryable=bool(run.retryable),
    )


def verification_summary(run: Any) -> str:
    """Explain what verification facts are available without exposing raw JSON."""

    verification = _run_verification(run)
    if not verification:
        return "Verification details are unavailable."
    if verification.get("verification_level") != "exact":
        return "The provider recorded completion, but does not expose an exact destination row-count comparison."
    if isinstance(verification.get("destination_row_delta"), int):
        return f"Destination row change: {verification['destination_row_delta']:+,}."
    if isinstance(verification.get("loaded_rows"), int):
        return "Destination write completed; the provider did not expose an exact row delta."
    return "The destination completed without an exact provider row-count comparison."


def _run_verification(run: Any) -> dict[str, Any]:
    try:
        verification = json.loads(run.verification_json or "{}")
    except (TypeError, ValueError):
        return {}
    return verification if isinstance(verification, dict) else {}
