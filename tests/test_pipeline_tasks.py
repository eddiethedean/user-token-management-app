"""Regression tests for the in-process pipeline runtime."""

from __future__ import annotations

import asyncio
import threading
import time

from fastapi import BackgroundTasks
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.config import get_settings
from app.database import SessionLocal
from app.models import PipelineRun, PipelineRunStatus, User
from app.services import pipeline_tasks
from app.services.pipeline_runs import enqueue_run, snapshot_from_definition
from app.services.pipeline_tasks import process_pipeline_run_background, schedule_pipeline_run
from app.services.pipelines import save_pipeline


def test_schedule_pipeline_run_is_disabled_in_test_mode(monkeypatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "app_env", "test")
    background_tasks = BackgroundTasks()

    schedule_pipeline_run(background_tasks, settings, "run-test")

    assert background_tasks.tasks == []


def test_schedule_pipeline_run_attaches_in_process_task(monkeypatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "app_env", "development")
    background_tasks = BackgroundTasks()

    schedule_pipeline_run(background_tasks, settings, "run-development")

    assert len(background_tasks.tasks) == 1
    task = background_tasks.tasks[0]
    assert task.func is process_pipeline_run_background
    assert task.args == (settings, "run-development", None)


def test_background_runtime_processes_then_runs_janitor_before_stopping(monkeypatch) -> None:
    settings = get_settings()
    stop_event = threading.Event()
    processed: list[bool] = []
    janitors: list[bool] = []

    def process_pending(current_settings, current_stop_event):
        assert current_settings is settings
        assert current_stop_event is stop_event
        processed.append(True)
        return False

    def run_janitor(current_settings):
        assert current_settings is settings
        janitors.append(True)
        stop_event.set()

    monkeypatch.setattr(pipeline_tasks, "process_pending_pipeline_run_background", process_pending)
    monkeypatch.setattr(pipeline_tasks, "run_pipeline_janitor_background", run_janitor)
    monkeypatch.setattr(settings, "pipeline_background_poll_seconds", 0.5)

    asyncio.run(pipeline_tasks.run_background_runtime(settings, stop_event))

    assert processed == [True]
    assert janitors == [True]


def test_app_lifespan_starts_and_stops_pipeline_runtime(access_app, monkeypatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "app_env", "development")

    with TestClient(access_app):
        stop_event = access_app.state.pipeline_stop_event
        assert not stop_event.is_set()

    assert stop_event.is_set()


def test_app_lifespan_recovers_a_queued_run(access_app, demo_connections, monkeypatch) -> None:
    """A run queued before startup is claimed by the real lifecycle supervisor."""
    settings = get_settings()
    monkeypatch.setattr(settings, "app_env", "development")
    monkeypatch.setattr(settings, "pipeline_background_poll_seconds", 0.01)

    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.email == "admin@example.gov"))
        assert user is not None
        pipeline = save_pipeline(
            db,
            user=user,
            name="Lifecycle recovery",
            source_provider="mss",
            destination_provider="postgres",
            write_mode="append",
            available_providers={"mss", "postgres"},
            source_schema="ri.foundry.main.dataset.demo-operations",
            source_table="mission_orders.parquet",
            destination_schema="public",
            destination_table="mission_orders",
        )
        run = enqueue_run(
            db, user=user, pipeline=pipeline, snapshot=snapshot_from_definition(pipeline)
        )
        run_id = run.id

    with TestClient(access_app):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            with SessionLocal() as db:
                current = db.get(PipelineRun, run_id)
                if current is not None and current.status in {
                    PipelineRunStatus.SUCCEEDED.value,
                    PipelineRunStatus.FAILED.value,
                    PipelineRunStatus.CANCELLED.value,
                    PipelineRunStatus.FAILED_NEEDS_RECONCILIATION.value,
                }:
                    break
            time.sleep(0.01)
        else:
            raise AssertionError("lifecycle supervisor did not process queued run")

    assert current is not None
    assert current.status == PipelineRunStatus.SUCCEEDED.value
