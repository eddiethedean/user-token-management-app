"""Application-level mappings from operational failures to safe outcomes."""

from __future__ import annotations

import logging

from app.connectors.errors import RETRYABLE_CODES, ConnectorError, TransferErrorCode
from app.domain.feedback import (
    DataImpact,
    FeedbackAction,
    FeedbackCode,
    FeedbackOutcome,
    FeedbackSeverity,
    ReferenceKind,
)
from app.logging_config import log_event

log = logging.getLogger(__name__)


_PIPELINE_FAILURE_COPY = {
    TransferErrorCode.CREDENTIALS_MISSING: (
        "Connection credentials are missing.",
        "Configure and test the connection before running this transfer.",
        FeedbackAction.RECONFIGURE,
        DataImpact.UNCHANGED,
    ),
    TransferErrorCode.CREDENTIALS_STALE: (
        "Connection credentials need to be refreshed.",
        "Replace and test the connection before retrying this transfer.",
        FeedbackAction.RECONFIGURE,
        DataImpact.UNCHANGED,
    ),
    TransferErrorCode.AUTHENTICATION_FAILED: (
        "The provider rejected the connection credentials.",
        "Replace and test the connection before retrying this transfer.",
        FeedbackAction.RECONFIGURE,
        DataImpact.UNCHANGED,
    ),
    TransferErrorCode.PERMISSION_DENIED: (
        "The provider denied this operation.",
        "Ask the provider administrator to grant the required access, then test the connection again.",
        FeedbackAction.CONTACT_ADMIN,
        DataImpact.UNCHANGED,
    ),
    TransferErrorCode.ENDPOINT_BLOCKED: (
        "The configured endpoint is not allowed.",
        "Review the endpoint policy or contact the application administrator.",
        FeedbackAction.CONTACT_ADMIN,
        DataImpact.UNCHANGED,
    ),
    TransferErrorCode.TLS_FAILED: (
        "A secure provider connection could not be established.",
        "Review the provider certificate and configured CA policy before retrying.",
        FeedbackAction.RECONFIGURE,
        DataImpact.UNCHANGED,
    ),
    TransferErrorCode.CONNECTION_TIMEOUT: (
        "The provider did not respond in time.",
        "Retry when the provider is available. Do not retry if destination publication is uncertain.",
        FeedbackAction.RETRY,
        DataImpact.UNCHANGED,
    ),
    TransferErrorCode.PROVIDER_UNAVAILABLE: (
        "The provider is unavailable.",
        "Retry later or ask the provider administrator to check availability.",
        FeedbackAction.RETRY,
        DataImpact.UNCHANGED,
    ),
    TransferErrorCode.RATE_LIMITED: (
        "The provider is temporarily rate-limiting requests.",
        "Wait and retry after the provider limit clears.",
        FeedbackAction.WAIT,
        DataImpact.UNCHANGED,
    ),
    TransferErrorCode.SOURCE_NOT_FOUND: (
        "The source object could not be found.",
        "Review the source selection and save the route again.",
        FeedbackAction.RECONFIGURE,
        DataImpact.UNCHANGED,
    ),
    TransferErrorCode.DESTINATION_NOT_FOUND: (
        "The destination object could not be found.",
        "Review the destination selection and save the route again.",
        FeedbackAction.RECONFIGURE,
        DataImpact.UNCHANGED,
    ),
    TransferErrorCode.SCHEMA_DRIFT: (
        "The source or destination schema changed.",
        "Refresh the route preview, resolve the schema differences, and retry only after review.",
        FeedbackAction.RECONFIGURE,
        DataImpact.UNCHANGED,
    ),
    TransferErrorCode.UNSUPPORTED_TYPE: (
        "The selected route contains an unsupported data type.",
        "Choose a compatible source or destination, or ask an administrator about supported types.",
        FeedbackAction.RECONFIGURE,
        DataImpact.UNCHANGED,
    ),
    TransferErrorCode.SOURCE_LIMIT_EXCEEDED: (
        "The source exceeded an approved transfer limit.",
        "Reduce the source scope or ask an administrator to review the approved limit.",
        FeedbackAction.RECONFIGURE,
        DataImpact.UNCHANGED,
    ),
    TransferErrorCode.SPOOL_LIMIT_EXCEEDED: (
        "The transfer staging limit was exceeded.",
        "Reduce the transfer scope or ask an administrator to review staging capacity.",
        FeedbackAction.CONTACT_ADMIN,
        DataImpact.UNCHANGED,
    ),
    TransferErrorCode.RUN_TIMEOUT: (
        "The transfer exceeded its time limit.",
        "Review the source scope and provider availability before retrying.",
        FeedbackAction.RETRY,
        DataImpact.UNCHANGED,
    ),
    TransferErrorCode.DESTINATION_CONFLICT: (
        "The destination rejected the requested write policy.",
        "Review the destination and write mode before retrying.",
        FeedbackAction.RECONFIGURE,
        DataImpact.UNCHANGED,
    ),
    TransferErrorCode.VERIFICATION_FAILED: (
        "The transfer completed without a verified result.",
        "Inspect the destination and run facts before deciding whether to retry.",
        FeedbackAction.RECONCILE,
        DataImpact.UNCERTAIN,
    ),
    TransferErrorCode.CANCELLED_BY_USER: (
        "The transfer was cancelled.",
        "Start a new run when the source and destination are ready.",
        FeedbackAction.NONE,
        DataImpact.UNCHANGED,
    ),
    TransferErrorCode.PARTIAL_WRITE: (
        "The destination may contain partial results.",
        "Inspect the destination before retrying this transfer.",
        FeedbackAction.RECONCILE,
        DataImpact.UNCERTAIN,
    ),
    TransferErrorCode.PUBLISH_UNCERTAIN: (
        "The destination publication could not be confirmed.",
        "Inspect the destination before retrying this transfer.",
        FeedbackAction.RECONCILE,
        DataImpact.UNCERTAIN,
    ),
    TransferErrorCode.WORKER_LOST: (
        "The transfer worker stopped unexpectedly.",
        "Inspect the destination before retrying this transfer.",
        FeedbackAction.RECONCILE,
        DataImpact.UNCERTAIN,
    ),
    TransferErrorCode.INTERNAL_ERROR: (
        "The transfer could not be completed.",
        "Retry only if the destination is known to be unchanged; otherwise contact your administrator.",
        FeedbackAction.CONTACT_ADMIN,
        DataImpact.UNCERTAIN,
    ),
}


def _unknown_mapping(*, operation: str, reference_id: str) -> None:
    log_event(
        log,
        "feedback.mapping.unknown",
        outcome="failed",
        error_code=str(FeedbackCode.INTERNAL_ERROR),
        reference_id=reference_id,
        operation=operation,
    )


def request_failure(*, reference_id: str = "") -> FeedbackOutcome:
    return FeedbackOutcome(
        code=FeedbackCode.REQUEST_UNAVAILABLE,
        severity=FeedbackSeverity.ERROR,
        title="We could not complete that request.",
        message="Try again. If the problem continues, share the reference with your administrator.",
        action=FeedbackAction.RETRY,
        action_label="Try again",
        retryable=True,
        reference_id=reference_id,
    )


def auth_failure(*, reason: str = "invalid", reference_id: str = "") -> FeedbackOutcome:
    if reason == "rate_limited":
        return FeedbackOutcome(
            code=FeedbackCode.AUTH_RATE_LIMITED,
            severity=FeedbackSeverity.WARNING,
            title="Too many sign-in attempts.",
            message="Wait a moment before trying again.",
            action=FeedbackAction.WAIT,
            action_label="Wait and retry",
            retryable=True,
            reference_id=reference_id,
        )
    if reason == "trusted_identity":
        return FeedbackOutcome(
            code=FeedbackCode.AUTH_TRUSTED_IDENTITY_FAILED,
            severity=FeedbackSeverity.ERROR,
            title="Organizational sign-in could not be completed.",
            message="Return through the approved sign-in entry point or contact your administrator.",
            action=FeedbackAction.CONTACT_ADMIN,
            action_label="Contact administrator",
            reference_id=reference_id,
        )
    if reason == "locked":
        return FeedbackOutcome(
            code=FeedbackCode.AUTH_RATE_LIMITED,
            severity=FeedbackSeverity.WARNING,
            title="Sign-in is temporarily locked.",
            message=(
                "Sign-in is temporarily locked after repeated unsuccessful attempts. "
                "Wait 15 minutes before trying again, or use password recovery."
            ),
            action=FeedbackAction.WAIT,
            action_label="Wait and try again",
            retryable=True,
            reference_id=reference_id,
        )
    return FeedbackOutcome(
        code=FeedbackCode.AUTH_INVALID,
        severity=FeedbackSeverity.ERROR,
        title="Sign-in was not completed.",
        message=(
            "Unable to sign in with those credentials. Check your email and password, then try "
            "again. If you recently made several attempts, wait before trying again."
        ),
        action=FeedbackAction.RETRY,
        action_label="Try again",
        retryable=True,
        reference_id=reference_id,
    )


def account_failure(*, reason: str, reference_id: str = "") -> FeedbackOutcome:
    """Return safe copy for enrollment and recovery failures."""

    if reason == "password_mismatch":
        return FeedbackOutcome(
            code=FeedbackCode.AUTH_INVALID,
            severity=FeedbackSeverity.ERROR,
            title="Passwords do not match.",
            message="Passwords do not match. Enter the same password in both fields and try again.",
            action=FeedbackAction.RETRY,
            action_label="Try again",
            retryable=True,
            reference_id=reference_id,
        )
    if reason == "password_policy":
        return FeedbackOutcome(
            code=FeedbackCode.AUTH_INVALID,
            severity=FeedbackSeverity.ERROR,
            title="Password does not meet the requirements.",
            message="Use 15–128 characters and avoid common passwords or words from your email address.",
            action=FeedbackAction.RETRY,
            action_label="Try again",
            retryable=True,
            reference_id=reference_id,
        )
    if reason in {"link_invalid", "link_expired", "link_used"}:
        return FeedbackOutcome(
            code=FeedbackCode.AUTH_LINK_INVALID,
            severity=FeedbackSeverity.ERROR,
            title="This link is no longer valid.",
            message="This link is invalid or expired. Request a new link or start the access request again.",
            action=FeedbackAction.RETRY,
            action_label="Request a new link",
            reference_id=reference_id,
        )
    if reason == "rate_limited":
        return auth_failure(reason="rate_limited", reference_id=reference_id)
    if reason == "service_unavailable":
        return FeedbackOutcome(
            code=FeedbackCode.AUTH_ACCOUNT_UNAVAILABLE,
            severity=FeedbackSeverity.ERROR,
            title="Account access is temporarily unavailable.",
            message="Try again later. If the problem continues, share the reference with your administrator.",
            action=FeedbackAction.RETRY,
            action_label="Try again",
            retryable=True,
            reference_id=reference_id,
        )
    if reason == "domain_ineligible":
        return FeedbackOutcome(
            code=FeedbackCode.AUTH_INVALID,
            severity=FeedbackSeverity.ERROR,
            title="This address is not eligible.",
            message="Use an approved government email domain and try again.",
            action=FeedbackAction.RETRY,
            action_label="Try again",
            retryable=True,
            reference_id=reference_id,
        )
    return FeedbackOutcome(
        code=FeedbackCode.AUTH_ACCOUNT_UNAVAILABLE,
        severity=FeedbackSeverity.ERROR,
        title="The account request could not be completed.",
        message="Check the submitted information and try again. If the problem continues, contact an administrator.",
        action=FeedbackAction.CONTACT_ADMIN,
        action_label="Contact administrator",
        reference_id=reference_id,
    )


def connection_failure(
    code: str | TransferErrorCode,
    *,
    reference_id: str = "",
    message: str = "",
) -> FeedbackOutcome:
    if str(code) == "connection_not_configured":
        return FeedbackOutcome(
            code=FeedbackCode.CONNECTION_NOT_CONFIGURED,
            severity=FeedbackSeverity.WARNING,
            title="Connection is not configured.",
            message="Save the connection details before testing the connection.",
            action=FeedbackAction.RECONFIGURE,
            action_label="Configure connection",
            reference_kind=ReferenceKind.CONNECTION_TEST,
            reference_id=reference_id,
        )
    persisted_mapping = {
        FeedbackCode.INTERNAL_ERROR: (
            FeedbackCode.CONNECTION_INVALID,
            "The connection test could not be completed.",
            "Try again. If the problem continues, share the reference with your administrator.",
            FeedbackAction.CONTACT_ADMIN,
            False,
        ),
        FeedbackCode.CONNECTION_INVALID: (
            FeedbackCode.CONNECTION_INVALID,
            "The connection test could not be completed.",
            "Try again. If the problem continues, share the reference with your administrator.",
            FeedbackAction.CONTACT_ADMIN,
            False,
        ),
        FeedbackCode.CONNECTION_AUTHENTICATION_FAILED: (
            FeedbackCode.CONNECTION_AUTHENTICATION_FAILED,
            "Connection credentials were rejected.",
            "Replace the credentials, then test the connection again.",
            FeedbackAction.RECONFIGURE,
            False,
        ),
        FeedbackCode.CONNECTION_PERMISSION_DENIED: (
            FeedbackCode.CONNECTION_PERMISSION_DENIED,
            "The connection is not authorized for this operation.",
            "Ask the provider administrator to grant the required access, then test again.",
            FeedbackAction.CONTACT_ADMIN,
            False,
        ),
        FeedbackCode.CONNECTION_ENDPOINT_BLOCKED: (
            FeedbackCode.CONNECTION_ENDPOINT_BLOCKED,
            "The connection endpoint is not allowed.",
            "Check the endpoint policy or contact the application administrator.",
            FeedbackAction.CONTACT_ADMIN,
            False,
        ),
        FeedbackCode.CONNECTION_TLS_FAILED: (
            FeedbackCode.CONNECTION_TLS_FAILED,
            "A secure connection could not be established.",
            "Check the provider certificate and configured CA policy, then try again.",
            FeedbackAction.RECONFIGURE,
            False,
        ),
        FeedbackCode.CONNECTION_TIMEOUT: (
            FeedbackCode.CONNECTION_TIMEOUT,
            "The provider did not respond in time.",
            "Try again. If this continues, ask the provider administrator to check availability.",
            FeedbackAction.RETRY,
            True,
        ),
        FeedbackCode.CONNECTION_PROVIDER_UNAVAILABLE: (
            FeedbackCode.CONNECTION_PROVIDER_UNAVAILABLE,
            "The provider is unavailable.",
            "Try again later or contact the provider administrator.",
            FeedbackAction.RETRY,
            True,
        ),
        FeedbackCode.CONNECTION_UNSUPPORTED: (
            FeedbackCode.CONNECTION_UNSUPPORTED,
            "This connection setup is not supported.",
            "Review the provider and connection fields, or contact an administrator.",
            FeedbackAction.RECONFIGURE,
            False,
        ),
    }
    try:
        persisted_code = FeedbackCode(str(code))
    except ValueError:
        persisted_code = None
    persisted = persisted_mapping.get(persisted_code) if persisted_code else None
    if persisted is not None:
        feedback_code, title, safe_message, action, retryable = persisted
        return FeedbackOutcome(
            code=feedback_code,
            severity=FeedbackSeverity.WARNING if retryable else FeedbackSeverity.ERROR,
            title=title,
            message=safe_message,
            action=action,
            action_label={
                FeedbackAction.RETRY: "Test again",
                FeedbackAction.RECONFIGURE: "Review connection",
                FeedbackAction.CONTACT_ADMIN: "Contact administrator",
            }.get(action, ""),
            retryable=retryable,
            reference_kind=ReferenceKind.CONNECTION_TEST,
            reference_id=reference_id,
        )
    mapping = {
        TransferErrorCode.CREDENTIALS_MISSING: (
            FeedbackCode.CONNECTION_INVALID,
            "Connection credentials are incomplete.",
            "Review the required connection fields, then test the connection again.",
            FeedbackAction.RECONFIGURE,
            False,
        ),
        TransferErrorCode.CREDENTIALS_STALE: (
            FeedbackCode.CONNECTION_AUTHENTICATION_FAILED,
            "Connection credentials need to be refreshed.",
            "Replace the credentials, then test the connection again.",
            FeedbackAction.RECONFIGURE,
            False,
        ),
        TransferErrorCode.AUTHENTICATION_FAILED: (
            FeedbackCode.CONNECTION_AUTHENTICATION_FAILED,
            "Connection credentials were rejected.",
            "Replace the credentials, then test the connection again.",
            FeedbackAction.RECONFIGURE,
            False,
        ),
        TransferErrorCode.PERMISSION_DENIED: (
            FeedbackCode.CONNECTION_PERMISSION_DENIED,
            "The connection is not authorized for this operation.",
            "Ask the provider administrator to grant the required access, then test again.",
            FeedbackAction.CONTACT_ADMIN,
            False,
        ),
        TransferErrorCode.ENDPOINT_BLOCKED: (
            FeedbackCode.CONNECTION_ENDPOINT_BLOCKED,
            "The connection endpoint is not allowed.",
            "Check the endpoint policy or contact the application administrator.",
            FeedbackAction.CONTACT_ADMIN,
            False,
        ),
        TransferErrorCode.TLS_FAILED: (
            FeedbackCode.CONNECTION_TLS_FAILED,
            "A secure connection could not be established.",
            "Check the provider certificate and configured CA policy, then try again.",
            FeedbackAction.RECONFIGURE,
            False,
        ),
        TransferErrorCode.CONNECTION_TIMEOUT: (
            FeedbackCode.CONNECTION_TIMEOUT,
            "The provider did not respond in time.",
            "Try again. If this continues, ask the provider administrator to check availability.",
            FeedbackAction.RETRY,
            True,
        ),
        TransferErrorCode.PROVIDER_UNAVAILABLE: (
            FeedbackCode.CONNECTION_PROVIDER_UNAVAILABLE,
            "The provider is unavailable.",
            "Try again later or contact the provider administrator.",
            FeedbackAction.RETRY,
            True,
        ),
        TransferErrorCode.UNSUPPORTED_TYPE: (
            FeedbackCode.CONNECTION_UNSUPPORTED,
            "This connection setup is not supported.",
            "Review the provider and connection fields, or contact an administrator.",
            FeedbackAction.RECONFIGURE,
            False,
        ),
        TransferErrorCode.INTERNAL_ERROR: (
            FeedbackCode.CONNECTION_INVALID,
            "The connection test could not be completed.",
            "Try again. If the problem continues, share the reference with your administrator.",
            FeedbackAction.CONTACT_ADMIN,
            False,
        ),
    }
    try:
        key = TransferErrorCode(str(code))
    except ValueError:
        key = None
    selected = mapping.get(key) if key is not None else None
    if selected is None:
        _unknown_mapping(operation="connection_test", reference_id=reference_id)
        return FeedbackOutcome(
            code=FeedbackCode.CONNECTION_INVALID,
            severity=FeedbackSeverity.ERROR,
            title="The connection test failed.",
            message=(
                "The connection test could not be classified safely. Do not replace working "
                "credentials based only on this message; share the reference with your administrator."
            ),
            action=FeedbackAction.CONTACT_ADMIN,
            action_label="Contact administrator",
            retryable=False,
            reference_kind=ReferenceKind.CONNECTION_TEST,
            reference_id=reference_id,
        )
    feedback_code, title, safe_message, action, retryable = selected
    return FeedbackOutcome(
        code=feedback_code,
        severity=FeedbackSeverity.WARNING if retryable else FeedbackSeverity.ERROR,
        title=title,
        message=safe_message,
        action=action,
        action_label={
            FeedbackAction.RETRY: "Test again",
            FeedbackAction.RECONFIGURE: "Review connection",
            FeedbackAction.CONTACT_ADMIN: "Contact administrator",
        }.get(action, ""),
        retryable=retryable,
        reference_kind=ReferenceKind.CONNECTION_TEST,
        reference_id=reference_id,
    )


def preflight_failure(*, reason: str = "", reference_id: str = "") -> FeedbackOutcome:
    """Project authoritative route checks into safe pre-enqueue guidance."""

    normalized = reason.casefold()
    action = FeedbackAction.RECONFIGURE
    action_label = "Review route"
    if "uncertain destination" in normalized or "reconciliation review" in normalized:
        message = "The previous transfer may have changed the destination. Inspect the destination and record reconciliation review before starting another run."
        action = FeedbackAction.RECONCILE
        action_label = "Review destination"
    elif "cannot store the csv decimal without rounding" in normalized:
        message = (
            "The destination table's numeric columns cannot hold all CSV decimal places. "
            "Choose a table with enough precision and scale, or update the destination column definitions."
        )
    elif "cannot store the source decimal without rounding" in normalized:
        message = (
            "The destination table's numeric columns cannot hold all source decimal places. "
            "Choose a table with enough precision and scale, or update the destination column definitions."
        )
    elif "cannot safely store the selected decimal cast" in normalized:
        message = (
            "The destination column cannot safely store the selected decimal values at its current precision. "
            "Choose an unconstrained numeric or text column, or select a different cast type."
        )
    elif "writer is not enabled" in normalized:
        message = "The selected destination is not enabled for writes. Choose another destination or contact an administrator."
    elif "unsupported transfer route" in normalized or "unsupported provider" in normalized:
        message = (
            "The selected source and destination route is not supported. Choose a compatible route."
        )
    elif "pipeline is required" in normalized or "pipeline_id is required" in normalized:
        message = "Select or save a pipeline before starting a transfer."
    else:
        message = "The route is not ready to run. Review the connection, source, destination, and write policy."
    outcome = FeedbackOutcome(
        code=FeedbackCode.PIPELINE_PREFLIGHT_INVALID,
        severity=FeedbackSeverity.ERROR,
        title="The transfer was not started.",
        message=message,
        action=action,
        action_label=action_label,
        reference_id=reference_id,
    )
    log_event(
        log,
        "pipeline.preflight.rejected",
        outcome="rejected",
        error_code=str(outcome.code),
        reference_id=reference_id,
        operation="enqueue",
    )
    return outcome


def pipeline_failure(
    error: ConnectorError | None = None,
    *,
    code: str | TransferErrorCode | None = None,
    summary: str = "",
    reference_id: str = "",
    data_impact: DataImpact = DataImpact.NOT_APPLICABLE,
    retryable: bool | None = None,
) -> FeedbackOutcome:
    selected_code = str(code or (error.code if error else TransferErrorCode.INTERNAL_ERROR))
    try:
        transfer_code = TransferErrorCode(selected_code)
    except ValueError:
        transfer_code = None
    selected = _PIPELINE_FAILURE_COPY.get(transfer_code) if transfer_code else None
    if selected is None:
        _unknown_mapping(operation="pipeline_run", reference_id=reference_id)
        return FeedbackOutcome(
            code=FeedbackCode.INTERNAL_ERROR,
            severity=FeedbackSeverity.ERROR,
            title="The transfer could not be completed.",
            message=(
                "The transfer ended without a recognized recovery path. Do not retry until "
                "the destination has been reviewed; share the run reference with your administrator."
            ),
            action=FeedbackAction.CONTACT_ADMIN,
            action_label="Contact administrator",
            retryable=False,
            data_impact=DataImpact.UNCERTAIN,
            reference_kind=ReferenceKind.RUN,
            reference_id=reference_id,
        )
    title, safe_message, action, default_impact = selected
    resolved_impact = data_impact if data_impact != DataImpact.NOT_APPLICABLE else default_impact
    if resolved_impact == DataImpact.UNCERTAIN and action != FeedbackAction.RECONCILE:
        title = "Transfer stopped; destination review required."
        safe_message = "Destination work may have occurred. Inspect the destination and record reconciliation review before retrying."
        action = FeedbackAction.RECONCILE
    retryable_value = (
        bool(error.retryable)
        if error is not None
        else (transfer_code in RETRYABLE_CODES if retryable is None else retryable)
    )
    if action == FeedbackAction.RETRY and not retryable_value:
        action = FeedbackAction.CONTACT_ADMIN
    if action == FeedbackAction.RETRY:
        severity = FeedbackSeverity.WARNING
        action_label = "Retry run"
    elif action == FeedbackAction.RECONCILE:
        severity = FeedbackSeverity.ERROR
        action_label = "Review destination"
    elif action == FeedbackAction.RECONFIGURE:
        severity = FeedbackSeverity.ERROR
        action_label = "Review route"
    elif action == FeedbackAction.WAIT:
        severity = FeedbackSeverity.WARNING
        action_label = "Wait and retry"
    elif action == FeedbackAction.CONTACT_ADMIN:
        severity = FeedbackSeverity.ERROR
        action_label = "Contact administrator"
    else:
        severity = FeedbackSeverity.INFO
        action_label = ""
    return FeedbackOutcome(
        code=selected_code,
        severity=severity,
        title=title,
        message=safe_message,
        action=action,
        action_label=action_label,
        retryable=retryable_value and action == FeedbackAction.RETRY,
        data_impact=resolved_impact,
        reference_kind=ReferenceKind.RUN,
        reference_id=reference_id,
    )
