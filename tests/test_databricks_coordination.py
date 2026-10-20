from __future__ import annotations

from collections.abc import Callable
from datetime import timedelta

import pytest
from sqlalchemy import select

from app.config import get_settings
from app.connectors.errors import ConnectorError, TransferErrorCode
from app.database import SessionLocal
from app.domain.locators import DatabricksTableLocator
from app.models import (
    DatabricksComputeAttempt,
    DatabricksPublication,
    PipelineRun,
    PipelineRunStatus,
    User,
    UserSecret,
    utcnow,
)
from app.services.databricks_compute import (
    cancel_compute_observation,
    claim_due_attempt,
    create_compute_attempt,
    observe_attempt,
    process_due_compute_attempt,
    record_start_submission,
    request_compute_start,
    resume_compute_observation,
)
from app.services.databricks_publication import (
    process_pending_publication_recovery,
    record_publication_event,
)
from app.services.pipeline_runs import claim_run, defer_run_for_databricks_compute
from app.services.secrets import decrypt_user_credentials_for_run, store_user_credentials


def _credentials(generation: str = "revision-1") -> dict[str, str]:
    return {
        "workspace_url": "https://dbc-12345678.cloud.databricks.com",
        "compute_kind": "warehouse",
        "compute_id": "warehouse-1",
        "connection_generation": generation,
    }


def _locator() -> DatabricksTableLocator:
    return DatabricksTableLocator(
        workspace_id="a" * 64,
        catalog="main",
        schema="analytics",
        table="orders",
        connection_generation="revision-1",
    )


def _run(user_id: str, *, run_id: str, lease_token: str) -> PipelineRun:
    return PipelineRun(
        id=run_id,
        user_id=user_id,
        definition_snapshot_json="{}",
        status=PipelineRunStatus.LOADING.value,
        stage="transfer",
        lease_token=lease_token,
        lease_expires_at=utcnow() + timedelta(minutes=5),
    )


def test_compute_attempt_deduplicates_shared_compute_and_releases_when_ready(
    access_app, make_user
) -> None:
    owner = make_user("dbx.owner@example.gov")
    other = make_user("dbx.other@example.gov")
    settings = get_settings()

    with SessionLocal() as db:
        first, created = create_compute_attempt(
            db, user=owner, credentials=_credentials(), settings=settings
        )
        assert created is True
        db.commit()
        first_id = first.id

        second, created = create_compute_attempt(
            db, user=other, credentials=_credentials("other-principal-revision"), settings=settings
        )
        assert created is False
        assert second.id == first_id
        assert second.user_id == owner.id

        record_start_submission(
            db,
            attempt_id=first_id,
            result={"outcome": "already_running", "state": "RUNNING"},
            settings=settings,
        )
        saved = db.get(DatabricksComputeAttempt, first_id)
        assert saved is not None
        assert saved.status == "ready"

        next_attempt, created = create_compute_attempt(
            db, user=other, credentials=_credentials("other-principal-revision"), settings=settings
        )
        assert created is True
        assert next_attempt.id != first_id


def test_compute_attempt_poll_lease_and_deadline_are_durable(access_app, make_user) -> None:
    owner = make_user("dbx.poll@example.gov")
    settings = get_settings()
    with SessionLocal() as db:
        attempt, _ = create_compute_attempt(
            db, user=owner, credentials=_credentials(), settings=settings
        )
        attempt.next_poll_at = utcnow() - timedelta(seconds=1)
        attempt.deadline_at = utcnow() + timedelta(minutes=1)
        db.commit()
        attempt_id = attempt.id

        claimed = claim_due_attempt(db)
        assert claimed is not None
        claimed_id, lease_token = claimed
        assert claimed_id == attempt_id
        assert observe_attempt(
            db,
            attempt_id=attempt_id,
            lease_token=lease_token,
            state="STARTING",
            settings=settings,
        )
        refreshed = db.get(DatabricksComputeAttempt, attempt_id)
        assert refreshed is not None
        assert refreshed.status == "starting"
        assert refreshed.lease_token is None
        assert refreshed.next_poll_at > utcnow()

        refreshed.next_poll_at = utcnow() - timedelta(seconds=1)
        db.commit()
        claimed_again = claim_due_attempt(db)
        assert claimed_again is not None
        _, second_token = claimed_again
        assert observe_attempt(
            db,
            attempt_id=attempt_id,
            lease_token=second_token,
            state="RUNNING",
            settings=settings,
        )
        refreshed = db.get(DatabricksComputeAttempt, attempt_id)
        assert refreshed is not None
        assert refreshed.status == "ready"
        assert refreshed.active_key is None


def test_compute_observation_times_out_at_its_deadline_and_releases_the_fence(
    access_app, make_user
) -> None:
    owner = make_user("dbx.deadline@example.gov")
    settings = get_settings()
    with SessionLocal() as db:
        attempt, _ = create_compute_attempt(
            db, user=owner, credentials=_credentials(), settings=settings
        )
        attempt.next_poll_at = utcnow() - timedelta(seconds=1)
        attempt.deadline_at = utcnow() - timedelta(seconds=1)
        attempt_id = attempt.id
        deadline = attempt.deadline_at
        db.commit()

        claimed = claim_due_attempt(db)
        assert claimed is not None
        claimed_id, lease_token = claimed
        assert claimed_id == attempt_id
        assert observe_attempt(
            db,
            attempt_id=attempt_id,
            lease_token=lease_token,
            state="STARTING",
            settings=settings,
            now=deadline + timedelta(microseconds=1),
        )
        saved = db.get(DatabricksComputeAttempt, attempt_id)
        assert saved is not None and saved.status == "timed_out"
        assert saved.active_key is None
        assert saved.error_code == "compute_start_timeout"


def test_failed_start_marks_only_matching_saved_connection_failed(access_app, make_user) -> None:
    owner = make_user("dbx.start-failed@example.gov")
    settings = _enabled_databricks_settings()
    with SessionLocal() as db:
        credentials = _saved_databricks_credentials(db, settings, owner)
        attempt, _ = create_compute_attempt(
            db, user=owner, credentials=credentials, settings=settings
        )
        attempt_id = attempt.id
        record_error = ConnectorError(
            TransferErrorCode.PROVIDER_UNAVAILABLE,
            "The Databricks start request failed.",
            retryable=False,
        )
        from app.services.databricks_compute import record_start_failure

        record_start_failure(db, attempt_id=attempt_id, error=record_error)
        saved = db.get(DatabricksComputeAttempt, attempt_id)
        secret = db.scalar(
            select(UserSecret).where(
                UserSecret.user_id == owner.id, UserSecret.provider == "databricks"
            )
        )
        assert saved is not None and saved.status == "failed"
        assert saved.active_key is None
        assert secret is not None and secret.runtime_status == "FAILED"


def test_compute_poll_without_status_reader_fails_attempt_safely(access_app, make_user) -> None:
    owner = make_user("dbx.no-status-reader@example.gov")
    settings = _enabled_databricks_settings()
    with SessionLocal() as db:
        credentials = _saved_databricks_credentials(db, settings, owner)
        attempt, _ = create_compute_attempt(
            db, user=owner, credentials=credentials, settings=settings
        )
        attempt.next_poll_at = utcnow() - timedelta(seconds=1)
        attempt_id = attempt.id
        db.commit()

    class StartOnlyConnector:
        def start_compute(self, _credentials):
            pytest.fail("a readiness poll must not submit another start")

    assert process_due_compute_attempt(
        settings=settings,
        session_factory=lambda: SessionLocal(),
        connector_resolver=lambda _provider: StartOnlyConnector(),
    )
    with SessionLocal() as db:
        saved = db.get(DatabricksComputeAttempt, attempt_id)
        assert saved is not None and saved.status == "starting"
        assert saved.error_code == str(TransferErrorCode.INTERNAL_ERROR)
        assert saved.next_poll_at > utcnow()


def test_compute_observation_cancellation_is_owner_scoped_and_fences_pollers(
    access_app, make_user
) -> None:
    owner = make_user("dbx.cancel.owner@example.gov")
    other = make_user("dbx.cancel.other@example.gov")
    with SessionLocal() as db:
        attempt, created = create_compute_attempt(
            db, user=owner, credentials=_credentials(), settings=get_settings()
        )
        assert created
        attempt.next_poll_at = utcnow() - timedelta(seconds=1)
        db.commit()
        attempt_id = attempt.id
        claimed = claim_due_attempt(db)
        assert claimed is not None
        claimed_id, lease_token = claimed
        assert claimed_id == attempt_id

        assert not cancel_compute_observation(db, user_id=other.id, attempt_id=attempt_id)
        assert cancel_compute_observation(db, user_id=owner.id, attempt_id=attempt_id)
        assert not observe_attempt(
            db,
            attempt_id=attempt_id,
            lease_token=lease_token,
            state="RUNNING",
            settings=get_settings(),
        )

        saved = db.get(DatabricksComputeAttempt, attempt_id)
        assert saved is not None
        assert saved.status == "cancelled"
        assert saved.active_key is not None
        assert saved.lease_token is None
        assert saved.error_code == "startup_observation_cancelled"
        assert claim_due_attempt(db) is None

        duplicate, created = create_compute_attempt(
            db, user=owner, credentials=_credentials(), settings=get_settings()
        )
        assert not created
        assert duplicate.id == attempt_id
        assert duplicate.status == "cancelled"

        assert resume_compute_observation(db, user_id=owner.id, attempt_id=attempt_id) == "resumed"
        resumed_claim = claim_due_attempt(db)
        assert resumed_claim is not None
        resumed_id, resumed_token = resumed_claim
        assert resumed_id == attempt_id
        assert observe_attempt(
            db,
            attempt_id=attempt_id,
            lease_token=resumed_token,
            state="RUNNING",
            settings=get_settings(),
        )
        saved = db.get(DatabricksComputeAttempt, attempt_id)
        assert saved is not None
        assert saved.status == "ready"


def test_resuming_cancelled_compute_observation_after_deadline_times_out(
    access_app, make_user
) -> None:
    owner = make_user("dbx.resume-timeout@example.gov")
    with SessionLocal() as db:
        attempt, _ = create_compute_attempt(
            db, user=owner, credentials=_credentials(), settings=get_settings()
        )
        attempt_id = attempt.id
        assert cancel_compute_observation(db, user_id=owner.id, attempt_id=attempt_id)
        attempt.deadline_at = utcnow() - timedelta(seconds=1)
        db.commit()

        assert (
            resume_compute_observation(db, user_id=owner.id, attempt_id=attempt_id) == "timed_out"
        )
        saved = db.get(DatabricksComputeAttempt, attempt_id)
        assert saved is not None and saved.status == "timed_out"
        assert saved.active_key is None
        assert saved.error_code == "compute_start_timeout"


def test_opted_in_pipeline_run_is_deferred_until_startup_deadline(access_app, make_user) -> None:
    owner = make_user("dbx.deferred-run@example.gov")
    settings = get_settings()
    with SessionLocal() as db:
        attempt, _ = create_compute_attempt(
            db, user=owner, credentials=_credentials(), settings=settings
        )
        run = PipelineRun(
            user_id=owner.id,
            definition_snapshot_json="{}",
            status=PipelineRunStatus.VALIDATING.value,
            stage="authenticate",
            worker_id="dbx-worker",
            lease_token="dbx-lease",
            lease_expires_at=utcnow() + timedelta(minutes=1),
            databricks_compute_attempt_id=attempt.id,
            databricks_compute_start_deadline_at=attempt.deadline_at,
        )
        db.add(run)
        db.commit()
        run_id = run.id

        assert defer_run_for_databricks_compute(db, run, lease_token="dbx-lease", settings=settings)
        db.refresh(run)
        assert run.status == PipelineRunStatus.QUEUED.value
        assert run.stage == "waiting_for_compute"
        assert run.next_attempt_at is not None and run.next_attempt_at > utcnow()
        assert claim_run(db, worker_id="early-worker", lease_seconds=60) is None

        run.next_attempt_at = utcnow() - timedelta(seconds=1)
        db.commit()
        claimed = claim_run(db, worker_id="ready-worker", lease_seconds=60, run_id=run_id)
        assert claimed is not None
        reclaimed, lease_token = claimed
        assert reclaimed.status == PipelineRunStatus.VALIDATING.value
        assert reclaimed.stage == "authenticate"
        assert reclaimed.lease_token == lease_token


def test_cancelled_run_is_terminal_while_waiting_for_compute(access_app, make_user) -> None:
    owner = make_user("dbx.cancel-waiting-run@example.gov")
    settings = get_settings()
    with SessionLocal() as db:
        run = PipelineRun(
            user_id=owner.id,
            definition_snapshot_json="{}",
            status=PipelineRunStatus.VALIDATING.value,
            stage="authenticate",
            worker_id="waiting-worker",
            lease_token="waiting-lease",
            lease_expires_at=utcnow() + timedelta(minutes=1),
            cancel_requested_at=utcnow(),
        )
        db.add(run)
        db.commit()

        assert defer_run_for_databricks_compute(
            db, run, lease_token="waiting-lease", settings=settings
        )
        db.refresh(run)
        assert run.status == PipelineRunStatus.CANCELLED.value
        assert run.lease_token is None


def test_opted_in_start_is_persisted_before_provider_call_and_deduplicated(
    access_app, make_user
) -> None:
    owner = make_user("dbx.auto-start@example.gov")
    settings = get_settings().model_copy(
        update={
            "pipeline_enable_databricks": True,
            "pipeline_databricks_startup_contract_verified": True,
            "pipeline_allowed_https_hosts": "dbc-12345678.cloud.databricks.com",
        }
    )
    saved_credentials = {
        "workspace_url": "https://dbc-12345678.cloud.databricks.com",
        "token": "synthetic-api-token",
        "compute_kind": "warehouse",
        "compute_id": "warehouse-1",
        "http_path": "sql/1.0/warehouses/warehouse-1",
        "startup_policy": "allow_start",
    }

    class FakeConnector:
        start_calls = 0

        def start_compute(self, credentials: dict[str, str]) -> dict[str, str]:
            self.start_calls += 1
            return {"outcome": "start_submitted", "state": "STOPPED", "kind": "warehouse"}

    connector = FakeConnector()
    with SessionLocal() as db:
        store_user_credentials(
            db,
            settings,
            user=owner,
            provider="databricks",
            credentials=saved_credentials,
        )
        credentials = decrypt_user_credentials_for_run(
            db, settings, user=owner, provider="databricks"
        )
        run = PipelineRun(
            user_id=owner.id,
            definition_snapshot_json="{}",
            status=PipelineRunStatus.VALIDATING.value,
            stage="authenticate",
            worker_id="auto-start-worker",
            lease_token="auto-start-lease",
            lease_expires_at=utcnow() + timedelta(minutes=1),
        )
        db.add(run)
        db.commit()

        result = request_compute_start(
            db,
            settings=settings,
            user=owner,
            credentials=credentials,
            connector_resolver=lambda _provider: connector,
            observed_state="TERMINATED",
            pipeline_run=run,
            lease_token="auto-start-lease",
            require_opt_in=True,
        )
        db.refresh(run)
        assert result["outcome"] == "start_submitted"
        assert connector.start_calls == 1
        assert run.databricks_compute_attempt_id is not None
        attempt_id = run.databricks_compute_attempt_id
        assert run.databricks_compute_start_deadline_at is not None

        duplicate = request_compute_start(
            db,
            settings=settings,
            user=owner,
            credentials=credentials,
            connector_resolver=lambda _provider: connector,
            observed_state="TERMINATED",
            pipeline_run=run,
            lease_token="auto-start-lease",
            require_opt_in=True,
        )
        assert duplicate["outcome"] == "already_starting"
        assert connector.start_calls == 1
        attempt = db.get(DatabricksComputeAttempt, attempt_id)
        assert attempt is not None
        assert attempt.status == "starting"


def test_compute_start_classifies_ambiguous_and_denied_provider_responses(
    access_app, make_user
) -> None:
    settings = _enabled_databricks_settings()

    for suffix, code, expected in (
        ("ambiguous", TransferErrorCode.PROVIDER_UNAVAILABLE, "start_uncertain"),
        ("denied", TransferErrorCode.PERMISSION_DENIED, "raises"),
    ):
        owner = make_user(f"dbx.start-{suffix}@example.gov")
        with SessionLocal() as db:
            credentials = _saved_databricks_credentials(db, settings, owner)
            credentials["compute_id"] = f"warehouse-{suffix}"

            class FailedStart:
                configured_error = ConnectorError(
                    code,
                    "The Databricks start result requires a status check.",
                    retryable=code is TransferErrorCode.PROVIDER_UNAVAILABLE,
                )

                def start_compute(self, _credentials):
                    raise self.configured_error

            if expected == "raises":
                with pytest.raises(ConnectorError) as denied:
                    request_compute_start(
                        db,
                        settings=settings,
                        user=owner,
                        credentials=credentials,
                        connector_resolver=lambda _provider: FailedStart(),
                    )
                assert denied.value.code is code
            else:
                result = request_compute_start(
                    db,
                    settings=settings,
                    user=owner,
                    credentials=credentials,
                    connector_resolver=lambda _provider: FailedStart(),
                )
                assert result["outcome"] == expected

            attempt = db.scalar(
                select(DatabricksComputeAttempt).where(DatabricksComputeAttempt.user_id == owner.id)
            )
            secret = db.scalar(
                select(UserSecret).where(
                    UserSecret.user_id == owner.id, UserSecret.provider == "databricks"
                )
            )
            assert attempt is not None
            if expected == "raises":
                assert attempt.status == "failed"
                assert attempt.active_key is None
                assert secret is not None and secret.runtime_status == "FAILED"
            else:
                assert attempt.status == "starting"
                assert attempt.error_code == str(code)
                assert secret is not None and secret.runtime_status != "FAILED"


def test_databricks_publication_serializes_target_and_keeps_uncertain_lock(
    access_app, make_user
) -> None:
    owner = make_user("dbx.publish@example.gov")
    locator = _locator()
    with SessionLocal() as db:
        first = _run(owner.id, run_id="11111111-1111-4111-8111-111111111111", lease_token="lease-1")
        second = _run(
            owner.id, run_id="22222222-2222-4222-8222-222222222222", lease_token="lease-2"
        )
        db.add_all((first, second))
        db.commit()

        record_publication_event(
            db,
            run_id=first.id,
            lease_token="lease-1",
            user_id=owner.id,
            locator=locator,
            connection_generation="revision-1",
            write_mode="databricks_append",
            event="prepare_started",
            staging_name="private.staging.data_mover_11111111111141118111111111111111",
        )
        with pytest.raises(ConnectorError) as conflict:
            record_publication_event(
                db,
                run_id=second.id,
                lease_token="lease-2",
                user_id=owner.id,
                locator=locator,
                connection_generation="revision-1",
                write_mode="databricks_append",
                event="prepare_started",
                staging_name="private.staging.data_mover_22222222222242228222222222222222",
            )
        assert conflict.value.code is TransferErrorCode.DESTINATION_CONFLICT

        record_publication_event(
            db,
            run_id=first.id,
            lease_token="lease-1",
            user_id=owner.id,
            locator=locator,
            connection_generation="revision-1",
            write_mode="databricks_append",
            event="publish_submitted",
        )
        record_publication_event(
            db,
            run_id=first.id,
            lease_token="lease-1",
            user_id=owner.id,
            locator=locator,
            connection_generation="revision-1",
            write_mode="databricks_append",
            event="publish_uncertain",
            error=ConnectorError(
                TransferErrorCode.PUBLISH_UNCERTAIN,
                "The destination outcome is not confirmed.",
                retryable=False,
            ),
        )
        saved = db.get(DatabricksPublication, first.id)
        assert saved is not None
        assert saved.state == "uncertain"
        assert saved.active_target_key == saved.target_key

        with pytest.raises(ConnectorError) as still_conflicted:
            record_publication_event(
                db,
                run_id=second.id,
                lease_token="lease-2",
                user_id=owner.id,
                locator=locator,
                connection_generation="revision-1",
                write_mode="databricks_append",
                event="prepare_started",
                staging_name="private.staging.data_mover_22222222222242228222222222222222",
            )
        assert still_conflicted.value.code is TransferErrorCode.DESTINATION_CONFLICT


def test_publication_marker_releases_target_only_after_confirmed_commit(
    access_app, make_user
) -> None:
    owner = make_user("dbx.commit@example.gov")
    locator = _locator()
    with SessionLocal() as db:
        run = _run(owner.id, run_id="33333333-3333-4333-8333-333333333333", lease_token="lease-3")
        db.add(run)
        db.commit()
        common = {
            "db": db,
            "run_id": run.id,
            "lease_token": "lease-3",
            "user_id": owner.id,
            "locator": locator,
            "connection_generation": "revision-1",
            "write_mode": "databricks_replace",
        }
        record_publication_event(
            **common, event="prepare_started", staging_name="private.s.data_mover"
        )
        record_publication_event(**common, event="publish_submitted")
        record_publication_event(**common, event="committed")
        saved = db.get(DatabricksPublication, run.id)
        assert saved is not None
        assert saved.state == "committed"
        assert saved.active_target_key is None
        assert saved.confirmed_at is not None
        record_publication_event(**common, event="cleanup_confirmed")
        saved = db.get(DatabricksPublication, run.id)
        assert saved is not None
        assert saved.state == "complete"
        assert saved.active_target_key is None
        assert db.scalar(select(DatabricksPublication.run_id)) == run.id


def _enabled_databricks_settings(*, startup_contract: bool = False):
    return get_settings().model_copy(
        update={
            "pipeline_enable_databricks": True,
            "pipeline_allowed_https_hosts": "dbc-12345678.cloud.databricks.com",
            "pipeline_databricks_startup_contract_verified": startup_contract,
        }
    )


def _saved_databricks_credentials(
    db, settings, user, *, startup_policy: str = "manual"
) -> dict[str, str]:
    values = {
        "workspace_url": "https://dbc-12345678.cloud.databricks.com",
        "token": "synthetic-api-token",
        "compute_kind": "warehouse",
        "compute_id": "warehouse-1",
        "http_path": "sql/1.0/warehouses/warehouse-1",
        "startup_policy": startup_policy,
    }
    store_user_credentials(
        db,
        settings,
        user=user,
        provider="databricks",
        credentials=values,
    )
    return decrypt_user_credentials_for_run(
        db, settings, user=user, provider="databricks", purpose="test_observation"
    )


@pytest.mark.parametrize(
    ("state", "expected_status", "expected_runtime"),
    [
        ("RUNNING", "ready", "RUNNING"),
        ("ERROR", "failed", "FAILED"),
        ("BAD!STATE", "starting", "STARTING"),
    ],
)
def test_compute_observer_polls_provider_and_persists_safe_outcomes(
    access_app, make_user, state: str, expected_status: str, expected_runtime: str
) -> None:
    owner = make_user(f"dbx.observe.{expected_status}@example.gov")
    settings = _enabled_databricks_settings()
    with SessionLocal() as db:
        credentials = _saved_databricks_credentials(db, settings, owner)
        attempt, created = create_compute_attempt(
            db, user=owner, credentials=credentials, settings=settings
        )
        assert created
        attempt.next_poll_at = utcnow() - timedelta(seconds=1)
        attempt_id = attempt.id
        db.commit()

    class FakeConnector:
        def compute_status(self, _credentials):
            return {"state": state}

    assert process_due_compute_attempt(
        settings=settings,
        session_factory=lambda: SessionLocal(),
        connector_resolver=lambda _provider: FakeConnector(),
    )
    with SessionLocal() as db:
        saved = db.get(DatabricksComputeAttempt, attempt_id)
        secret = db.scalar(
            select(UserSecret).where(
                UserSecret.user_id == owner.id, UserSecret.provider == "databricks"
            )
        )
        assert saved is not None and saved.status == expected_status
        assert saved.poll_count == 1
        assert secret is not None and secret.runtime_status == expected_runtime


def test_compute_observer_rejects_changed_compute_and_retries_transient_errors(
    access_app, make_user
) -> None:
    owner = make_user("dbx.observe-stale@example.gov")
    settings = _enabled_databricks_settings()
    with SessionLocal() as db:
        credentials = _saved_databricks_credentials(db, settings, owner)
        attempt, _ = create_compute_attempt(
            db, user=owner, credentials=credentials, settings=settings
        )
        attempt.compute_id = "different-compute"
        attempt.next_poll_at = utcnow() - timedelta(seconds=1)
        stale_id = attempt.id
        db.commit()

    class ShouldNotBeCalled:
        def compute_status(self, _credentials):
            pytest.fail("stale compute authorization must not be used")

    assert process_due_compute_attempt(
        settings=settings,
        session_factory=lambda: SessionLocal(),
        connector_resolver=lambda _provider: ShouldNotBeCalled(),
    )
    with SessionLocal() as db:
        stale = db.get(DatabricksComputeAttempt, stale_id)
        assert stale is not None and stale.status == "stale"
        assert stale.error_code == str(TransferErrorCode.CREDENTIALS_STALE)

    retry_owner = make_user("dbx.observe-retry@example.gov")
    with SessionLocal() as db:
        credentials = _saved_databricks_credentials(db, settings, retry_owner)
        attempt, _ = create_compute_attempt(
            db, user=retry_owner, credentials=credentials, settings=settings
        )
        attempt.next_poll_at = utcnow() - timedelta(seconds=1)
        retry_id = attempt.id
        db.commit()

    class TemporarilyUnavailable:
        def compute_status(self, _credentials):
            raise RuntimeError("private provider response")

    assert process_due_compute_attempt(
        settings=settings,
        session_factory=lambda: SessionLocal(),
        connector_resolver=lambda _provider: TemporarilyUnavailable(),
    )
    with SessionLocal() as db:
        retry = db.get(DatabricksComputeAttempt, retry_id)
        assert retry is not None and retry.status == "starting"
        assert retry.error_code == str(TransferErrorCode.PROVIDER_UNAVAILABLE)
        assert retry.next_poll_at > utcnow()
        assert "private provider response" not in retry.error_summary


def test_compute_poll_of_inactive_owner_fails_without_provider_access(
    access_app, make_user
) -> None:
    owner = make_user("dbx.inactive-owner@example.gov")
    settings = _enabled_databricks_settings()
    with SessionLocal() as db:
        credentials = _saved_databricks_credentials(db, settings, owner)
        attempt, _ = create_compute_attempt(
            db, user=owner, credentials=credentials, settings=settings
        )
        attempt.next_poll_at = utcnow() - timedelta(seconds=1)
        attempt_id = attempt.id
        saved_owner = db.get(User, owner.id)
        assert saved_owner is not None
        saved_owner.status = "disabled"
        db.commit()

    assert process_due_compute_attempt(
        settings=settings,
        session_factory=lambda: SessionLocal(),
        connector_resolver=lambda _provider: pytest.fail("inactive owners cannot poll compute"),
    )
    with SessionLocal() as db:
        saved = db.get(DatabricksComputeAttempt, attempt_id)
        secret = db.scalar(
            select(UserSecret).where(
                UserSecret.user_id == owner.id, UserSecret.provider == "databricks"
            )
        )
        assert saved is not None and saved.status == "failed"
        assert saved.error_code == str(TransferErrorCode.PERMISSION_DENIED)
        assert secret is not None and secret.runtime_status == "FAILED"


def _create_recovery_candidate(db, *, owner, generation: str, state: str):
    run = PipelineRun(
        user_id=owner.id,
        definition_snapshot_json="{}",
        status=PipelineRunStatus.FAILED.value,
        stage="failed",
    )
    db.add(run)
    db.flush()
    locator = DatabricksTableLocator(
        workspace_id="a" * 64,
        catalog="main",
        schema="analytics",
        table="orders",
        connection_generation=generation,
    )
    target_key = run.id.replace("-", "").ljust(64, "b")[:64]
    publication = DatabricksPublication(
        run_id=run.id,
        user_id=owner.id,
        workspace_id=locator.workspace_id,
        connection_generation=generation,
        target_key=target_key,
        active_target_key=target_key,
        target_locator_json=locator.model_dump_json(),
        write_mode="databricks_append",
        staging_name=f"private.staging.data_mover_{run.id.replace('-', '')}",
        state=state,
        submission_id=run.id,
        submitted_at=utcnow() if state in {"publishing", "uncertain"} else None,
    )
    db.add(publication)
    db.commit()
    return run.id


def test_publication_recovery_cleans_unsubmitted_stage_and_confirms_exact_commit(
    access_app, make_user
) -> None:
    settings = _enabled_databricks_settings()
    owner = make_user("dbx.recovery-cleanup@example.gov")
    with SessionLocal() as db:
        credentials = _saved_databricks_credentials(db, settings, owner)
        cleanup_run_id = _create_recovery_candidate(
            db,
            owner=owner,
            generation=credentials["connection_generation"],
            state="cleanup_pending",
        )
        committed_run_id = _create_recovery_candidate(
            db,
            owner=owner,
            generation=credentials["connection_generation"],
            state="uncertain",
        )
        committed = db.get(DatabricksPublication, committed_run_id)
        assert committed is not None
        committed.active_target_key = None
        db.commit()

    class RecoverableConnector:
        def __init__(self) -> None:
            self.cleaned: list[str] = []
            self.reconciled: list[str] = []

        def cleanup_staging(self, _credentials, *, run_id: str) -> None:
            self.cleaned.append(run_id)

        def reconcile_publication(
            self, _credentials, _locator, *, submission_id: str, submitted_at_ms: int
        ) -> str:
            self.reconciled.append(submission_id)
            return "committed"

    connector = RecoverableConnector()
    assert process_pending_publication_recovery(
        settings=settings,
        session_factory=lambda: SessionLocal(),
        connector_resolver=lambda _provider: connector,
    )
    with SessionLocal() as db:
        cleanup = db.get(DatabricksPublication, cleanup_run_id)
        assert cleanup is not None and cleanup.state == "unchanged"
        assert cleanup.active_target_key is None

    assert process_pending_publication_recovery(
        settings=settings,
        session_factory=lambda: SessionLocal(),
        connector_resolver=lambda _provider: connector,
    )
    with SessionLocal() as db:
        committed = db.get(DatabricksPublication, committed_run_id)
        assert committed is not None and committed.state == "complete"
        assert committed.confirmed_at is not None
        assert committed.active_target_key is None
    assert connector.reconciled == [committed_run_id]
    assert set(connector.cleaned) == {cleanup_run_id, committed_run_id}


def test_publication_recovery_keeps_unconfirmed_commit_fenced_and_retries(
    access_app, make_user
) -> None:
    settings = _enabled_databricks_settings()
    owner = make_user("dbx.recovery-unknown@example.gov")
    with SessionLocal() as db:
        credentials = _saved_databricks_credentials(db, settings, owner)
        run_id = _create_recovery_candidate(
            db,
            owner=owner,
            generation=credentials["connection_generation"],
            state="uncertain",
        )

    class UnknownCommit:
        def reconcile_publication(
            self, _credentials, _locator, *, submission_id: str, submitted_at_ms: int
        ) -> str:
            assert submission_id == run_id
            return "unknown"

        def cleanup_staging(self, *_args, **_kwargs) -> None:
            pytest.fail("uncertain target staging must be retained until reconciliation")

    assert process_pending_publication_recovery(
        settings=settings,
        session_factory=lambda: SessionLocal(),
        connector_resolver=lambda _provider: UnknownCommit(),
    )
    with SessionLocal() as db:
        record = db.get(DatabricksPublication, run_id)
        assert record is not None and record.state == "uncertain"
        assert record.active_target_key == record.target_key
        assert record.last_error_code == "publish_unconfirmed"
        assert record.recovery_token is None
        assert record.recovery_expires_at is not None


def test_publication_recovery_defers_stale_connection_without_provider_calls(
    access_app, make_user
) -> None:
    settings = _enabled_databricks_settings()
    owner = make_user("dbx.recovery-stale@example.gov")
    with SessionLocal() as db:
        credentials = _saved_databricks_credentials(db, settings, owner)
        run_id = _create_recovery_candidate(
            db,
            owner=owner,
            generation=f"stale-{credentials['connection_generation']}",
            state="cleanup_pending",
        )

    class NoProviderCalls:
        def __getattr__(self, name: str):
            pytest.fail(f"provider method {name} must not run with stale credentials")

    assert process_pending_publication_recovery(
        settings=settings,
        session_factory=lambda: SessionLocal(),
        connector_resolver=lambda _provider: NoProviderCalls(),
    )
    with SessionLocal() as db:
        record = db.get(DatabricksPublication, run_id)
        assert record is not None and record.state == "cleanup_required"
        assert record.last_error_code == "credentials_stale"
        assert record.active_target_key == record.target_key


def test_publication_recovery_defers_when_connection_owner_is_inactive(
    access_app, make_user
) -> None:
    settings = _enabled_databricks_settings()
    owner = make_user("dbx.recovery-inactive-owner@example.gov")
    with SessionLocal() as db:
        credentials = _saved_databricks_credentials(db, settings, owner)
        run_id = _create_recovery_candidate(
            db,
            owner=owner,
            generation=credentials["connection_generation"],
            state="cleanup_pending",
        )
        saved_owner = db.get(User, owner.id)
        assert saved_owner is not None
        saved_owner.status = "disabled"
        db.commit()

    assert process_pending_publication_recovery(
        settings=settings,
        session_factory=lambda: SessionLocal(),
        connector_resolver=lambda _provider: pytest.fail("inactive owners cannot recover runs"),
    )
    with SessionLocal() as db:
        record = db.get(DatabricksPublication, run_id)
        assert record is not None and record.state == "cleanup_pending"
        assert record.last_error_code == "owner_unavailable"
        assert record.active_target_key == record.target_key
        assert record.recovery_expires_at is not None


def test_publication_recovery_uses_the_same_opt_in_startup_authorization(
    access_app, make_user
) -> None:
    settings = _enabled_databricks_settings(startup_contract=True)
    owner = make_user("dbx.recovery-opt-in@example.gov")
    with SessionLocal() as db:
        credentials = _saved_databricks_credentials(
            db, settings, owner, startup_policy="allow_start"
        )
        run_id = _create_recovery_candidate(
            db,
            owner=owner,
            generation=credentials["connection_generation"],
            state="cleanup_pending",
        )

    class OptInRecoveryConnector:
        start_calls = 0
        cleanup_calls = 0

        def start_compute(self, _credentials):
            self.start_calls += 1
            return {"outcome": "start_submitted", "state": "STOPPED", "kind": "warehouse"}

        def cleanup_staging(self, saved_credentials, *, run_id: str) -> None:
            assert run_id == run_id_expected
            result = saved_credentials["_startup_request"]("STOPPED")
            assert result["outcome"] == "start_submitted"
            self.cleanup_calls += 1

    run_id_expected = run_id
    connector = OptInRecoveryConnector()
    assert process_pending_publication_recovery(
        settings=settings,
        session_factory=lambda: SessionLocal(),
        connector_resolver=lambda _provider: connector,
    )
    assert connector.start_calls == 1
    assert connector.cleanup_calls == 1
    with SessionLocal() as db:
        attempt = db.scalar(
            select(DatabricksComputeAttempt).where(DatabricksComputeAttempt.user_id == owner.id)
        )
        record = db.get(DatabricksPublication, run_id)
        assert attempt is not None and attempt.status == "starting"
        assert record is not None and record.state == "unchanged"


def test_publication_recovery_retains_fence_when_cleanup_is_unavailable(
    access_app, make_user
) -> None:
    settings = _enabled_databricks_settings()
    owner = make_user("dbx.recovery-cleanup-retry@example.gov")
    with SessionLocal() as db:
        credentials = _saved_databricks_credentials(db, settings, owner)
        run_id = _create_recovery_candidate(
            db,
            owner=owner,
            generation=credentials["connection_generation"],
            state="cleanup_pending",
        )

    class CleanupUnavailable:
        def cleanup_staging(self, *_args, **_kwargs) -> None:
            raise ConnectorError(
                TransferErrorCode.PROVIDER_UNAVAILABLE,
                "The staging table could not be checked.",
                retryable=True,
            )

    assert process_pending_publication_recovery(
        settings=settings,
        session_factory=lambda: SessionLocal(),
        connector_resolver=lambda _provider: CleanupUnavailable(),
    )
    with SessionLocal() as db:
        record = db.get(DatabricksPublication, run_id)
        assert record is not None and record.state == "cleanup_pending"
        assert record.active_target_key == record.target_key
        assert record.last_error_code == str(TransferErrorCode.PROVIDER_UNAVAILABLE)
        assert record.recovery_expires_at is not None


def test_publication_recovery_sanitizes_unexpected_cleanup_errors(access_app, make_user) -> None:
    settings = _enabled_databricks_settings()
    owner = make_user("dbx.recovery-unexpected@example.gov")
    with SessionLocal() as db:
        credentials = _saved_databricks_credentials(db, settings, owner)
        run_id = _create_recovery_candidate(
            db,
            owner=owner,
            generation=credentials["connection_generation"],
            state="cleanup_pending",
        )

    class UnexpectedCleanupFailure:
        def cleanup_staging(self, *_args, **_kwargs) -> None:
            raise RuntimeError("private provider response")

    assert process_pending_publication_recovery(
        settings=settings,
        session_factory=lambda: SessionLocal(),
        connector_resolver=lambda _provider: UnexpectedCleanupFailure(),
    )
    with SessionLocal() as db:
        record = db.get(DatabricksPublication, run_id)
        assert record is not None and record.state == "cleanup_pending"
        assert record.active_target_key == record.target_key
        assert record.last_error_code == "recovery_unavailable"
        assert "private provider response" not in record.last_error_summary
        assert record.recovery_expires_at is not None


def test_compute_worker_returns_false_when_no_poll_is_due(access_app) -> None:
    assert not process_due_compute_attempt(
        settings=get_settings(),
        session_factory=lambda: SessionLocal(),
        connector_resolver=lambda _provider: pytest.fail("nothing is due"),
    )


class _RecoveryProbe:
    def __init__(self, on_cleanup: Callable[[str], None] | None = None) -> None:
        self.on_cleanup = on_cleanup
        self.reconciled: list[str] = []
        self.cleaned: list[str] = []

    def reconcile_publication(
        self, _credentials, _locator, *, submission_id: str, submitted_at_ms: int
    ) -> str:
        self.reconciled.append(submission_id)
        return "committed"

    def cleanup_staging(self, _credentials, *, run_id: str) -> None:
        self.cleaned.append(run_id)
        if self.on_cleanup is not None:
            self.on_cleanup(run_id)


def _recover_publication(settings, connector) -> bool:
    return process_pending_publication_recovery(
        settings=settings,
        session_factory=SessionLocal,
        connector_resolver=lambda _provider: connector,
    )


@pytest.mark.parametrize("unexpected", [False, True], ids=["provider-error", "unexpected-error"])
def test_confirmed_publication_cleanup_failure_is_deferred_and_retried(
    access_app, make_user, unexpected: bool
) -> None:
    settings = _enabled_databricks_settings()
    owner = make_user("dbx.post-commit-cleanup@example.gov")
    with SessionLocal() as db:
        credentials = _saved_databricks_credentials(db, settings, owner)
        run_id = _create_recovery_candidate(
            db, owner=owner, generation=credentials["connection_generation"], state="uncertain"
        )

    def fail_cleanup(_run_id: str) -> None:
        if unexpected:
            raise RuntimeError("private-provider-response")
        raise ConnectorError(
            TransferErrorCode.PROVIDER_UNAVAILABLE,
            "Staging cleanup is unavailable.",
            retryable=True,
        )

    connector = _RecoveryProbe(fail_cleanup)
    assert _recover_publication(settings, connector)
    with SessionLocal() as db:
        saved = db.get(DatabricksPublication, run_id)
        assert saved is not None and saved.state == "cleanup_pending"
        assert saved.confirmed_at is not None
        assert saved.active_target_key is None
        assert saved.recovery_token is None
        assert saved.recovery_expires_at is not None and saved.recovery_expires_at > utcnow()
        assert saved.last_error_code == (
            "recovery_unavailable" if unexpected else str(TransferErrorCode.PROVIDER_UNAVAILABLE)
        )
        assert "private-provider-response" not in saved.last_error_summary

    assert not _recover_publication(settings, connector)
    assert connector.cleaned == [run_id]
    with SessionLocal() as db:
        saved = db.get(DatabricksPublication, run_id)
        assert saved is not None
        saved.recovery_expires_at = utcnow() - timedelta(seconds=1)
        db.commit()

    retry = _RecoveryProbe()
    assert _recover_publication(settings, retry)
    assert retry.cleaned == [run_id]
    assert retry.reconciled == []
    with SessionLocal() as db:
        saved = db.get(DatabricksPublication, run_id)
        assert saved is not None and saved.state == "complete"
        assert saved.confirmed_at is not None
        assert saved.last_error_code is None


def test_recovery_keeps_its_lease_until_post_commit_cleanup_finishes(access_app, make_user) -> None:
    settings = _enabled_databricks_settings()
    owner = make_user("dbx.recovery-lease@example.gov")
    with SessionLocal() as db:
        credentials = _saved_databricks_credentials(db, settings, owner)
        run_id = _create_recovery_candidate(
            db, owner=owner, generation=credentials["connection_generation"], state="uncertain"
        )
    competitor = _RecoveryProbe()
    nested_claims: list[bool] = []
    held_tokens: list[str | None] = []

    def inspect_during_cleanup(cleanup_run_id: str) -> None:
        with SessionLocal() as db:
            saved = db.get(DatabricksPublication, cleanup_run_id)
            assert saved is not None
            held_tokens.append(saved.recovery_token)
        nested_claims.append(_recover_publication(settings, competitor))

    connector = _RecoveryProbe(inspect_during_cleanup)
    assert _recover_publication(settings, connector)
    assert held_tokens and held_tokens[0] is not None
    assert nested_claims == [False]
    assert connector.cleaned == [run_id]
    assert competitor.cleaned == []


def test_stale_cleanup_result_cannot_overwrite_another_recovery_owner(
    access_app, make_user
) -> None:
    settings = _enabled_databricks_settings()
    owner = make_user("dbx.stale-cleanup-result@example.gov")
    with SessionLocal() as db:
        credentials = _saved_databricks_credentials(db, settings, owner)
        run_id = _create_recovery_candidate(
            db,
            owner=owner,
            generation=credentials["connection_generation"],
            state="cleanup_pending",
        )

    def replace_recovery_owner(cleanup_run_id: str) -> None:
        with SessionLocal() as db:
            saved = db.get(DatabricksPublication, cleanup_run_id)
            assert saved is not None
            saved.recovery_token = "replacement-recovery-owner"
            saved.recovery_expires_at = utcnow() + timedelta(minutes=1)
            saved.last_error_code = "newer-review-required"
            db.commit()

    assert _recover_publication(settings, _RecoveryProbe(replace_recovery_owner))
    with SessionLocal() as db:
        saved = db.get(DatabricksPublication, run_id)
        assert saved is not None and saved.state == "cleanup_pending"
        assert saved.active_target_key == saved.target_key
        assert saved.recovery_token == "replacement-recovery-owner"
        assert saved.last_error_code == "newer-review-required"


@pytest.mark.parametrize("stale_state", ["RUNNING", "ERROR"])
def test_reclaimed_poll_lease_rejects_results_from_a_cached_old_worker(
    access_app, make_user, stale_state: str
) -> None:
    owner = make_user("dbx.reclaimed-poll@example.gov")
    settings = get_settings()
    now = utcnow()
    with SessionLocal() as old_worker:
        attempt, _ = create_compute_attempt(
            old_worker, user=owner, credentials=_credentials(), settings=settings
        )
        attempt.next_poll_at = now
        attempt.deadline_at = now + timedelta(minutes=5)
        old_worker.commit()
        first = claim_due_attempt(old_worker, now=now, lease_seconds=15)
        assert first is not None
        attempt_id, old_token = first

        with SessionLocal() as new_worker:
            second = claim_due_attempt(new_worker, now=now + timedelta(seconds=16))
            assert second is not None and second[0] == attempt_id
            new_token = second[1]
        assert new_token != old_token
        assert not observe_attempt(
            old_worker,
            attempt_id=attempt_id,
            lease_token=old_token,
            state=stale_state,
            settings=settings,
            now=now + timedelta(seconds=17),
        )

    with SessionLocal() as db:
        saved = db.get(DatabricksComputeAttempt, attempt_id)
        assert saved is not None and saved.status == "starting"
        assert saved.poll_count == 0
        assert saved.lease_token == new_token
        assert observe_attempt(
            db,
            attempt_id=attempt_id,
            lease_token=new_token,
            state="RUNNING",
            settings=settings,
            now=now + timedelta(seconds=18),
        )
        db.refresh(saved)
        assert saved.status == "ready"
        assert saved.poll_count == 1
