"""Regression tests for the in-process pipeline runtime."""

from __future__ import annotations

import asyncio
import threading
import time
from contextlib import nullcontext
from types import SimpleNamespace
from typing import cast
from unittest.mock import Mock, patch

import pytest
from fastapi import BackgroundTasks
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from app.config import Settings, get_settings
from app.connectors.registry import ConnectorRegistry, current_registry, load_builtin_connectors
from app.database import SessionLocal
from app.infrastructure.runtime import ExecutionRuntime
from app.models import PipelineRun, PipelineRunStatus, User
from app.services import pipeline_tasks
from app.services.pipeline_runs import enqueue_run, snapshot_from_definition
from app.services.pipeline_tasks import (
    drain_background_runtime,
    process_pipeline_run_background,
    schedule_pipeline_run,
)
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
    runtime = ExecutionRuntime(settings, SessionLocal, current_registry())

    schedule_pipeline_run(background_tasks, settings, "run-development", runtime=runtime)

    assert len(background_tasks.tasks) == 1
    task = background_tasks.tasks[0]
    assert task.func is process_pipeline_run_background
    assert task.args == (runtime, "run-development")


def test_legacy_owner_rejects_cached_settings_before_opening_resources(monkeypatch) -> None:
    settings = SimpleNamespace(database_url="sqlite:///owner-a.db")
    factory = Mock(return_value=nullcontext())
    monkeypatch.setattr(pipeline_tasks, "_LEGACY_RUNTIME", None)
    monkeypatch.setattr("app.database.legacy_database_url", lambda: "sqlite:///owner-b.db")
    monkeypatch.setattr("app.database.legacy_session_factory", factory)
    monkeypatch.setattr("app.connectors.registry.legacy_registry_settings", lambda: None)

    with pytest.raises(ValueError, match="global database"):
        pipeline_tasks.process_pending_pipeline_run_background(cast(Settings, settings))
    factory.assert_not_called()


def test_legacy_owner_rejects_foreign_session_binding(monkeypatch, tmp_path) -> None:
    engine_a = create_engine(f"sqlite:///{tmp_path / 'owner-a.db'}")
    engine_b = create_engine(f"sqlite:///{tmp_path / 'owner-b.db'}")
    settings = get_settings().model_copy(update={"database_url": str(engine_a.url)})
    monkeypatch.setattr(pipeline_tasks, "_LEGACY_RUNTIME", None)
    monkeypatch.setattr("app.database.legacy_database_engine", lambda: engine_a)
    monkeypatch.setattr("app.database.legacy_database_url", lambda: engine_a.url)
    monkeypatch.setattr("app.database.legacy_session_engine", lambda: engine_b)
    monkeypatch.setattr("app.database.legacy_session_factory", lambda: sessionmaker(bind=engine_b))
    monkeypatch.setattr("app.connectors.registry.legacy_registry_settings", lambda: settings)

    try:
        with pytest.raises(ValueError, match="session factory"):
            pipeline_tasks.process_pending_pipeline_run_background(settings)
    finally:
        engine_a.dispose()
        engine_b.dispose()


def test_legacy_owner_accepts_password_and_query_url(monkeypatch) -> None:
    url = make_url("postgresql+psycopg://review:p%40ss@localhost/review?sslmode=require")
    engine = create_engine("sqlite:///:memory:")
    settings = get_settings().model_copy(
        update={"database_url": url.render_as_string(hide_password=False)}
    )
    factory = sessionmaker(bind=engine)
    registry = current_registry()
    monkeypatch.setattr(pipeline_tasks, "_LEGACY_RUNTIME", None)
    monkeypatch.setattr("app.database.legacy_database_engine", lambda: engine)
    monkeypatch.setattr("app.database.legacy_database_url", lambda: url)
    monkeypatch.setattr("app.database.legacy_session_engine", lambda: engine)
    monkeypatch.setattr("app.database.legacy_session_factory", lambda: factory)
    monkeypatch.setattr("app.connectors.registry.legacy_registry", lambda: registry)
    monkeypatch.setattr("app.connectors.registry.legacy_registry_settings", lambda: settings)

    runtime = None
    try:
        runtime = pipeline_tasks._legacy_runtime(settings, None)
        assert runtime.settings is settings
        assert runtime.sessions().get_bind() is engine
    finally:
        if runtime is not None:
            pipeline_tasks.release_legacy_runtime(runtime)
        engine.dispose()


def test_cached_legacy_owner_rejects_changed_resource_facts(monkeypatch, tmp_path) -> None:
    engine_a = create_engine(f"sqlite:///{tmp_path / 'owner-a.db'}")
    engine_b = create_engine(f"sqlite:///{tmp_path / 'owner-b.db'}")
    settings = get_settings().model_copy(update={"database_url": str(engine_a.url)})
    factory = sessionmaker(bind=engine_a)
    registry = current_registry()
    state = {"engine": engine_a, "factory": factory}
    monkeypatch.setattr(pipeline_tasks, "_LEGACY_RUNTIME", None)
    monkeypatch.setattr("app.database.legacy_database_engine", lambda: state["engine"])
    monkeypatch.setattr("app.database.legacy_database_url", lambda: state["engine"].url)
    monkeypatch.setattr("app.database.legacy_session_engine", lambda: state["engine"])
    monkeypatch.setattr("app.database.legacy_session_factory", lambda: state["factory"])
    monkeypatch.setattr("app.connectors.registry.legacy_registry", lambda: registry)
    monkeypatch.setattr("app.connectors.registry.legacy_registry_settings", lambda: settings)

    runtime = pipeline_tasks._legacy_runtime(settings, None)
    try:
        settings.database_url = str(engine_b.url)
        state.update(engine=engine_b, factory=sessionmaker(bind=engine_b))
        with pytest.raises(ValueError, match="resources changed"):
            pipeline_tasks._legacy_runtime(settings, None)
    finally:
        pipeline_tasks.release_legacy_runtime(runtime)
        engine_a.dispose()
        engine_b.dispose()


def test_drain_records_completion_callback_cancellation() -> None:
    runtime = ExecutionRuntime(get_settings(), SessionLocal, current_registry())

    async def scenario() -> None:
        caller = asyncio.current_task()
        assert caller is not None
        finished = asyncio.Event()

        async def supervisor() -> None:
            await finished.wait()
            raise RuntimeError("supervisor failed")

        task = asyncio.create_task(supervisor())
        task.add_done_callback(lambda _: caller.cancel())
        finished.set()
        outcome = await drain_background_runtime(task, runtime)
        assert outcome.cancelled
        assert isinstance(outcome.supervisor_error, RuntimeError)

    asyncio.run(scenario())


def test_standalone_cancellation_during_final_idle_wait_is_propagated(monkeypatch) -> None:
    settings = get_settings().model_copy(update={"app_env": "test"})
    runtime = ExecutionRuntime(settings, SessionLocal, current_registry())
    entered = asyncio.Event()

    async def wait_for_idle(_runtime) -> pipeline_tasks._CompletionResult:
        entered.set()
        try:
            await asyncio.Future()
        except asyncio.CancelledError as exc:
            return pipeline_tasks._CompletionResult(caller_cancellation=exc)
        return pipeline_tasks._CompletionResult()

    def process_pending(_runtime) -> bool:
        runtime.stop_event.set()
        return False

    monkeypatch.setattr(pipeline_tasks, "_legacy_runtime", lambda *_args: runtime)
    monkeypatch.setattr(pipeline_tasks, "_wait_for_runtime_idle", wait_for_idle)
    monkeypatch.setattr(pipeline_tasks, "process_pending_pipeline_run_background", process_pending)
    monkeypatch.setattr(pipeline_tasks, "_LEGACY_RUNTIME", runtime)
    monkeypatch.setattr(pipeline_tasks, "_LEGACY_OWNER_KIND", None)
    monkeypatch.setattr(pipeline_tasks, "_LEGACY_SUPERVISOR_TASK", None)

    async def scenario() -> None:
        task = asyncio.create_task(
            pipeline_tasks.run_background_runtime(settings, runtime.stop_event)
        )
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(scenario())
    assert pipeline_tasks._LEGACY_RUNTIME is None


@pytest.mark.parametrize("cancel_body", [False, True])
def test_standalone_idle_submission_failure_retains_owner_and_body_error(
    monkeypatch, cancel_body
) -> None:
    settings = get_settings().model_copy(update={"app_env": "test"})
    runtime = ExecutionRuntime(settings, SessionLocal, current_registry())
    lease = runtime.try_admit_pipeline_operation()
    assert lease is not None
    body_error = (
        asyncio.CancelledError("supervisor cancelled")
        if cancel_body
        else ValueError("supervisor failed")
    )
    drain_error = RuntimeError("executor rejected idle submission")
    original_submit = pipeline_tasks._submit_sync

    def submit(function, *args):
        if function == runtime.wait_until_pipeline_idle:
            raise drain_error
        return original_submit(function, *args)

    def process_pending(_runtime) -> bool:
        raise body_error

    monkeypatch.setattr(pipeline_tasks, "_legacy_runtime", lambda *_args: runtime)
    monkeypatch.setattr(pipeline_tasks, "_submit_sync", submit)
    monkeypatch.setattr(pipeline_tasks, "process_pending_pipeline_run_background", process_pending)
    monkeypatch.setattr(pipeline_tasks, "_LEGACY_RUNTIME", runtime)
    monkeypatch.setattr(pipeline_tasks, "_LEGACY_OWNER_KIND", None)
    monkeypatch.setattr(pipeline_tasks, "_LEGACY_SUPERVISOR_TASK", None)

    async def scenario() -> None:
        task = asyncio.create_task(
            pipeline_tasks.run_background_runtime(settings, runtime.stop_event)
        )
        with pytest.raises(type(body_error)) as raised:
            await task
        assert raised.value is body_error
        assert vars(raised.value)["shutdown_secondary_errors"] == (drain_error,)
        assert pipeline_tasks._LEGACY_RUNTIME is runtime
        assert pipeline_tasks._LEGACY_OWNER_KIND == "closed"
        assert runtime.stop_event.is_set()
        assert not runtime.lifetime.is_idle()

    try:
        asyncio.run(scenario())
    finally:
        lease.release()
        pipeline_tasks.release_legacy_runtime(runtime)


def test_legacy_startup_retains_owner_when_idle_drain_fails(monkeypatch) -> None:
    runtime = ExecutionRuntime(get_settings(), SessionLocal, current_registry())
    drain_error = RuntimeError("idle drain failed")
    monkeypatch.setattr(pipeline_tasks, "_LEGACY_RUNTIME", runtime)
    monkeypatch.setattr(pipeline_tasks, "_LEGACY_SUPERVISOR_TASK", None)
    monkeypatch.setattr(pipeline_tasks, "_LEGACY_OWNER_KIND", "temporary")
    monkeypatch.setattr(pipeline_tasks, "_LEGACY_TRANSITIONING", False)
    monkeypatch.setattr(pipeline_tasks, "_LEGACY_TRANSITION_TOKEN", None)

    async def failed_drain(*_args):
        return pipeline_tasks.RuntimeDrainOutcome(drain_error=drain_error)

    monkeypatch.setattr(pipeline_tasks, "drain_background_runtime", failed_drain)

    async def scenario() -> None:
        with pytest.raises(RuntimeError) as raised:
            await pipeline_tasks.begin_legacy_startup()
        assert raised.value is drain_error

    try:
        asyncio.run(scenario())
        assert pipeline_tasks._LEGACY_RUNTIME is runtime
        assert pipeline_tasks._LEGACY_OWNER_KIND == "closed"
        assert not pipeline_tasks._LEGACY_TRANSITIONING
    finally:
        pipeline_tasks.release_legacy_runtime(runtime)


def test_startup_reservation_blocks_compatibility_owner(monkeypatch) -> None:
    monkeypatch.setattr(pipeline_tasks, "_LEGACY_RUNTIME", None)
    monkeypatch.setattr(pipeline_tasks, "_LEGACY_TRANSITIONING", False)
    monkeypatch.setattr(pipeline_tasks, "_LEGACY_TRANSITION_TOKEN", None)
    facts = Mock(side_effect=AssertionError("resource facts must not be opened during reservation"))
    monkeypatch.setattr(pipeline_tasks, "_legacy_resource_facts", facts)

    async def scenario() -> None:
        token = await pipeline_tasks.begin_legacy_startup()
        try:
            with pytest.raises(RuntimeError, match="transition"):
                pipeline_tasks._legacy_runtime(get_settings(), None)
            facts.assert_not_called()
        finally:
            pipeline_tasks.abort_legacy_startup(token)

    asyncio.run(scenario())


def test_cached_legacy_owner_rejects_in_place_registry_clear(monkeypatch, tmp_path) -> None:
    from app.connectors.csv_source import CsvSourceConnector
    from app.connectors.registry import ConnectorRegistry

    engine = create_engine(f"sqlite:///{tmp_path / 'registry.db'}")
    settings = get_settings().model_copy(update={"database_url": str(engine.url)})
    factory = sessionmaker(bind=engine)
    registry = ConnectorRegistry(settings=settings)
    registry.register(CsvSourceConnector)
    monkeypatch.setattr(pipeline_tasks, "_LEGACY_RUNTIME", None)
    monkeypatch.setattr("app.database.legacy_database_engine", lambda: engine)
    monkeypatch.setattr("app.database.legacy_database_url", lambda: engine.url)
    monkeypatch.setattr("app.database.legacy_session_engine", lambda: engine)
    monkeypatch.setattr("app.database.legacy_session_factory", lambda: factory)
    monkeypatch.setattr("app.connectors.registry.legacy_registry", lambda: registry)
    monkeypatch.setattr("app.connectors.registry.legacy_registry_settings", lambda: settings)
    runtime = pipeline_tasks._legacy_runtime(settings, None)
    try:
        assert len(runtime.connectors.specifications()) == 1
        registry.clear()
        with pytest.raises(ValueError, match="resources changed"):
            pipeline_tasks._legacy_runtime(settings, None)
        assert len(runtime.connectors.specifications()) == 1
    finally:
        pipeline_tasks.release_legacy_runtime(runtime)
        engine.dispose()


def test_sequential_legacy_supervisor_generations_restart(monkeypatch) -> None:
    engine = create_engine("sqlite:///:memory:")
    settings = get_settings().model_copy(update={"database_url": str(engine.url)})
    factory = sessionmaker(bind=engine)
    registry = current_registry()
    monkeypatch.setattr(pipeline_tasks, "_LEGACY_RUNTIME", None)
    monkeypatch.setattr("app.database.legacy_database_engine", lambda: engine)
    monkeypatch.setattr("app.database.legacy_database_url", lambda: engine.url)
    monkeypatch.setattr("app.database.legacy_session_engine", lambda: engine)
    monkeypatch.setattr("app.database.legacy_session_factory", lambda: factory)
    monkeypatch.setattr("app.connectors.registry.legacy_registry", lambda: registry)
    monkeypatch.setattr("app.connectors.registry.legacy_registry_settings", lambda: settings)

    def stop(runtime):
        runtime.begin_shutdown()
        return False

    monkeypatch.setattr(pipeline_tasks, "process_pending_pipeline_run_background", stop)
    try:
        asyncio.run(pipeline_tasks.run_background_runtime(settings, threading.Event()))
        asyncio.run(pipeline_tasks.run_background_runtime(settings, threading.Event()))
        assert pipeline_tasks._LEGACY_RUNTIME is None
    finally:
        engine.dispose()


def test_runtime_schedule_forwards_only_the_owned_runtime(monkeypatch) -> None:
    settings = get_settings().model_copy(update={"app_env": "development"})
    runtime = ExecutionRuntime(settings, SessionLocal, current_registry())
    background_tasks = BackgroundTasks()
    observed: list[object] = []

    def process(current_runtime, run_id):
        observed.extend((current_runtime, run_id))

    monkeypatch.setattr(pipeline_tasks, "process_pipeline_run_background", process)
    schedule_pipeline_run(
        background_tasks,
        settings,
        "run-matching-event",
        runtime.stop_event,
        runtime,
    )

    asyncio.run(background_tasks())
    assert observed == [runtime, "run-matching-event"]


def test_background_runtime_processes_then_runs_janitor_before_stopping(monkeypatch) -> None:
    settings = get_settings()
    stop_event = threading.Event()
    runtime = ExecutionRuntime(settings, SessionLocal, current_registry(), stop_event)
    processed: list[bool] = []
    janitors: list[bool] = []

    def process_pending(current_runtime):
        assert current_runtime is runtime
        processed.append(True)
        return False

    def run_janitor(current_runtime):
        assert current_runtime is runtime
        janitors.append(True)
        stop_event.set()

    monkeypatch.setattr(pipeline_tasks, "process_pending_pipeline_run_background", process_pending)
    monkeypatch.setattr(pipeline_tasks, "run_pipeline_janitor_background", run_janitor)
    monkeypatch.setattr(settings, "pipeline_background_poll_seconds", 0.5)

    asyncio.run(pipeline_tasks.run_background_runtime(runtime=runtime))

    assert processed == [True]
    assert janitors == [True]


def test_cancellation_after_thread_completion_still_signals_shutdown() -> None:
    runtime = ExecutionRuntime(get_settings(), SessionLocal, current_registry())

    async def scenario() -> None:
        supervisor = asyncio.current_task()
        assert supervisor is not None
        original_submit = pipeline_tasks._submit_sync

        def submit(function, *args):
            completion = original_submit(function, *args)
            completion.add_done_callback(lambda _: supervisor.cancel())
            return completion

        with patch.object(pipeline_tasks, "_submit_sync", submit):
            with pytest.raises(asyncio.CancelledError):
                await pipeline_tasks._drainable_thread_call(runtime, lambda: False)

    asyncio.run(scenario())
    assert runtime.stop_event.is_set()


def test_cancellation_preserves_worker_failure_evidence() -> None:
    runtime = ExecutionRuntime(get_settings(), SessionLocal, current_registry())
    entered = threading.Event()
    release = threading.Event()

    def worker() -> bool:
        entered.set()
        release.wait(2)
        raise RuntimeError("worker failed during cancellation")

    async def scenario() -> None:
        operation = asyncio.create_task(pipeline_tasks._drainable_thread_call(runtime, worker))
        assert await asyncio.to_thread(entered.wait, 2)
        operation.cancel()
        assert await asyncio.to_thread(runtime.stop_event.wait, 2)
        release.set()
        with pytest.raises(asyncio.CancelledError) as error:
            await operation
        assert isinstance(error.value.__cause__, RuntimeError)

    asyncio.run(scenario())


def test_legacy_owner_transitions_before_startup(monkeypatch) -> None:
    engine = create_engine("sqlite:///:memory:")
    settings = get_settings().model_copy(update={"database_url": str(engine.url)})
    factory = sessionmaker(bind=engine)
    registry = current_registry()
    monkeypatch.setattr(pipeline_tasks, "_LEGACY_RUNTIME", None)
    monkeypatch.setattr("app.database.legacy_database_engine", lambda: engine)
    monkeypatch.setattr("app.database.legacy_database_url", lambda: engine.url)
    monkeypatch.setattr("app.database.legacy_session_engine", lambda: engine)
    monkeypatch.setattr("app.database.legacy_session_factory", lambda: factory)
    monkeypatch.setattr("app.connectors.registry.legacy_registry", lambda: registry)
    monkeypatch.setattr("app.connectors.registry.legacy_registry_settings", lambda: settings)
    old_runtime = pipeline_tasks._legacy_runtime(settings, None)
    replacement = ExecutionRuntime(settings, factory, registry)

    async def scenario() -> None:
        await pipeline_tasks.retire_legacy_runtime()

    try:
        asyncio.run(scenario())
        pipeline_tasks.install_legacy_runtime(replacement)
        assert pipeline_tasks._LEGACY_RUNTIME is replacement
    finally:
        pipeline_tasks.release_legacy_runtime(replacement)
        pipeline_tasks.release_legacy_runtime(old_runtime)
        engine.dispose()


def test_legacy_publication_rejects_foreign_proposed_resources(monkeypatch, tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'owner.db'}")
    foreign_engine = create_engine(f"sqlite:///{tmp_path / 'foreign.db'}")
    settings = get_settings().model_copy(update={"database_url": str(engine.url)})
    factory = sessionmaker(bind=engine)
    registry = load_builtin_connectors(
        demo=True, registry=ConnectorRegistry(settings=settings), settings=settings
    )
    runtime = ExecutionRuntime(settings, sessionmaker(bind=foreign_engine), ConnectorRegistry())

    class ForeignRoutingSession(factory.class_):
        def get_bind(self, mapper=None, clause=None, bind=None, **kwargs):
            return foreign_engine

    foreign_routing_runtime = ExecutionRuntime(
        settings,
        sessionmaker(bind=engine, class_=ForeignRoutingSession),
        registry,
    )
    monkeypatch.setattr(pipeline_tasks, "_LEGACY_RUNTIME", None)
    monkeypatch.setattr(pipeline_tasks, "_LEGACY_OWNER_KIND", None)
    monkeypatch.setattr(pipeline_tasks, "_LEGACY_SUPERVISOR_TASK", None)
    monkeypatch.setattr(pipeline_tasks, "_LEGACY_TRANSITIONING", False)
    monkeypatch.setattr(pipeline_tasks, "_LEGACY_TRANSITION_TOKEN", None)
    monkeypatch.setattr("app.database.legacy_database_engine", lambda: engine)
    monkeypatch.setattr("app.database.legacy_database_url", lambda: engine.url)
    monkeypatch.setattr("app.database.legacy_session_engine", lambda: engine)
    monkeypatch.setattr("app.database.legacy_session_factory", lambda: factory)
    monkeypatch.setattr("app.connectors.registry.legacy_registry", lambda: registry)
    monkeypatch.setattr("app.connectors.registry.legacy_registry_settings", lambda: settings)

    try:
        with pytest.raises(ValueError, match="do not match compatibility ownership"):
            pipeline_tasks.install_legacy_runtime(runtime)
        assert pipeline_tasks._LEGACY_RUNTIME is None
        assert pipeline_tasks._LEGACY_OWNER_KIND is None
        assert not hasattr(runtime, "_legacy_resource_facts")

        foreign_registry_runtime = ExecutionRuntime(settings, factory, ConnectorRegistry())
        with pytest.raises(ValueError, match="do not match compatibility ownership"):
            pipeline_tasks.install_legacy_runtime(foreign_registry_runtime)
        assert pipeline_tasks._LEGACY_RUNTIME is None
        assert not hasattr(foreign_registry_runtime, "_legacy_resource_facts")

        with pytest.raises(ValueError, match="do not match compatibility ownership"):
            pipeline_tasks.install_legacy_runtime(foreign_routing_runtime)
        assert pipeline_tasks._LEGACY_RUNTIME is None
        assert not hasattr(foreign_routing_runtime, "_legacy_resource_facts")
    finally:
        engine.dispose()
        foreign_engine.dispose()


def test_shutdown_before_legacy_publication_preserves_the_reservation(
    monkeypatch, tmp_path
) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'owner.db'}")
    settings = get_settings().model_copy(update={"database_url": str(engine.url)})
    factory = sessionmaker(bind=engine)
    registry = load_builtin_connectors(
        demo=True, registry=ConnectorRegistry(settings=settings), settings=settings
    )
    runtime = ExecutionRuntime(settings, factory, registry)
    entered = threading.Event()
    release = threading.Event()
    original_facts = pipeline_tasks._legacy_resource_facts
    reservation = object()
    monkeypatch.setattr(pipeline_tasks, "_LEGACY_RUNTIME", None)
    monkeypatch.setattr(pipeline_tasks, "_LEGACY_OWNER_KIND", None)
    monkeypatch.setattr(pipeline_tasks, "_LEGACY_SUPERVISOR_TASK", None)
    monkeypatch.setattr(pipeline_tasks, "_LEGACY_TRANSITIONING", True)
    monkeypatch.setattr(pipeline_tasks, "_LEGACY_TRANSITION_TOKEN", reservation)
    monkeypatch.setattr("app.database.legacy_database_engine", lambda: engine)
    monkeypatch.setattr("app.database.legacy_database_url", lambda: engine.url)
    monkeypatch.setattr("app.database.legacy_session_engine", lambda: engine)
    monkeypatch.setattr("app.database.legacy_session_factory", lambda: factory)
    monkeypatch.setattr("app.connectors.registry.legacy_registry", lambda: registry)
    monkeypatch.setattr("app.connectors.registry.legacy_registry_settings", lambda: settings)

    def paused_facts(current_settings):
        entered.set()
        assert release.wait(2)
        return original_facts(current_settings)

    def stop_runtime() -> None:
        assert entered.wait(2)
        runtime.begin_shutdown()
        release.set()

    stopper = threading.Thread(target=stop_runtime)
    stopper.start()
    monkeypatch.setattr(pipeline_tasks, "_legacy_resource_facts", paused_facts)
    try:
        with pytest.raises(RuntimeError, match="stopped legacy runtime"):
            pipeline_tasks.install_legacy_runtime(runtime, transition_token=reservation)
        stopper.join(2)
        assert not stopper.is_alive()
        assert pipeline_tasks._LEGACY_RUNTIME is None
        assert pipeline_tasks._LEGACY_OWNER_KIND is None
        assert pipeline_tasks._LEGACY_TRANSITIONING is True
        assert pipeline_tasks._LEGACY_TRANSITION_TOKEN is reservation
    finally:
        release.set()
        stopper.join(2)
        engine.dispose()


def test_runtime_scheduling_uses_captured_environment() -> None:
    development = get_settings().model_copy(update={"app_env": "development"})
    development_runtime = ExecutionRuntime(development, SessionLocal, current_registry())
    development.app_env = "test"
    scheduled_after_source_mutation = BackgroundTasks()

    schedule_pipeline_run(
        scheduled_after_source_mutation,
        development,
        "development-generation",
        runtime=development_runtime,
    )

    test_settings = get_settings().model_copy(update={"app_env": "test"})
    test_runtime = ExecutionRuntime(test_settings, SessionLocal, current_registry())
    test_settings.app_env = "development"
    suppressed_after_source_mutation = BackgroundTasks()

    schedule_pipeline_run(
        suppressed_after_source_mutation,
        test_settings,
        "test-generation",
        runtime=test_runtime,
    )

    assert len(scheduled_after_source_mutation.tasks) == 1
    assert suppressed_after_source_mutation.tasks == []


def test_live_lifespan_owner_rejects_retirement(monkeypatch) -> None:
    engine = create_engine("sqlite:///:memory:")
    settings = get_settings().model_copy(update={"database_url": str(engine.url)})
    factory = sessionmaker(bind=engine)
    registry = current_registry()
    monkeypatch.setattr(pipeline_tasks, "_LEGACY_RUNTIME", None)
    monkeypatch.setattr("app.database.legacy_database_engine", lambda: engine)
    monkeypatch.setattr("app.database.legacy_database_url", lambda: engine.url)
    monkeypatch.setattr("app.database.legacy_session_engine", lambda: engine)
    monkeypatch.setattr("app.database.legacy_session_factory", lambda: factory)
    monkeypatch.setattr("app.connectors.registry.legacy_registry", lambda: registry)
    monkeypatch.setattr("app.connectors.registry.legacy_registry_settings", lambda: settings)
    runtime = ExecutionRuntime(settings, factory, registry)
    try:
        pipeline_tasks.install_legacy_runtime(runtime)
        with pytest.raises(RuntimeError, match="lifespan-owned"):
            asyncio.run(pipeline_tasks.retire_legacy_runtime())
        assert not runtime.stop_event.is_set()
    finally:
        pipeline_tasks.release_legacy_runtime(runtime)
        engine.dispose()


def test_legacy_owner_rejects_a_stopped_runtime_before_claiming_it(monkeypatch) -> None:
    runtime = ExecutionRuntime(get_settings(), SessionLocal, current_registry())
    runtime.begin_shutdown()
    monkeypatch.setattr(pipeline_tasks, "_LEGACY_RUNTIME", None)
    monkeypatch.setattr(pipeline_tasks, "_LEGACY_OWNER_KIND", None)
    monkeypatch.setattr(pipeline_tasks, "_LEGACY_SUPERVISOR_TASK", None)
    monkeypatch.setattr(pipeline_tasks, "_LEGACY_TRANSITIONING", False)
    monkeypatch.setattr(pipeline_tasks, "_LEGACY_TRANSITION_TOKEN", None)

    with pytest.raises(RuntimeError, match="stopped legacy runtime"):
        pipeline_tasks.install_legacy_runtime(runtime)
    assert pipeline_tasks._LEGACY_RUNTIME is None


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
