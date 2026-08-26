"""Durable run state machine, worker execution, and retention janitor."""

from __future__ import annotations

import json
import os
from datetime import timedelta

from sqlalchemy import select

from app.config import get_settings
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
    enqueue_run,
    janitor,
    record_reconciliation_review,
    request_cancel,
    snapshot_from_definition,
)
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


def test_active_run_monitor_exposes_cancel_control(client, demo_connections) -> None:
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
    response = client.get(
        f"/pipeline/runs/{run_id}/status",
        headers={"HX-Request": "true", "HX-Target": "pipeline-run-monitor"},
    )
    assert response.status_code == 200
    assert "Cancel run" in response.text
    assert f'hx-post="/pipeline/runs/{run_id}/cancel"' in response.text
    assert 'data-hedron-action-phase="pending"' in response.text


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
        db.add(run)
        db.flush()
        db.add(
            PipelineRunEvent(
                run_id=run.id,
                sequence=1,
                occurred_at=stale,
                message="old event",
            )
        )
        db.commit()
        run_id = run.id
    with SessionLocal() as db:
        counts = janitor(db, settings)
        assert counts["events"] >= 1
        assert counts["runs"] >= 1
        assert counts["spool_files"] == 1
        assert not stale_chunks.exists()
        assert db.get(PipelineRun, run_id) is None
    settings.pipeline_event_retention_days = original_events
    settings.pipeline_run_retention_days = original_runs
    settings.pipeline_spool_root = original_spool


def test_process_one_is_idle_when_queue_is_empty(access_app) -> None:
    with SessionLocal() as db:
        assert process_one(db, get_settings()) is False
