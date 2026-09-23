from __future__ import annotations

import json
import logging
from types import SimpleNamespace

from app.application.feedback import (
    account_failure,
    auth_failure,
    connection_failure,
    pipeline_failure,
    preflight_failure,
)
from app.connectors.errors import ConnectorError, TransferErrorCode
from app.connectors.redaction import redact_mapping, redact_text
from app.domain.feedback import DataImpact, FeedbackAction, FeedbackCode
from app.logging_config import SafeFormatter
from app.ui.presenters.feedback import connection_outcome, run_data_impact, run_outcome


def test_auth_feedback_remains_generic_for_invalid_credentials() -> None:
    outcome = auth_failure(reason="invalid", reference_id="req-123")

    assert outcome.code == FeedbackCode.AUTH_INVALID
    assert outcome.action == FeedbackAction.RETRY
    assert "credentials" in outcome.message
    assert outcome.reference_id == "req-123"


def test_connection_feedback_maps_provider_timeout_to_retry() -> None:
    outcome = connection_failure(
        TransferErrorCode.CONNECTION_TIMEOUT,
        reference_id="req-123",
        message="provider detail must not become the user contract",
    )

    assert outcome.code == FeedbackCode.CONNECTION_TIMEOUT
    assert outcome.action == FeedbackAction.RETRY
    assert outcome.retryable is True
    assert "provider detail" not in outcome.message


def test_pipeline_feedback_requires_reconciliation_after_uncertain_publish() -> None:
    outcome = pipeline_failure(
        ConnectorError(
            TransferErrorCode.PUBLISH_UNCERTAIN,
            "destination completion was not confirmed",
        ),
        reference_id="run-123",
    )

    assert outcome.action == FeedbackAction.RECONCILE
    assert outcome.data_impact == DataImpact.UNCERTAIN
    assert outcome.reference_id == "run-123"


def test_pipeline_feedback_covers_every_transfer_code_with_safe_action() -> None:
    for code in TransferErrorCode:
        outcome = pipeline_failure(code=code, reference_id="run-123")

        assert outcome.code
        assert outcome.title
        assert outcome.message
        assert outcome.reference_id == "run-123"
        if code in {
            TransferErrorCode.PARTIAL_WRITE,
            TransferErrorCode.PUBLISH_UNCERTAIN,
            TransferErrorCode.WORKER_LOST,
            TransferErrorCode.VERIFICATION_FAILED,
        }:
            assert outcome.action == FeedbackAction.RECONCILE
            assert outcome.data_impact == DataImpact.UNCERTAIN


def test_account_link_feedback_is_safe_and_referenceable() -> None:
    outcome = account_failure(reason="link_expired", reference_id="req-123")

    assert outcome.code == FeedbackCode.AUTH_LINK_INVALID
    assert outcome.action == FeedbackAction.RETRY
    assert "token" not in outcome.message.casefold()
    assert outcome.reference_id == "req-123"


def test_account_password_feedback_is_specific_and_safe() -> None:
    outcome = account_failure(reason="password_mismatch", reference_id="req-123")

    assert outcome.code == FeedbackCode.AUTH_INVALID
    assert outcome.action == FeedbackAction.RETRY
    assert "same password" in outcome.message


def test_connection_internal_feedback_has_support_guidance() -> None:
    outcome = connection_failure("internal_error", reference_id="req-123")

    assert outcome.code == FeedbackCode.CONNECTION_INVALID
    assert outcome.action == FeedbackAction.CONTACT_ADMIN
    assert outcome.reference_id == "req-123"


def test_unknown_connection_feedback_fails_closed_to_administrator() -> None:
    outcome = connection_failure("future_connection_failure", reference_id="req-unknown")

    assert outcome.action == FeedbackAction.CONTACT_ADMIN
    assert outcome.retryable is False
    assert outcome.reference_id == "req-unknown"
    assert "administrator" in outcome.message.casefold()


def test_persisted_connection_feedback_code_keeps_specific_copy() -> None:
    outcome = connection_failure(
        "connection_authentication_failed",
        reference_id="test-123",
    )

    assert outcome.code == "connection_authentication_failed"
    assert outcome.action == FeedbackAction.RECONFIGURE
    assert "rejected" in outcome.title.casefold()
    assert outcome.reference_id == "test-123"


def test_preflight_feedback_explains_route_correction_before_enqueue() -> None:
    outcome = preflight_failure(
        reason="That saved pipeline's destination writer is not enabled.",
        reference_id="req-456",
    )

    assert outcome.code == FeedbackCode.PIPELINE_PREFLIGHT_INVALID
    assert outcome.action == FeedbackAction.RECONFIGURE
    assert "destination" in outcome.message.casefold()
    assert outcome.reference_id == "req-456"


def test_safe_formatter_redacts_sensitive_message_and_emits_json() -> None:
    record = logging.LogRecord(
        "test",
        logging.ERROR,
        __file__,
        1,
        "request failed password=super-secret",
        (),
        None,
    )
    record.request_id = "req-123"
    record.event = "auth.login.failed"
    record.error_code = "auth_invalid"

    rendered = SafeFormatter(json_mode=True).format(record)
    payload = json.loads(rendered)

    assert payload["request_id"] == "req-123"
    assert payload["event"] == "auth.login.failed"
    assert "super-secret" not in rendered
    assert "[redacted]" in rendered


def test_safe_formatter_recursively_redacts_structured_diagnostic_values() -> None:
    record = logging.LogRecord("test", logging.ERROR, __file__, 1, "event", (), None)
    record.event = "pipeline.run.failed"
    record.cause = {
        "password": "CANARY_PASSWORD",
        "nested": [{"private_key": "CANARY_KEY"}],
    }

    rendered = SafeFormatter(json_mode=True).format(record)

    assert "CANARY_PASSWORD" not in rendered
    assert "CANARY_KEY" not in rendered
    assert json.loads(rendered)["cause"] == {
        "password": "[redacted]",
        "nested": [{"private_key": "[redacted]"}],
    }


def test_log_event_redacts_nested_diagnostic_values_before_formatting(caplog) -> None:
    from app.logging_config import log_event

    logger = logging.getLogger("structured-feedback-test")
    with caplog.at_level(logging.INFO):
        log_event(
            logger,
            "pipeline.run.failed",
            outcome="failed",
            cause={"password": "CANARY_PASSWORD", "nested": [{"token": "CANARY_TOKEN"}]},
        )

    rendered = SafeFormatter(json_mode=True).format(caplog.records[-1])

    assert "CANARY_PASSWORD" not in rendered
    assert "CANARY_TOKEN" not in rendered


def test_text_formatter_includes_outcome() -> None:
    record = logging.LogRecord("test", logging.WARNING, __file__, 1, "event", (), None)
    record.request_id = "req-1"
    record.event = "pipeline.run.failed"
    record.outcome = "uncertain"

    rendered = SafeFormatter(json_mode=False).format(record)

    assert "outcome=uncertain" in rendered


def test_redaction_covers_api_keys_dsn_variants_and_nested_sequences() -> None:
    rendered = redact_text(
        "api_key=CANARY_API access_key=CANARY_ACCESS "
        "mongodb://user:CANARY_MONGO@host/db "
        "mysql://user:CANARY_MYSQL@host/db "
        "https://user:CANARY_URL@host/?api_key=CANARY_QUERY"
    )
    for canary in (
        "CANARY_API",
        "CANARY_ACCESS",
        "CANARY_MONGO",
        "CANARY_MYSQL",
        "CANARY_URL",
        "CANARY_QUERY",
    ):
        assert canary not in rendered
    mapped = redact_mapping({"api_key": "CANARY_API", "nested": [{"private_key": "CANARY_KEY"}]})
    assert mapped["api_key"] == "[redacted]"
    assert mapped["nested"][0]["private_key"] == "[redacted]"


def test_redaction_removes_basic_auth_and_header_values() -> None:
    rendered = redact_text(
        "Authorization: Basic dXNlcjpwYXNz\n"
        "Proxy-Authorization: Basic cHJveHk6cGFzcw==\n"
        "X-Api-Key: CANARY_KEY"
    )

    assert "dXNlcjpwYXNz" not in rendered
    assert "cHJveHk6cGFzcw==" not in rendered
    assert "CANARY_KEY" not in rendered
    assert rendered.count("[redacted]") == 3


def test_redaction_covers_compound_secret_keys_and_quoted_values() -> None:
    rendered = redact_text(
        'access_token=ACCESS refresh_token=REFRESH password="alpha beta" Cookie: session=COOKIE'
    )
    mapped = redact_mapping(
        {
            "access_token": "ACCESS",
            "refresh_token": "REFRESH",
            "session_cookie": "COOKIE",
            "api_token": "API_TOKEN",
        }
    )

    for canary in ("ACCESS", "REFRESH", "alpha beta", "COOKIE"):
        assert canary not in rendered
    assert all(value == "[redacted]" for value in mapped.values())


def test_redaction_covers_compound_secret_text_values() -> None:
    rendered = redact_text(
        'secret_token=CANARY_SECRET private_token="CANARY_PRIVATE" '
        "api_secret: CANARY_API credential_token=CANARY_CREDENTIAL "
        '"secret_token": "CANARY_JSON"'
    )

    for canary in (
        "CANARY_SECRET",
        "CANARY_PRIVATE",
        "CANARY_API",
        "CANARY_CREDENTIAL",
        "CANARY_JSON",
    ):
        assert canary not in rendered


def test_run_data_impact_prefers_persisted_safety_fact() -> None:
    run = SimpleNamespace(
        status="failed",
        stage="loading",
        loaded_rows=0,
        verification_json=json.dumps({"data_impact": "uncertain"}),
    )

    assert run_data_impact(run) == DataImpact.UNCERTAIN


def test_run_data_impact_prefers_authoritative_column_over_legacy_json() -> None:
    run = SimpleNamespace(
        status="failed",
        stage="transfer",
        loaded_rows=0,
        data_impact=DataImpact.UNCERTAIN,
        verification_json=json.dumps({"data_impact": "unchanged"}),
    )

    assert run_data_impact(run) == DataImpact.UNCERTAIN


def test_terminal_run_without_safety_facts_is_uncertain() -> None:
    run = SimpleNamespace(
        status="failed",
        stage="failed",
        loaded_rows=0,
        data_impact=None,
        verification_json="{}",
    )

    assert run_data_impact(run) == DataImpact.UNCERTAIN


def test_unknown_pipeline_feedback_fails_closed_to_reconciliation() -> None:
    outcome = pipeline_failure(
        code="future_provider_failure",
        reference_id="run-unknown",
        data_impact=DataImpact.UNCHANGED,
    )

    assert outcome.action.name == "CONTACT_ADMIN"
    assert outcome.data_impact == DataImpact.UNCERTAIN


def test_timeout_feedback_is_retryable_when_no_durable_run_decision_exists() -> None:
    outcome = pipeline_failure(code=TransferErrorCode.RUN_TIMEOUT, reference_id="run-timeout")

    assert outcome.action.name == "RETRY"
    assert outcome.retryable is True


def test_connection_health_incomplete_after_a_test_shows_provider_guidance() -> None:
    outcome = connection_outcome(
        SimpleNamespace(
            validation_status="untested",
            validation_code="connection_test_incomplete",
            validation_reference="connection-123",
            validation_message="Provide a default dataset RID to verify access.",
            validated_at="2026-09-22T00:00:00",
        )
    )

    assert outcome is not None
    assert outcome.code == "connection_test_incomplete"
    assert "default dataset RID" in outcome.message
    assert outcome.action.name == "RECONFIGURE"


def test_log_event_rejects_untrusted_correlation_formats(caplog) -> None:
    logger = logging.getLogger("feedback-test")

    from app.logging_config import log_event

    with caplog.at_level(logging.INFO):
        log_event(
            logger,
            "test.invalid.fields",
            outcome="failed",
            reference_id="not safe\nvalue",
            provider_correlation_id="provider-secret/with spaces",
            sqlstate="1234",
        )

    record = caplog.records[-1]
    assert record.reference_id == "[invalid]"
    assert record.provider_correlation_id == "[invalid]"
    assert record.sqlstate == "[invalid]"


def test_text_formatter_keeps_the_same_diagnostic_fields_as_json() -> None:
    record = logging.LogRecord("test", logging.WARNING, __file__, 1, "event", (), None)
    record.request_id = "req-1"
    record.event = "connection.test.failed"
    record.outcome = "failed"
    record.error_code = "connection_timeout"
    record.reference_id = "ref-1"
    record.provider = "postgres"
    record.operation = "test_connection"
    record.http_status = 504
    record.duration_ms = 123
    record.retry_after_seconds = 5

    rendered = SafeFormatter(json_mode=False).format(record)

    for field in (
        "error_code",
        "reference_id",
        "provider",
        "operation",
        "http_status",
        "duration_ms",
    ):
        assert f"{field}=" in rendered


def test_diagnostic_contract_supplies_required_connection_duration_and_level(caplog) -> None:
    logger = logging.getLogger("feedback-contract-test")

    with caplog.at_level(logging.INFO):
        from app.logging_config import log_event

        log_event(
            logger,
            "connection.test.failed",
            outcome="failed",
            error_code="internal_error",
            reference_id="ref-1",
            provider="postgres",
            operation="test_connection",
            retryable=False,
        )
        log_event(
            logger,
            "connection.test.completed",
            outcome="incomplete",
            reference_id="ref-2",
            provider="mss",
            operation="test_connection",
        )

    failed, incomplete = caplog.records[-2:]
    assert failed.duration_ms == 0
    assert incomplete.levelno == logging.INFO


def test_local_manifest_completion_does_not_claim_exact_verification() -> None:
    run = SimpleNamespace(
        id="run-123",
        status="succeeded",
        verification_json=json.dumps({"verification_level": "local_manifest"}),
        data_impact=None,
    )

    outcome = run_outcome(run)

    assert outcome is not None
    assert outcome.title == "Transfer completed."
    assert outcome.data_impact == DataImpact.CHANGED
    assert "exact destination comparison" in outcome.message


def test_missing_verification_level_does_not_claim_exact_verification() -> None:
    run = SimpleNamespace(
        id="run-legacy",
        status="succeeded",
        verification_json="{}",
        data_impact=None,
    )

    outcome = run_outcome(run)

    assert outcome is not None
    assert outcome.data_impact == DataImpact.CHANGED
    assert outcome.title == "Transfer completed."
