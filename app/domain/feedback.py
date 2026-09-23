"""Framework-neutral feedback and troubleshooting values.

The application produces one outcome for an operation.  The browser and the
diagnostic logger intentionally project that outcome differently: the browser
gets safe, actionable copy while logs retain structured troubleshooting facts.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class FeedbackSeverity(StrEnum):
    INFO = "info"
    SUCCESS = "success"
    WARNING = "warning"
    ERROR = "error"


class FeedbackAction(StrEnum):
    NONE = "none"
    RETRY = "retry"
    REAUTHENTICATE = "reauthenticate"
    RECONFIGURE = "reconfigure"
    WAIT = "wait"
    RECONCILE = "reconcile"
    CONTACT_ADMIN = "contact_admin"


class DataImpact(StrEnum):
    NOT_APPLICABLE = "not_applicable"
    UNCHANGED = "unchanged"
    ROLLED_BACK = "rolled_back"
    CHANGED = "changed"
    UNCERTAIN = "uncertain"
    VERIFIED = "verified"


class ReferenceKind(StrEnum):
    REQUEST = "request"
    CONNECTION_TEST = "connection_test"
    RUN = "run"


class FeedbackCode(StrEnum):
    AUTH_INVALID = "auth_invalid"
    AUTH_RATE_LIMITED = "auth_rate_limited"
    AUTH_ACCOUNT_UNAVAILABLE = "auth_account_unavailable"
    AUTH_LINK_INVALID = "auth_link_invalid"
    AUTH_TRUSTED_IDENTITY_FAILED = "auth_trusted_identity_failed"
    PIPELINE_PREFLIGHT_INVALID = "pipeline_preflight_invalid"
    CONNECTION_INVALID = "connection_invalid"
    CONNECTION_NOT_CONFIGURED = "connection_not_configured"
    CONNECTION_SAVED_UNTESTED = "connection_saved_untested"
    CONNECTION_TEST_INCOMPLETE = "connection_test_incomplete"
    CONNECTION_TEST_SUCCEEDED = "connection_test_succeeded"
    CONNECTION_AUTHENTICATION_FAILED = "connection_authentication_failed"
    CONNECTION_PERMISSION_DENIED = "connection_permission_denied"
    CONNECTION_ENDPOINT_BLOCKED = "connection_endpoint_blocked"
    CONNECTION_TLS_FAILED = "connection_tls_failed"
    CONNECTION_TIMEOUT = "connection_timeout"
    CONNECTION_PROVIDER_UNAVAILABLE = "connection_provider_unavailable"
    CONNECTION_UNSUPPORTED = "connection_unsupported"
    REQUEST_UNAVAILABLE = "request_unavailable"
    INTERNAL_ERROR = "internal_error"


@dataclass(frozen=True)
class FeedbackOutcome:
    """Safe, actionable result of a user-visible operation."""

    code: str
    severity: FeedbackSeverity
    title: str
    message: str
    action: FeedbackAction = FeedbackAction.NONE
    action_label: str = ""
    retryable: bool = False
    data_impact: DataImpact = DataImpact.NOT_APPLICABLE
    field_errors: dict[str, str] = field(default_factory=dict)
    reference_kind: ReferenceKind = ReferenceKind.REQUEST
    reference_id: str = ""

    @property
    def has_action(self) -> bool:
        return self.action != FeedbackAction.NONE

    def with_reference(
        self, reference_id: str, *, kind: ReferenceKind | None = None
    ) -> FeedbackOutcome:
        return FeedbackOutcome(
            code=self.code,
            severity=self.severity,
            title=self.title,
            message=self.message,
            action=self.action,
            action_label=self.action_label,
            retryable=self.retryable,
            data_impact=self.data_impact,
            field_errors=dict(self.field_errors),
            reference_kind=kind or self.reference_kind,
            reference_id=reference_id,
        )
