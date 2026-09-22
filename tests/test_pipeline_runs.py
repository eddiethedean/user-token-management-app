"""Durable run state machine, worker execution, and retention janitor."""

from __future__ import annotations

import json
import logging
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from types import SimpleNamespace
from typing import cast
from unittest.mock import Mock

import pytest
from sqlalchemy import select

from app.config import get_settings
from app.connectors.errors import TransferErrorCode
from app.database import SessionLocal
from app.models import (
    PipelineDefinition,
    PipelineRun,
    PipelineRunEvent,
    PipelineRunStatus,
    User,
    utcnow,
)
from app.services.pipeline_runs import (
    ALLOWED_TRANSITIONS,
    _run_requires_reconciliation_review,
    append_event,
    cancel_claimed_run,
    claim_run,
    complete_run,
    enqueue_run,
    fail_run,
    heartbeat,
    janitor,
    record_reconciliation_review,
    renew_lease,
    request_cancel,
    snapshot_from_definition,
)
from app.services.pipeline_state import RunConflictError
from app.services.pipelines import save_pipeline
from app.worker import _cancel_flag, process_one
from tests.helpers import csrf_from, web_login


def test_guarded_transitions_reject_illegal_jumps() -> None:
    assert "succeeded" not in ALLOWED_TRANSITIONS[PipelineRunStatus.QUEUED.value]
    assert (
        PipelineRunStatus.EXTRACTING.value
        in ALLOWED_TRANSITIONS[PipelineRunStatus.VALIDATING.value]
    )


def test_queued_pipeline_run_executes_through_fake_connectors(client, demo_connections) -> None:
    web_login(client, next_path="/pipeline")
    page = client.get("/pipeline")
    saved = client.post(
        "/pipeline/save",
        data={
            "csrf_token": csrf_from(page.text),
            "pipeline_name": "Worker handshake",
            "source_provider": "mss",
            "source_schema": "ri.foundry.main.dataset.demo-operations",
            "source_table": "mission_orders.parquet",
            "destination_provider": "postgres",
            "destination_schema": "public",
            "destination_table": "mission_orders",
            "write_mode": "append",
        },
    )
    assert saved.status_code == 303
    with SessionLocal() as db:
        pipeline = db.scalar(
            select(PipelineDefinition).where(PipelineDefinition.name == "Worker handshake")
        )
        assert pipeline is not None
        pipeline_id = pipeline.id

    queued = client.post(
        "/pipeline/runs",
        data={
            "csrf_token": csrf_from(client.get("/pipeline").text),
            "pipeline_id": str(pipeline_id),
        },
        headers={"HX-Request": "true", "HX-Target": "pipeline-run-monitor", "Accept": "text/html"},
    )
    assert queued.status_code in {200, 202}
    assert "pipeline-run-monitor" in queued.text
    assert "succeeded" in queued.text or "queued" in queued.text or "extracting" in queued.text
    assert "Succeeded" in queued.text
    assert 'data-hedron-connector-active="false"' in queued.text
    assert 'aria-label="Live transfer stages"' in queued.text
    assert 'aria-label="Event feed for Worker handshake"' in queued.text
    assert "Transfer succeeded." in queued.text
    assert "Destination table" in queued.text
    assert "0 → 3 rows" in queued.text
    assert "+3 rows" in queued.text
    assert "Run schema &amp; row counts" in queued.text
    assert "Persisted schema" in queued.text
    assert "event_id" in queued.text
    assert 'data-hedron-columns="2" data-hedron-columns-xl="4"' in queued.text
    assert 'data-hedron-columns="1" data-hedron-columns-xl="2"' in queued.text
    assert 'data-hedron-columns="2" data-hedron-columns-lg="3"' in queued.text
    assert 'data-hedron-async-region="true"' in queued.text
    assert 'data-hedron-action-phase="success"' in queued.text

    restored = client.get(f"/pipeline?pipeline_id={pipeline_id}")
    assert "Live transfer" in restored.text
    assert "Persisted run history" in restored.text
    assert "Transfer succeeded." in restored.text

    with SessionLocal() as db:
        run = db.scalar(
            select(PipelineRun).where(PipelineRun.pipeline_definition_id == pipeline_id)
        )
        assert run is not None
        assert run.status == PipelineRunStatus.SUCCEEDED.value
        verification = json.loads(run.verification_json or "{}")
        assert verification["destination_rows_before"] == 0
        assert verification["destination_rows_after"] == 3
        assert verification["destination_row_delta"] == 3
        events = list(
            db.scalars(select(PipelineRunEvent).where(PipelineRunEvent.run_id == run.id)).all()
        )
        assert events
        assert all("token" not in event.message.casefold() for event in events)


def test_worker_persists_writer_policy_denial_without_writing(
    client, demo_connections, monkeypatch
) -> None:
    from app.connectors.locators import postgres_table
    from app.connectors.registry import row_counter_for
    from app.services import transfer_engine
    from app.services.demo import DEMO_CONNECTION_CREDENTIALS

    web_login(client, next_path="/pipeline")
    page = client.get("/pipeline")
    saved = client.post(
        "/pipeline/save",
        data={
            "csrf_token": csrf_from(page.text),
            "pipeline_name": "Worker writer denial",
            "source_provider": "mss",
            "source_schema": "ri.foundry.main.dataset.demo-operations",
            "source_table": "mission_orders.parquet",
            "destination_provider": "postgres",
            "destination_schema": "public",
            "destination_table": "readiness_events",
            "write_mode": "append",
        },
    )
    assert saved.status_code == 303
    with SessionLocal() as db:
        pipeline = db.scalar(
            select(PipelineDefinition).where(PipelineDefinition.name == "Worker writer denial")
        )
        user = db.scalar(select(User).where(User.email == "admin@example.gov"))
        assert pipeline is not None and user is not None
        run = enqueue_run(
            db, user=user, pipeline=pipeline, snapshot=snapshot_from_definition(pipeline)
        )
        run_id = run.id

    credentials = DEMO_CONNECTION_CREDENTIALS["postgres"]
    destination_locator = postgres_table("public", "readiness_events")
    before = row_counter_for("postgres").count_rows(credentials, destination_locator)
    assert before is not None
    denied = Mock(return_value=False)
    settings = get_settings()
    monkeypatch.setattr(transfer_engine, "writer_enabled", denied)

    with SessionLocal() as db:
        assert process_one(db, settings, run_id=run_id) is True

    with SessionLocal() as db:
        failed = db.get(PipelineRun, run_id)
        assert failed is not None
        assert failed.status == PipelineRunStatus.FAILED.value
        assert failed.error_code == "permission_denied"
        assert failed.retryable is False
        assert failed.source_rows == 0
        assert failed.source_bytes == 0
        assert failed.loaded_rows == 0
        assert failed.loaded_bytes == 0

    after = row_counter_for("postgres").count_rows(credentials, destination_locator)
    assert after == before
    denied.assert_called_once_with("postgres", settings=settings)


def test_worker_unexpected_failures_do_not_log_exception_values(access_app, caplog, monkeypatch):
    from app import worker

    marker = "AUDIT_SYNTHETIC_PRIVATE_CELL"
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.email == "admin@example.gov"))
        assert user is not None
        run = PipelineRun(
            user_id=user.id,
            status=PipelineRunStatus.QUEUED.value,
            definition_snapshot_json="{}",
        )
        db.add(run)
        db.commit()
        run_id = run.id

    monkeypatch.setattr(
        worker,
        "execute_transfer",
        Mock(side_effect=RuntimeError(marker)),
    )
    monkeypatch.setattr(worker, "parse_snapshot", Mock(return_value=Mock()))
    with caplog.at_level(logging.ERROR, logger="app.worker"):
        with SessionLocal() as db:
            assert process_one(
                db,
                get_settings(),
                run_id=run_id,
                credential_resolver=lambda *args, **kwargs: {},
            )

    assert marker not in caplog.text
    assert "RuntimeError" in caplog.text
    assert any(getattr(record, "exception_type", "") == "RuntimeError" for record in caplog.records)
    with SessionLocal() as db:
        failed = db.get(PipelineRun, run_id)
        assert failed is not None
        assert failed.error_summary == "The transfer failed unexpectedly."


def test_partial_write_is_persisted_as_reconciliation_required(access_app) -> None:
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.email == "admin@example.gov"))
        assert user is not None
        run = PipelineRun(
            user_id=user.id,
            definition_snapshot_json="{}",
            status=PipelineRunStatus.LOADING.value,
            stage="loading",
            loaded_rows=0,
        )
        db.add(run)
        db.commit()
        fail_run(
            db,
            run,
            code=TransferErrorCode.PARTIAL_WRITE,
            summary="The destination write was only partially acknowledged.",
        )

        assert run.status == PipelineRunStatus.FAILED_NEEDS_RECONCILIATION.value
        assert run.retryable is False
        facts = json.loads(run.verification_json or "{}")
        assert facts["data_impact"] == "uncertain"
        assert facts["reconciliation_required"] is True


def test_unknown_failure_after_destination_work_requires_reconciliation(access_app) -> None:
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.email == "admin@example.gov"))
        assert user is not None
        run = PipelineRun(
            user_id=user.id,
            definition_snapshot_json="{}",
            status=PipelineRunStatus.LOADING.value,
            stage="transfer",
            loaded_rows=1,
        )
        db.add(run)
        db.commit()

        fail_run(db, run, code="future_provider_failure", summary="future failure", retryable=True)

        assert run.status == PipelineRunStatus.FAILED_NEEDS_RECONCILIATION.value
        assert run.reconciliation_required is True
        assert run.data_impact == "uncertain"
        assert run.retryable is False


def test_completion_impact_matches_provider_verification_level(access_app) -> None:
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.email == "admin@example.gov"))
        assert user is not None
        run = PipelineRun(
            user_id=user.id,
            definition_snapshot_json="{}",
            status=PipelineRunStatus.VERIFYING.value,
            stage="verify",
            lease_token="lease-1",
        )
        db.add(run)
        db.commit()

        complete_run(
            db,
            run,
            lease_token="lease-1",
            verification={"verification_level": "local_manifest"},
        )

        assert run.status == PipelineRunStatus.SUCCEEDED.value
        assert run.data_impact == "changed"


def test_uncertain_cancellation_is_reviewable_and_blocks_enqueue(
    access_app, demo_connections
) -> None:
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.email == "admin@example.gov"))
        assert user is not None
        pipeline = save_pipeline(
            db,
            user=user,
            name="Uncertain cancellation gate",
            source_provider="mss",
            destination_provider="postgres",
            write_mode="append",
            available_providers={"mss", "postgres"},
            source_schema="ri.foundry.main.dataset.demo-operations",
            source_table="mission_orders.parquet",
            destination_schema="public",
            destination_table="uncertain_cancellation",
        )
        run = PipelineRun(
            user_id=user.id,
            pipeline_definition_id=pipeline.id,
            definition_snapshot_json=snapshot_from_definition(pipeline).model_dump_json(),
            status=PipelineRunStatus.LOADING.value,
            stage="transfer",
            lease_token="lease-1",
            loaded_rows=1,
        )
        db.add(run)
        db.commit()

        cancel_claimed_run(db, run, lease_token="lease-1")
        assert run.status == PipelineRunStatus.FAILED_NEEDS_RECONCILIATION.value
        assert run.reconciliation_required is True
        assert run.last_safe_stage == "transfer"

        with pytest.raises(ValueError, match="reconciliation review"):
            enqueue_run(
                db,
                user=user,
                pipeline=pipeline,
                snapshot=snapshot_from_definition(pipeline),
            )

        record_reconciliation_review(db, user=user, run_id=run.id)
        retry = enqueue_run(
            db,
            user=user,
            pipeline=pipeline,
            snapshot=snapshot_from_definition(pipeline),
        )
        assert retry.parent_run_id == run.id
        assert retry.attempt == 2

        follow_up = enqueue_run(
            db,
            user=user,
            pipeline=pipeline,
            snapshot=snapshot_from_definition(pipeline),
        )
        assert follow_up.parent_run_id is None
        assert follow_up.attempt == 1


def test_legacy_failed_run_with_loaded_rows_blocks_enqueue(access_app, demo_connections) -> None:
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.email == "admin@example.gov"))
        assert user is not None
        pipeline = save_pipeline(
            db,
            user=user,
            name="Legacy failed gate",
            source_provider="mss",
            destination_provider="postgres",
            write_mode="append",
            available_providers={"mss", "postgres"},
            source_schema="ri.foundry.main.dataset.demo-operations",
            source_table="mission_orders.parquet",
            destination_schema="public",
            destination_table="legacy_failed_gate",
        )
        run = PipelineRun(
            user_id=user.id,
            pipeline_definition_id=pipeline.id,
            definition_snapshot_json=snapshot_from_definition(pipeline).model_dump_json(),
            status=PipelineRunStatus.FAILED.value,
            stage="failed",
            loaded_rows=4,
            reconciliation_required=True,
        )
        db.add(run)
        db.commit()

        with pytest.raises(ValueError, match="reconciliation review"):
            enqueue_run(
                db,
                user=user,
                pipeline=pipeline,
                snapshot=snapshot_from_definition(pipeline),
            )


def test_malformed_legacy_safety_facts_block_reconciliation_review() -> None:
    run = cast(
        PipelineRun,
        SimpleNamespace(
            status=PipelineRunStatus.FAILED.value,
            reconciliation_required=False,
            data_impact=None,
            verification_json="{not-json",
        ),
    )

    assert _run_requires_reconciliation_review(run) is True


def test_cancel_before_claim_marks_run_cancelled(client, demo_connections) -> None:
    web_login(client, next_path="/pipeline")
    page = client.get("/pipeline")
    saved = client.post(
        "/pipeline/save",
        data={
            "csrf_token": csrf_from(page.text),
            "pipeline_name": "Cancel queued",
            "source_provider": "mss",
            "source_schema": "ri.foundry.main.dataset.demo-operations",
            "source_table": "mission_orders.parquet",
            "destination_provider": "postgres",
            "destination_schema": "public",
            "destination_table": "mission_orders",
            "write_mode": "append",
        },
    )
    assert saved.status_code == 303
    with SessionLocal() as db:
        pipeline = db.scalar(
            select(PipelineDefinition).where(PipelineDefinition.name == "Cancel queued")
        )
        user = db.scalar(select(User).where(User.email == "admin@example.gov"))
        assert pipeline is not None and user is not None
        snapshot = snapshot_from_definition(pipeline)
        run = enqueue_run(db, user=user, pipeline=pipeline, snapshot=snapshot)
        cancelled = request_cancel(db, user=user, run_id=run.id)
        assert cancelled.status == PipelineRunStatus.CANCELLED.value
        assert cancelled.data_impact == "unchanged"
        assert cancelled.reconciliation_required is False
        assert json.loads(cancelled.verification_json or "{}")["data_impact"] == "unchanged"
        assert _run_requires_reconciliation_review(cancelled) is False


def test_worker_cancellation_check_reads_changes_from_another_session(access_app) -> None:
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.email == "admin@example.gov"))
        assert user is not None
        run = PipelineRun(
            user_id=user.id,
            definition_snapshot_json="{}",
            status=PipelineRunStatus.EXTRACTING.value,
            stage="inspect",
        )
        db.add(run)
        db.commit()
        run_id = run.id

    with SessionLocal() as worker_db:
        # Load the run first to reproduce the worker identity-map state.
        assert worker_db.get(PipelineRun, run_id) is not None
        with SessionLocal() as request_db:
            request_user = request_db.get(User, user.id)
            assert request_user is not None
            request_cancel(request_db, user=request_user, run_id=run_id)
        assert _cancel_flag(worker_db, run_id)


def test_lease_renewal_is_atomic_and_stale_identity_map_is_rejected(access_app) -> None:
    settings = get_settings()
    with SessionLocal() as worker_db:
        user = worker_db.scalar(select(User).where(User.email == "admin@example.gov"))
        assert user is not None
        queued = PipelineRun(
            user_id=user.id,
            definition_snapshot_json="{}",
            status=PipelineRunStatus.QUEUED.value,
            stage="queued",
        )
        worker_db.add(queued)
        worker_db.commit()
        claimed = claim_run(
            worker_db,
            worker_id="worker-a",
            lease_seconds=settings.pipeline_lease_seconds,
            run_id=queued.id,
        )
        assert claimed is not None
        run, token = claimed

        with SessionLocal() as heartbeat_db:
            assert renew_lease(
                heartbeat_db,
                run_id=run.id,
                lease_token=token,
                lease_seconds=settings.pipeline_lease_seconds,
            )
            replacement = heartbeat_db.get(PipelineRun, run.id)
            assert replacement is not None
            replacement.lease_token = "worker-b-token"
            heartbeat_db.commit()

        with pytest.raises(RunConflictError):
            heartbeat(
                worker_db,
                run,
                lease_token=token,
                lease_seconds=settings.pipeline_lease_seconds,
            )


def test_active_run_monitor_exposes_cancel_control(client, demo_connections, monkeypatch) -> None:
    web_login(client, next_path="/pipeline")
    page = client.get("/pipeline")
    saved = client.post(
        "/pipeline/save",
        data={
            "csrf_token": csrf_from(page.text),
            "pipeline_name": "Cancel monitor",
            "source_provider": "mss",
            "source_schema": "ri.foundry.main.dataset.demo-operations",
            "source_table": "mission_orders.parquet",
            "destination_provider": "postgres",
            "destination_schema": "public",
            "destination_table": "mission_orders",
            "write_mode": "append",
        },
    )
    assert saved.status_code == 303
    with SessionLocal() as db:
        pipeline = db.scalar(
            select(PipelineDefinition).where(PipelineDefinition.name == "Cancel monitor")
        )
        user = db.scalar(select(User).where(User.email == "admin@example.gov"))
        assert pipeline is not None and user is not None
        run = enqueue_run(
            db, user=user, pipeline=pipeline, snapshot=snapshot_from_definition(pipeline)
        )
        run_id = run.id
    import app.services.pipeline_runs as pipeline_run_service

    real_events_after = pipeline_run_service.events_after
    event_queries: list[int] = []

    def counted_events_after(db, *, run, after_sequence=0):
        event_queries.append(after_sequence)
        return real_events_after(db, run=run, after_sequence=after_sequence)

    monkeypatch.setattr(pipeline_run_service, "events_after", counted_events_after)
    response = client.get(
        f"/pipeline/runs/{run_id}/status",
        headers={"HX-Request": "true", "HX-Target": "pipeline-run-monitor"},
    )
    assert response.status_code == 200
    assert "Cancel run" in response.text
    assert f'hx-post="/pipeline/runs/{run_id}/cancel"' in response.text
    assert 'data-hedron-action-phase="pending"' in response.text
    assert f'hx-get="/pipeline/runs/{run_id}/status"' in response.text
    assert event_queries == [0]

    direct = client.get(f"/pipeline/runs/{run_id}/status")
    assert direct.status_code == 303
    assert direct.headers["location"] == f"/pipeline?run_id={run_id}"


def test_reconciliation_review_is_recorded_without_clearing_safety_state(access_app) -> None:
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.email == "admin@example.gov"))
        assert user is not None
        run = PipelineRun(
            user_id=user.id,
            definition_snapshot_json="{}",
            status=PipelineRunStatus.FAILED_NEEDS_RECONCILIATION.value,
            stage="reconcile",
        )
        db.add(run)
        db.commit()
        run_id = run.id
        reviewed = record_reconciliation_review(db, user=user, run_id=run_id)
        verification = json.loads(reviewed.verification_json or "{}")
        assert reviewed.status == PipelineRunStatus.FAILED_NEEDS_RECONCILIATION.value
        assert verification["reconciliation_reviewed_at"]
        events = list(
            db.scalars(select(PipelineRunEvent).where(PipelineRunEvent.run_id == run_id)).all()
        )
        assert any("reconciliation review" in event.message.casefold() for event in events)


def test_janitor_purges_expired_events_and_terminal_runs(access_app, tmp_path) -> None:
    settings = get_settings()
    original_events = settings.pipeline_event_retention_days
    original_runs = settings.pipeline_run_retention_days
    original_spool = settings.pipeline_spool_root
    settings.pipeline_event_retention_days = 1
    settings.pipeline_run_retention_days = 1
    settings.pipeline_spool_root = str(tmp_path)
    stale = utcnow() - timedelta(days=40)
    stale_chunks = tmp_path / "stale-run.chunks"
    stale_chunks.mkdir()
    (stale_chunks / "00000001.parquet").write_bytes(b"stale")
    old_timestamp = stale.timestamp()
    os.utime(stale_chunks, (old_timestamp, old_timestamp))
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.email == "admin@example.gov"))
        assert user is not None
        run = PipelineRun(
            user_id=user.id,
            definition_snapshot_json="{}",
            status=PipelineRunStatus.SUCCEEDED.value,
            stage="verify",
            finished_at=stale,
        )
        protected = PipelineRun(
            user_id=user.id,
            definition_snapshot_json="{}",
            status=PipelineRunStatus.FAILED_NEEDS_RECONCILIATION.value,
            stage="reconcile",
            finished_at=stale,
            reconciliation_required=True,
            data_impact="uncertain",
        )
        db.add(run)
        db.add(protected)
        db.flush()
        db.add(
            PipelineRunEvent(
                run_id=run.id,
                sequence=1,
                occurred_at=stale,
                message="old event",
            )
        )
        db.add(
            PipelineRunEvent(
                run_id=protected.id,
                sequence=1,
                occurred_at=stale,
                message="reconciliation evidence",
            )
        )
        db.commit()
        run_id = run.id
        protected_id = protected.id
    with SessionLocal() as db:
        counts = janitor(db, settings)
        assert counts["events"] >= 1
        assert counts["runs"] >= 1
        assert counts["spool_files"] == 1
        assert not stale_chunks.exists()
        assert db.get(PipelineRun, run_id) is None
        assert db.get(PipelineRun, protected_id) is not None
        assert (
            db.scalar(select(PipelineRunEvent).where(PipelineRunEvent.run_id == protected_id))
            is not None
        )
    settings.pipeline_event_retention_days = original_events
    settings.pipeline_run_retention_days = original_runs
    settings.pipeline_spool_root = original_spool


def test_process_one_is_idle_when_queue_is_empty(access_app) -> None:
    with SessionLocal() as db:
        assert process_one(db, get_settings()) is False


def test_event_sequences_are_atomic_across_sessions(access_app) -> None:
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.email == "admin@example.gov"))
        assert user is not None
        run = PipelineRun(
            user_id=user.id,
            definition_snapshot_json="{}",
            status=PipelineRunStatus.QUEUED.value,
            stage="queued",
        )
        db.add(run)
        db.commit()
        run_id = run.id

    barrier = threading.Barrier(2)

    def record(message: str) -> int:
        with SessionLocal() as db:
            current = db.get(PipelineRun, run_id)
            assert current is not None
            barrier.wait()
            event = append_event(db, current, message)
            db.commit()
            return event.sequence

    with ThreadPoolExecutor(max_workers=2) as executor:
        sequences = list(executor.map(record, ("worker event", "cancel event")))

    assert sorted(sequences) == [1, 2]
    with SessionLocal() as db:
        current = db.get(PipelineRun, run_id)
        assert current is not None
        assert current.next_event_sequence == 2


def test_idempotent_enqueue_is_atomic_across_sessions(access_app, demo_connections) -> None:
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.email == "admin@example.gov"))
        assert user is not None
        pipeline = save_pipeline(
            db,
            user=user,
            name="Concurrent enqueue",
            source_provider="mss",
            destination_provider="postgres",
            write_mode="append",
            available_providers={"mss", "postgres"},
            source_schema="ri.foundry.main.dataset.demo-operations",
            source_table="mission_orders.parquet",
            destination_schema="public",
            destination_table="mission_orders",
        )
        user_id = user.id
        pipeline_id = pipeline.id

    barrier = threading.Barrier(2)

    def enqueue() -> str:
        with SessionLocal() as db:
            user = db.get(User, user_id)
            pipeline = db.get(PipelineDefinition, pipeline_id)
            assert user is not None and pipeline is not None
            snapshot = snapshot_from_definition(pipeline)
            barrier.wait()
            return enqueue_run(
                db,
                user=user,
                pipeline=pipeline,
                snapshot=snapshot,
                idempotency_token="same-request",
            ).id

    with ThreadPoolExecutor(max_workers=2) as executor:
        run_ids = list(executor.map(lambda _index: enqueue(), range(2)))

    assert len(set(run_ids)) == 1
    with SessionLocal() as db:
        runs = list(
            db.scalars(
                select(PipelineRun).where(PipelineRun.idempotency_token == "same-request")
            ).all()
        )
        events = list(
            db.scalars(select(PipelineRunEvent).where(PipelineRunEvent.run_id == run_ids[0])).all()
        )
        assert len(runs) == 1
        assert [event.message for event in events] == ["Run queued."]
