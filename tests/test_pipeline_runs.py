"""Durable run state machine, worker execution, and retention janitor."""

from __future__ import annotations

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
    request_cancel,
    snapshot_from_definition,
)
from app.worker import process_one
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
        f"/pipeline/{pipeline_id}/runs",
        data={"csrf_token": csrf_from(client.get("/pipeline").text)},
        headers={"HX-Request": "true", "HX-Target": "pipeline-run-monitor", "Accept": "text/html"},
    )
    assert queued.status_code in {200, 202}
    assert "pipeline-run-monitor" in queued.text
    assert "succeeded" in queued.text or "queued" in queued.text or "extracting" in queued.text

    with SessionLocal() as db:
        run = db.scalar(
            select(PipelineRun).where(PipelineRun.pipeline_definition_id == pipeline_id)
        )
        assert run is not None
        assert run.status == PipelineRunStatus.SUCCEEDED.value
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


def test_janitor_purges_expired_events_and_terminal_runs(access_app, tmp_path) -> None:
    settings = get_settings()
    original_events = settings.pipeline_event_retention_days
    original_runs = settings.pipeline_run_retention_days
    original_spool = settings.pipeline_spool_root
    settings.pipeline_event_retention_days = 1
    settings.pipeline_run_retention_days = 1
    settings.pipeline_spool_root = str(tmp_path)
    stale = utcnow() - timedelta(days=40)
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
        assert db.get(PipelineRun, run_id) is None
    settings.pipeline_event_retention_days = original_events
    settings.pipeline_run_retention_days = original_runs
    settings.pipeline_spool_root = original_spool


def test_process_one_is_idle_when_queue_is_empty(access_app) -> None:
    with SessionLocal() as db:
        assert process_one(db, get_settings()) is False
