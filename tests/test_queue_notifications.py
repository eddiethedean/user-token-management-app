"""Idle SQL suppression, transactional wakeups, and durable recovery deadlines."""

from __future__ import annotations

import asyncio
import threading
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from alembic import command
from sqlalchemy import create_engine, event, or_, select, text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.orm import sessionmaker

from app.config import Settings
from app.connectors.registry import ConnectorRegistry
from app.database import Base
from app.infrastructure import queue_notifications
from app.infrastructure.queue_notifications import QueueListener
from app.infrastructure.runtime import ExecutionRuntime
from app.models import DatabricksComputeAttempt, DatabricksPublication, PipelineRun, User, utcnow
from app.schema import alembic_config
from app.services import pipeline_tasks
from app.services.pipeline_wakeups import next_background_work_delay


def _runtime(engine, sessions, **updates) -> ExecutionRuntime:
    settings = Settings(
        _env_file=None,  # pyright: ignore[reportCallIssue]
        app_env="development",
        data_mover_mode="demo",
        database_url=engine.url.render_as_string(hide_password=False),
        **updates,
    )
    return ExecutionRuntime(settings, sessions, ConnectorRegistry(settings=settings))


def _owner(sessions) -> None:
    with sessions() as db:
        db.add(
            User(
                id="owner",
                email="owner@example.gov",
                email_original="owner@example.gov",
                status="active",
            )
        )
        db.commit()


def _run(sessions, run_id: str = "run", **values) -> None:
    with sessions() as db:
        db.add(PipelineRun(id=run_id, user_id="owner", definition_snapshot_json="{}", **values))
        db.commit()


@pytest.fixture()
def notification_store(postgresql):
    dsn = postgresql.dsn()
    engine = create_engine(
        URL.create(
            "postgresql+psycopg",
            username=dsn["user"],
            host=dsn["host"],
            port=dsn["port"],
            database=dsn["database"],
        )
    )
    command.upgrade(alembic_config(engine.url.render_as_string(hide_password=False)), "head")
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    _owner(sessions)
    try:
        yield engine, sessions
    finally:
        engine.dispose()


@pytest.mark.postgres
def test_queue_notifications_only_arrive_after_commit(notification_store) -> None:
    engine, sessions = notification_store
    listener = QueueListener(str(engine.url), threading.Event())
    try:
        assert listener.connect()
        with sessions() as db:
            db.add(PipelineRun(id="rolled-back", user_id="owner", definition_snapshot_json="{}"))
            db.flush()
            assert not listener.wait(0.05)
            db.rollback()
        assert not listener.wait(0.05)
        _run(sessions)
        assert listener.wait(2)
        assert not listener.wait(0.05)
        with sessions() as db:
            db.execute(
                text(
                    "UPDATE pipeline_runs SET heartbeat_at = CURRENT_TIMESTAMP, lease_expires_at = CURRENT_TIMESTAMP"
                )
            )
            db.commit()
        assert not listener.wait(0.05)
        with sessions() as db:
            db.execute(text("UPDATE pipeline_runs SET status = 'succeeded'"))
            db.commit()
        assert listener.wait(2)
        with sessions() as db:
            db.execute(text("DELETE FROM pipeline_runs"))
            db.commit()
        assert not listener.wait(0.05)  # Terminal retention does not wake queue workers.
    finally:
        listener.close()


@pytest.mark.postgres
def test_listener_reconnect_can_recover_work_committed_while_disconnected(
    notification_store, monkeypatch
) -> None:
    engine, sessions = notification_store
    runtime = _runtime(engine, sessions)
    listener = QueueListener(str(engine.url), runtime.stop_event)
    try:
        assert listener.connect()
        assert listener.connection is not None
        with engine.begin() as db:
            db.execute(
                text("SELECT pg_terminate_backend(:pid)"),
                {"pid": listener.connection.info.backend_pid},
            )
        assert listener.wait(2)
        assert not listener.connected
        _run(sessions)
        assert not listener.connect()
        current_time = queue_notifications.monotonic
        monkeypatch.setattr(queue_notifications, "monotonic", lambda: current_time() + 61)
        assert listener.connect()
        assert next_background_work_delay(runtime) == 0.5
    finally:
        listener.close()


@pytest.mark.postgres
@pytest.mark.parametrize("recover_expired_lease", [False, True])
def test_idle_supervisor_stops_reading_queues_and_wakes_for_other_connection(
    notification_store, monkeypatch, recover_expired_lease
) -> None:
    engine, sessions = notification_store
    runtime = _runtime(engine, sessions)
    # This row predates LISTEN and must be recovered by the initial inspection.
    if recover_expired_lease:
        _run(
            sessions,
            "startup",
            status="validating",
            lease_expires_at=utcnow() + timedelta(seconds=0.3),
        )
    else:
        _run(sessions, "startup")
    waiting = threading.Event()
    processed = threading.Event()
    reads: list[str] = []
    original_wait = QueueListener.wait

    def observe_reads(conn, cursor, statement, parameters, context, executemany):
        if statement.startswith("SELECT") and "pipeline_runs" in statement:
            reads.append(statement)

    def wait(listener, timeout):
        if timeout > 30:  # No work: only the hourly janitor deadline remains.
            waiting.set()
        return original_wait(listener, timeout)

    def process(current_runtime):
        with sessions() as db:
            run = db.scalar(
                select(PipelineRun).where(
                    or_(
                        PipelineRun.status == "queued",
                        (PipelineRun.status == "validating")
                        & (PipelineRun.lease_expires_at <= utcnow()),
                    )
                )
            )
            if run is None:
                return False
            run.status = "succeeded"
            db.commit()
            if run.id == "external":
                processed.set()
            return True

    monkeypatch.setattr(QueueListener, "wait", wait)
    monkeypatch.setattr(pipeline_tasks, "process_pending_pipeline_run_background", process)
    monkeypatch.setattr(
        pipeline_tasks,
        "process_pending_databricks_compute_attempt_background",
        Mock(return_value=False),
    )
    monkeypatch.setattr(
        pipeline_tasks,
        "process_pending_databricks_publication_recovery_background",
        Mock(return_value=False),
    )
    monkeypatch.setattr(pipeline_tasks, "run_pipeline_janitor_background", Mock())
    event.listen(engine, "after_cursor_execute", observe_reads)

    async def scenario() -> None:
        task = asyncio.create_task(pipeline_tasks.run_background_runtime(runtime=runtime))
        try:
            assert await asyncio.to_thread(waiting.wait, 3)
            await asyncio.sleep(0.1)  # Drain the startup completion notification.
            before = len(reads)
            await asyncio.sleep(1.2)  # Cross the listener's local shutdown/socket check.
            assert len(reads) == before
            assert next_background_work_delay(runtime) is None
            _run(sessions, "external")
            assert await asyncio.to_thread(processed.wait, 3)
        finally:
            runtime.begin_shutdown()
            await asyncio.wait_for(task, timeout=2)

    try:
        asyncio.run(scenario())
        with sessions() as db:
            assert db.get(PipelineRun, "startup").status == "succeeded"
            assert db.get(PipelineRun, "external").status == "succeeded"
    finally:
        event.remove(engine, "after_cursor_execute", observe_reads)


@pytest.mark.postgres
def test_notification_migration_downgrades_and_upgrades(notification_store) -> None:
    engine, sessions = notification_store
    config = alembic_config(engine.url.render_as_string(hide_password=False))
    _run(sessions)
    command.downgrade(config, "0026_token_expiry")
    with engine.connect() as db:
        assert (
            db.scalar(text("SELECT count(*) FROM pg_trigger WHERE tgname LIKE 'dm_queue_%'")) == 0
        )
    command.upgrade(config, "head")
    with sessions() as db:
        assert db.get(PipelineRun, "run") is not None


@pytest.mark.postgres
def test_compute_publication_and_owner_changes_wake_listeners(notification_store) -> None:
    engine, sessions = notification_store
    _run(sessions, status="succeeded")
    listener = QueueListener(str(engine.url), threading.Event())
    try:
        assert listener.connect()
        with sessions() as db:
            db.add(
                DatabricksComputeAttempt(
                    user_id="owner",
                    workspace_id="workspace",
                    compute_key="key",
                    active_key="key",
                    compute_kind="warehouse",
                    compute_id="compute",
                    connection_generation="rev",
                    deadline_at=utcnow() + timedelta(minutes=10),
                    next_poll_at=utcnow() + timedelta(seconds=5),
                )
            )
            db.commit()
        assert listener.wait(2)
        with sessions() as db:
            db.execute(
                text("UPDATE databricks_compute_attempts SET status = 'ready', active_key = NULL")
            )
            db.commit()
        assert listener.wait(2)
        with sessions() as db:
            db.add(
                DatabricksPublication(
                    run_id="run",
                    user_id="owner",
                    workspace_id="workspace",
                    connection_generation="rev",
                    target_key="target",
                    target_locator_json="{}",
                    write_mode="append",
                    state="uncertain",
                )
            )
            db.commit()
        assert listener.wait(2)
        with sessions() as db:
            db.execute(
                text("UPDATE databricks_publications SET recovery_expires_at = CURRENT_TIMESTAMP")
            )
            db.commit()
        assert listener.wait(2)
        with sessions() as db:
            db.execute(text("UPDATE users SET status = 'disabled'"))
            db.commit()
        assert listener.wait(2)
    finally:
        listener.close()


@pytest.mark.postgres
@pytest.mark.parametrize("notifications_enabled", [False, True])
def test_disabled_or_unavailable_listener_uses_bounded_fallback(
    notification_store, monkeypatch, notifications_enabled
) -> None:
    engine, sessions = notification_store
    runtime = _runtime(engine, sessions, pipeline_queue_notifications_enabled=notifications_enabled)
    pipeline = Mock(return_value=False)
    intervals: list[float] = []

    def wait(timeout):
        intervals.append(timeout)
        runtime.stop_event.set()
        return True

    connect = Mock(return_value=False)
    monkeypatch.setattr(QueueListener, "connect", connect)
    monkeypatch.setattr(runtime.stop_event, "wait", wait)
    monkeypatch.setattr(pipeline_tasks, "process_pending_pipeline_run_background", pipeline)
    monkeypatch.setattr(
        pipeline_tasks,
        "process_pending_databricks_compute_attempt_background",
        Mock(return_value=False),
    )
    monkeypatch.setattr(
        pipeline_tasks,
        "process_pending_databricks_publication_recovery_background",
        Mock(return_value=False),
    )
    monkeypatch.setattr(pipeline_tasks, "run_pipeline_janitor_background", Mock())
    asyncio.run(pipeline_tasks.run_background_runtime(runtime=runtime))
    assert intervals == [60.0]
    assert connect.call_count == int(notifications_enabled)
    pipeline.assert_called_once_with(runtime)


@pytest.mark.parametrize("retry_seconds", [0.5, 60.0])
def test_repeated_listener_drops_enforce_retry_delay_before_reconnecting(
    monkeypatch, retry_seconds
) -> None:
    engine = SimpleNamespace(url=make_url("postgresql+psycopg://localhost/notification-test"))
    runtime = _runtime(engine, Mock(), pipeline_background_poll_seconds=retry_seconds)
    clock = [100.0]
    attempts: list[float] = []
    waits: list[float] = []
    drops: list[bool] = []
    pipeline = Mock(return_value=False)

    def connect(*args, **kwargs):
        attempts.append(clock[0])
        return Mock()

    def disconnect(*args):
        drops.append(True)
        # Ensure a regression terminates even if it bypasses fallback entirely.
        if len(drops) > 3:
            runtime.stop_event.set()
        raise OSError("listener connection dropped")

    def fallback_wait(timeout):
        waits.append(timeout)
        clock[0] += timeout
        if len(waits) == 3:
            runtime.stop_event.set()
        return runtime.stop_event.is_set()

    monkeypatch.setattr(queue_notifications, "monotonic", lambda: clock[0])
    monkeypatch.setattr("psycopg.connect", connect)
    monkeypatch.setattr("app.infrastructure.queue_notifications.select.select", disconnect)
    monkeypatch.setattr(runtime.stop_event, "wait", fallback_wait)
    monkeypatch.setattr(pipeline_tasks, "process_pending_pipeline_run_background", pipeline)
    monkeypatch.setattr(
        pipeline_tasks,
        "process_pending_databricks_compute_attempt_background",
        Mock(return_value=False),
    )
    monkeypatch.setattr(
        pipeline_tasks,
        "process_pending_databricks_publication_recovery_background",
        Mock(return_value=False),
    )
    monkeypatch.setattr(pipeline_tasks, "run_pipeline_janitor_background", Mock())
    monkeypatch.setattr(
        "app.services.pipeline_wakeups.next_background_work_delay", Mock(return_value=None)
    )

    asyncio.run(pipeline_tasks.run_background_runtime(runtime=runtime))

    assert attempts == [100.0, 100.0 + retry_seconds, 100.0 + 2 * retry_seconds]
    assert waits == [retry_seconds] * 3
    assert len(drops) == 3
    # One reconnect inspection and one immediate fallback inspection per drop;
    # further recovery scans must wait for the fallback interval.
    assert pipeline.call_count == 6
    assert runtime.lifetime.is_idle()


@pytest.fixture()
def deadline_store(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'deadlines.db'}")
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    _owner(sessions)
    try:
        yield engine, sessions
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    "kind", ["queued", "lease", "compute", "publication", "committed_publication"]
)
def test_durable_deadline_waits_for_retry_or_lease_expiry(
    deadline_store, monkeypatch, kind
) -> None:
    engine, sessions = deadline_store
    now = datetime(2026, 10, 22, 12)
    monkeypatch.setattr("app.services.pipeline_wakeups.utcnow", lambda: now)
    future = now + timedelta(seconds=20)
    later = now + timedelta(seconds=40)
    if kind == "queued":
        _run(sessions, next_attempt_at=future)
        expected = 20
    elif kind == "lease":
        _run(sessions, status="loading", lease_expires_at=later)
        expected = 40
    elif kind == "compute":
        with sessions() as db:
            db.add(
                DatabricksComputeAttempt(
                    user_id="owner",
                    workspace_id="workspace",
                    compute_key="key",
                    active_key="key",
                    compute_kind="warehouse",
                    compute_id="compute",
                    connection_generation="rev",
                    deadline_at=later,
                    next_poll_at=future,
                    lease_expires_at=later,
                )
            )
            db.commit()
        expected = 40
    else:
        _run(sessions, status="loading", lease_expires_at=later)
        with sessions() as db:
            db.add(
                DatabricksPublication(
                    run_id="run",
                    user_id="owner",
                    workspace_id="workspace",
                    connection_generation="rev",
                    target_key="target",
                    target_locator_json="{}",
                    write_mode="append",
                    state=("committed" if kind == "committed_publication" else "uncertain"),
                    recovery_expires_at=future,
                )
            )
            db.commit()
        expected = 40
    assert next_background_work_delay(_runtime(engine, sessions)) == expected


def test_empty_and_inactive_owner_queues_have_no_work_timer(deadline_store) -> None:
    engine, sessions = deadline_store
    runtime = _runtime(engine, sessions)
    assert next_background_work_delay(runtime) is None
    _run(sessions)
    assert next_background_work_delay(runtime) == 0.5
    with sessions() as db:
        owner = db.get(User, "owner")
        assert owner is not None
        owner.status = "disabled"
        db.commit()
    assert next_background_work_delay(runtime) is None


def test_listener_connection_failure_redacts_credentials_and_allows_fallback(
    monkeypatch, caplog
) -> None:
    secret = "private-password-should-not-be-logged"
    monkeypatch.setattr("psycopg.connect", Mock(side_effect=RuntimeError(secret)))
    listener = QueueListener(f"postgresql+psycopg://user:{secret}@localhost/db", threading.Event())
    assert not listener.connect()
    assert not listener.connected
    assert "using recovery polling" in caplog.text
    assert secret not in caplog.text
    assert not listener.wait(0.01)
