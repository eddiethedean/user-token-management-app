"""Admission and lifespan-drain regression coverage."""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Callable
from contextlib import nullcontext
from contextvars import copy_context
from types import SimpleNamespace
from typing import cast

import anyio
import pytest
from fastapi import HTTPException
from sqlalchemy import event, text
from sqlalchemy.orm import Session
from starlette.requests import Request

from app.config import Settings
from app.connectors.registry import ConnectorRegistry
from app.infrastructure.runtime import ExecutionRuntime
from app.main import RuntimeOwnershipMiddleware, _request_execution, create_app
from app.models import Base
from app.services import pipeline_tasks
from app.services.pipeline_tasks import (
    RuntimeDrainOutcome,
    drain_background_runtime,
    raise_runtime_shutdown_outcome,
)


def _settings(tmp_path) -> Settings:
    return Settings(
        app_env="development",
        database_url=f"sqlite:///{tmp_path / 'lifecycle.db'}",
        data_mover_mode="demo",
        jwt_secret="j" * 40,
        session_pepper="p" * 40,
        csrf_secret="c" * 40,
        pipeline_background_poll_seconds=0.5,
    )


def test_request_ownership_releases_on_asgi_send_failure(tmp_path) -> None:
    runtime = ExecutionRuntime(
        _settings(tmp_path),
        cast(Callable[[], Session], lambda: nullcontext()),
        ConnectorRegistry(),
    )
    owner = SimpleNamespace(state=SimpleNamespace(runtime_lifecycle="accepting", execution=runtime))

    async def downstream(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})

    async def failing_send(message):
        raise RuntimeError("transport failed")

    async def scenario() -> None:
        middleware = RuntimeOwnershipMiddleware(downstream)
        with pytest.raises(RuntimeError, match="transport failed"):
            await middleware(
                {"type": "http", "path": "/data", "app": owner, "state": {}},
                None,
                failing_send,
            )

    asyncio.run(scenario())
    assert runtime.lifetime.is_idle()


def test_readiness_request_remains_admitted_until_its_database_check_finishes(tmp_path) -> None:
    """A slow readiness check must keep shutdown from disposing its runtime."""

    runtime = ExecutionRuntime(
        _settings(tmp_path),
        cast(Callable[[], Session], lambda: nullcontext()),
        ConnectorRegistry(),
    )
    owner = SimpleNamespace(state=SimpleNamespace(runtime_lifecycle="accepting", execution=runtime))
    entered = asyncio.Event()
    release = threading.Event()

    async def downstream(scope, receive, send) -> None:
        entered.set()
        assert await asyncio.to_thread(release.wait, 2)
        await send({"type": "http.response.start", "status": 200, "headers": []})

    async def scenario() -> None:
        middleware = RuntimeOwnershipMiddleware(downstream)
        request = asyncio.create_task(
            middleware(
                {"type": "http", "path": "/ready", "app": owner, "state": {}},
                None,
                lambda message: asyncio.sleep(0),
            )
        )
        await entered.wait()
        draining = asyncio.create_task(drain_background_runtime(None, runtime))
        await asyncio.sleep(0)
        assert not draining.done()
        release.set()
        await request
        await asyncio.wait_for(draining, 2)

    asyncio.run(scenario())
    assert runtime.lifetime.is_idle()


def test_accepting_request_uses_the_runtime_frozen_settings(tmp_path) -> None:
    settings = _settings(tmp_path)
    runtime = ExecutionRuntime(
        settings,
        cast(Callable[[], Session], lambda: nullcontext()),
        ConnectorRegistry(),
    )
    settings.data_mover_mode = "real"
    owner = SimpleNamespace(
        state=SimpleNamespace(runtime_lifecycle="accepting", execution=runtime, settings=settings)
    )

    async def downstream(scope, receive, send) -> None:
        assert scope["state"]["settings"] is runtime.execution_settings
        assert scope["state"]["settings"].data_mover_mode == "demo"
        await send({"type": "http.response.start", "status": 200, "headers": []})

    async def scenario() -> None:
        await RuntimeOwnershipMiddleware(downstream)(
            {"type": "http", "path": "/pipeline", "app": owner, "state": {}},
            None,
            lambda message: asyncio.sleep(0),
        )

    asyncio.run(scenario())


def test_closed_app_execution_resolution_cannot_fall_back_to_global_runtime() -> None:
    app = SimpleNamespace(state=SimpleNamespace(runtime_lifecycle="closed", execution=object()))
    request = Request({"type": "http", "app": app, "state": {}})

    with pytest.raises(HTTPException) as error:
        _request_execution(request)
    assert error.value.status_code == 503


def test_runtime_drain_waits_for_admitted_operation(tmp_path) -> None:
    runtime = ExecutionRuntime(
        _settings(tmp_path),
        cast(Callable[[], Session], lambda: nullcontext()),
        ConnectorRegistry(),
    )
    lease = runtime.try_admit_pipeline_operation()
    assert lease is not None
    waiting = threading.Event()
    original_wait = runtime.wait_until_pipeline_idle

    def wait_until_idle() -> None:
        waiting.set()
        original_wait()

    runtime.wait_until_pipeline_idle = wait_until_idle  # type: ignore[method-assign]

    async def scenario() -> None:
        supervisor = asyncio.create_task(asyncio.sleep(0))
        draining = asyncio.create_task(drain_background_runtime(supervisor, runtime))
        assert await asyncio.to_thread(waiting.wait, 2)
        assert not draining.done()
        lease.release()
        await asyncio.wait_for(draining, 2)

    asyncio.run(scenario())
    assert runtime.stop_event.is_set()


def test_runtime_drain_closes_admission_without_supervisor(tmp_path) -> None:
    runtime = ExecutionRuntime(
        _settings(tmp_path),
        cast(Callable[[], Session], lambda: nullcontext()),
        ConnectorRegistry(),
    )
    lease = runtime.try_admit_pipeline_operation()
    assert lease is not None

    async def scenario() -> None:
        draining = asyncio.create_task(drain_background_runtime(None, runtime))
        await asyncio.sleep(0)
        assert not draining.done()
        assert runtime.try_admit_pipeline_operation() is None
        lease.release()
        await asyncio.wait_for(draining, 2)

    asyncio.run(scenario())
    assert runtime.stop_event.is_set()


def test_borrowed_scope_allows_retained_work_to_nest_after_shutdown_starts(tmp_path) -> None:
    runtime = ExecutionRuntime(
        _settings(tmp_path),
        cast(Callable[[], Session], lambda: nullcontext()),
        ConnectorRegistry(),
    )

    with runtime.operation_scope():
        with runtime.operation_scope():
            retained_context = copy_context()
            runtime.begin_shutdown()

            def nested_operation() -> None:
                with runtime.operation_scope():
                    pass

            retained_context.run(nested_operation)

    assert runtime.lifetime.is_idle()


def test_anyio_cancellation_drains_owned_worker_without_repeated_shields(
    tmp_path, monkeypatch
) -> None:
    runtime = ExecutionRuntime(
        _settings(tmp_path),
        cast(Callable[[], Session], lambda: nullcontext()),
        ConnectorRegistry(),
    )
    entered = threading.Event()
    release = threading.Event()
    shields = 0
    original_shield = asyncio.shield

    def counting_shield(awaitable):
        nonlocal shields
        shields += 1
        return original_shield(awaitable)

    def worker() -> None:
        entered.set()
        release.wait(2)

    async def scenario() -> None:
        outcomes: list[str] = []

        async def operation() -> None:
            try:
                await runtime.run_owned_sync(worker)
            except asyncio.CancelledError:
                outcomes.append("cancelled")
                raise

        async with anyio.create_task_group() as group:
            group.start_soon(operation)
            assert await anyio.to_thread.run_sync(entered.wait, 2)
            group.cancel_scope.cancel()
            release.set()

        assert outcomes == ["cancelled"]

    monkeypatch.setattr(asyncio, "shield", counting_shield)
    anyio.run(scenario)
    assert shields < 10
    assert runtime.lifetime.is_idle()


def test_anyio_timeout_propagates_after_owned_worker_cleanup(tmp_path) -> None:
    runtime = ExecutionRuntime(
        _settings(tmp_path),
        cast(Callable[[], Session], lambda: nullcontext()),
        ConnectorRegistry(),
    )
    entered = threading.Event()
    release = threading.Event()
    cleaned = threading.Event()

    def worker() -> None:
        entered.set()
        try:
            release.wait(2)
        finally:
            cleaned.set()

    async def scenario() -> None:
        timer = threading.Timer(0.05, release.set)
        timer.start()
        try:
            with pytest.raises(TimeoutError):
                with anyio.fail_after(0.01):
                    await runtime.run_owned_sync(worker)
        finally:
            release.set()
            timer.join()

    anyio.run(scenario)
    assert entered.is_set()
    assert cleaned.is_set()
    assert runtime.lifetime.is_idle()


def test_anyio_cancellation_preserves_worker_failure_as_its_cause(tmp_path) -> None:
    runtime = ExecutionRuntime(
        _settings(tmp_path),
        cast(Callable[[], Session], lambda: nullcontext()),
        ConnectorRegistry(),
    )
    entered = threading.Event()
    release = threading.Event()
    worker_error = ValueError("worker failed during caller cancellation")

    def worker() -> None:
        entered.set()
        release.wait(2)
        raise worker_error

    async def scenario() -> None:
        observed: list[asyncio.CancelledError] = []

        async def operation() -> None:
            try:
                await runtime.run_owned_sync(worker)
            except asyncio.CancelledError as error:
                observed.append(error)
                raise

        async with anyio.create_task_group() as group:
            group.start_soon(operation)
            assert await anyio.to_thread.run_sync(entered.wait, 2)
            group.cancel_scope.cancel()
            release.set()

        assert len(observed) == 1
        assert observed[0].__cause__ is worker_error

    anyio.run(scenario)
    assert runtime.lifetime.is_idle()


@pytest.mark.parametrize(
    ("worker_error", "expected_cause"),
    [
        pytest.param(None, None, id="worker-success"),
        pytest.param(ValueError("worker failed during drain"), ValueError, id="worker-failure"),
    ],
)
def test_independent_cancellation_survives_timeout_during_owned_worker_drain(
    tmp_path, worker_error, expected_cause
) -> None:
    runtime = ExecutionRuntime(
        _settings(tmp_path),
        cast(Callable[[], Session], lambda: nullcontext()),
        ConnectorRegistry(),
    )
    entered = threading.Event()
    release_work = threading.Event()
    cleanup_entered = threading.Event()
    release_cleanup = threading.Event()
    invocations = 0

    def worker() -> None:
        nonlocal invocations
        invocations += 1
        entered.set()
        try:
            assert release_work.wait(2)
            if worker_error is not None:
                raise worker_error
        finally:
            cleanup_entered.set()
            assert release_cleanup.wait(2)

    async def scenario() -> None:
        timeout_ready = asyncio.Event()
        timeout: asyncio.Timeout | None = None

        async def operation() -> None:
            nonlocal timeout
            async with asyncio.timeout(None) as timeout:
                timeout_ready.set()
                await runtime.run_owned_sync(worker)

        owned_operation = asyncio.create_task(operation())
        try:
            assert await asyncio.to_thread(entered.wait, 2)
            await timeout_ready.wait()
            assert timeout is not None

            owned_operation.cancel("independent task cancellation")
            await asyncio.sleep(0)
            assert owned_operation.cancelling() == 1
            assert not owned_operation.done()
            assert not runtime.lifetime.is_idle()

            timeout.reschedule(asyncio.get_running_loop().time())
            for _ in range(5):
                if timeout.expired():
                    break
                await asyncio.sleep(0)
            assert timeout.expired()
            assert owned_operation.cancelling() == 2
            assert not owned_operation.done()

            release_work.set()
            assert await asyncio.to_thread(cleanup_entered.wait, 2)
            assert not runtime.lifetime.is_idle()
            release_cleanup.set()
            with pytest.raises(
                asyncio.CancelledError, match="independent task cancellation"
            ) as raised:
                await owned_operation
            if expected_cause is None:
                assert raised.value.__cause__ is None
            else:
                assert isinstance(raised.value.__cause__, expected_cause)
                assert raised.value.__cause__ is worker_error
            assert owned_operation.cancelling() == 1
        finally:
            release_work.set()
            release_cleanup.set()
            if not owned_operation.done():
                with pytest.raises(asyncio.CancelledError):
                    await asyncio.wait_for(owned_operation, 2)

    asyncio.run(scenario())
    assert invocations == 1
    assert runtime.lifetime.is_idle()


def test_timeout_only_cancellation_drains_owned_worker_and_raises_timeout(tmp_path) -> None:
    runtime = ExecutionRuntime(
        _settings(tmp_path),
        cast(Callable[[], Session], lambda: nullcontext()),
        ConnectorRegistry(),
    )
    entered = threading.Event()
    release_work = threading.Event()
    cleanup_entered = threading.Event()
    release_cleanup = threading.Event()

    def worker() -> None:
        entered.set()
        try:
            assert release_work.wait(2)
        finally:
            cleanup_entered.set()
            assert release_cleanup.wait(2)

    async def scenario() -> None:
        timeout_ready = asyncio.Event()
        timeout: asyncio.Timeout | None = None

        async def operation() -> None:
            nonlocal timeout
            async with asyncio.timeout(None) as timeout:
                timeout_ready.set()
                await runtime.run_owned_sync(worker)

        owned_operation = asyncio.create_task(operation())
        try:
            assert await asyncio.to_thread(entered.wait, 2)
            await timeout_ready.wait()
            assert timeout is not None
            timeout.reschedule(asyncio.get_running_loop().time())
            for _ in range(5):
                if timeout.expired():
                    break
                await asyncio.sleep(0)
            assert timeout.expired()
            assert owned_operation.cancelling() == 1
            assert not owned_operation.done()

            release_work.set()
            assert await asyncio.to_thread(cleanup_entered.wait, 2)
            assert not runtime.lifetime.is_idle()
            release_cleanup.set()
            with pytest.raises(TimeoutError):
                await owned_operation
            assert owned_operation.cancelling() == 0
        finally:
            release_work.set()
            release_cleanup.set()
            if not owned_operation.done():
                with pytest.raises(TimeoutError):
                    await asyncio.wait_for(owned_operation, 2)

    asyncio.run(scenario())
    assert runtime.lifetime.is_idle()


def test_repeated_independent_cancellation_preserves_counts_during_owned_worker_drain(
    tmp_path,
) -> None:
    runtime = ExecutionRuntime(
        _settings(tmp_path),
        cast(Callable[[], Session], lambda: nullcontext()),
        ConnectorRegistry(),
    )
    entered = threading.Event()
    release = threading.Event()
    invocations = 0

    def worker() -> None:
        nonlocal invocations
        invocations += 1
        entered.set()
        assert release.wait(2)

    async def scenario() -> None:
        owned_operation = asyncio.create_task(runtime.run_owned_sync(worker))
        try:
            assert await asyncio.to_thread(entered.wait, 2)
            owned_operation.cancel("first cancellation")
            await asyncio.sleep(0)
            assert owned_operation.cancelling() == 1
            assert not owned_operation.done()

            owned_operation.cancel("second cancellation")
            await asyncio.sleep(0)
            assert owned_operation.cancelling() == 2
            assert not owned_operation.done()

            release.set()
            with pytest.raises(asyncio.CancelledError, match="first cancellation"):
                await owned_operation
            assert owned_operation.cancelling() == 2
        finally:
            release.set()
            if not owned_operation.done():
                with pytest.raises(asyncio.CancelledError):
                    await asyncio.wait_for(owned_operation, 2)

    asyncio.run(scenario())
    assert invocations == 1
    assert runtime.lifetime.is_idle()


def test_worker_originated_cancellation_is_terminal(tmp_path) -> None:
    runtime = ExecutionRuntime(
        _settings(tmp_path),
        cast(Callable[[], Session], lambda: nullcontext()),
        ConnectorRegistry(),
    )

    def worker() -> None:
        raise asyncio.CancelledError("worker cancelled itself")

    async def scenario() -> None:
        with pytest.raises(asyncio.CancelledError, match="worker cancelled itself"):
            await asyncio.wait_for(runtime.run_owned_sync(worker), 1)

    asyncio.run(scenario())
    assert runtime.lifetime.is_idle()


def test_rejected_owned_worker_submission_releases_scope_with_traceback_retained(
    tmp_path, monkeypatch
) -> None:
    runtime = ExecutionRuntime(
        _settings(tmp_path),
        cast(Callable[[], Session], lambda: nullcontext()),
        ConnectorRegistry(),
    )

    async def scenario() -> RuntimeError:
        loop = asyncio.get_running_loop()

        def reject_submission(*_args):
            raise RuntimeError("executor rejected submission")

        monkeypatch.setattr(loop, "run_in_executor", reject_submission)
        with pytest.raises(RuntimeError, match="rejected submission") as raised:
            await runtime.run_owned_sync(lambda: None)
        return raised.value

    retained_error = asyncio.run(scenario())
    assert retained_error.__traceback__ is not None
    assert runtime.lifetime.is_idle()


def test_owned_worker_drain_waits_for_actual_thread_cleanup(tmp_path) -> None:
    runtime = ExecutionRuntime(
        _settings(tmp_path),
        cast(Callable[[], Session], lambda: nullcontext()),
        ConnectorRegistry(),
    )
    entered = threading.Event()
    release = threading.Event()
    cleaned = threading.Event()

    def worker() -> None:
        entered.set()
        try:
            release.wait(2)
        finally:
            cleaned.set()

    async def scenario() -> None:
        operation = asyncio.create_task(runtime.run_owned_sync(worker))
        assert await asyncio.to_thread(entered.wait, 2)
        assert all(
            getattr(task.get_coro(), "__name__", "") != "to_thread" for task in asyncio.all_tasks()
        )
        operation.cancel()
        await asyncio.sleep(0)
        assert not operation.done()
        draining = asyncio.create_task(drain_background_runtime(None, runtime))
        await asyncio.sleep(0)
        assert not draining.done()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await operation
        await asyncio.wait_for(draining, 2)

    asyncio.run(scenario())
    assert cleaned.is_set()
    assert runtime.lifetime.is_idle()


def test_cancelled_runtime_drain_still_waits_for_admitted_work(tmp_path) -> None:
    runtime = ExecutionRuntime(
        _settings(tmp_path),
        cast(Callable[[], Session], lambda: nullcontext()),
        ConnectorRegistry(),
    )
    lease = runtime.try_admit_pipeline_operation()
    assert lease is not None

    async def scenario() -> None:
        draining = asyncio.create_task(drain_background_runtime(None, runtime))
        await asyncio.sleep(0)
        draining.cancel()
        await asyncio.sleep(0.02)
        assert not draining.done()
        lease.release()
        outcome = await asyncio.wait_for(draining, 2)
        assert outcome.cancelled
        assert runtime.lifetime.is_idle()

    asyncio.run(scenario())


def test_anyio_runtime_drain_waits_without_spinning_on_cancellation(tmp_path, monkeypatch) -> None:
    runtime = ExecutionRuntime(
        _settings(tmp_path),
        cast(Callable[[], Session], lambda: nullcontext()),
        ConnectorRegistry(),
    )
    lease = runtime.try_admit_pipeline_operation()
    assert lease is not None
    shields = 0
    original_shield = asyncio.shield

    def counting_shield(awaitable):
        nonlocal shields
        shields += 1
        return original_shield(awaitable)

    async def scenario() -> None:
        outcomes = []

        async def release_after_cancellation() -> None:
            with anyio.CancelScope(shield=True):
                await anyio.sleep(0.03)
                lease.release()

        async def drain() -> None:
            outcomes.append(await drain_background_runtime(None, runtime))

        async with anyio.create_task_group() as group:
            group.start_soon(release_after_cancellation)
            group.start_soon(drain)
            await anyio.sleep(0)
            group.cancel_scope.cancel()

        assert outcomes[0].cancelled

    monkeypatch.setattr(asyncio, "shield", counting_shield)
    anyio.run(scenario)
    assert shields < 10
    assert runtime.lifetime.is_idle()


def test_anyio_timeout_remains_a_timeout_for_the_runtime_supervisor(tmp_path, monkeypatch) -> None:
    runtime = ExecutionRuntime(
        _settings(tmp_path),
        cast(Callable[[], Session], lambda: nullcontext()),
        ConnectorRegistry(),
    )
    entered = threading.Event()
    release = threading.Event()

    def process_pending(_runtime) -> bool:
        entered.set()
        release.wait(2)
        return False

    monkeypatch.setattr(pipeline_tasks, "process_pending_pipeline_run_background", process_pending)

    async def scenario() -> None:
        timer = threading.Timer(0.05, release.set)
        timer.start()
        try:
            with pytest.raises(TimeoutError):
                with anyio.fail_after(0.01):
                    await pipeline_tasks.run_background_runtime(runtime=runtime)
        finally:
            release.set()
            timer.join()

    anyio.run(scenario)
    assert entered.is_set()


def test_runtime_drain_records_supervisor_failure_after_admitted_work(tmp_path) -> None:
    runtime = ExecutionRuntime(
        _settings(tmp_path),
        cast(Callable[[], Session], lambda: nullcontext()),
        ConnectorRegistry(),
    )
    lease = runtime.try_admit_pipeline_operation()
    assert lease is not None
    failed = asyncio.Event()

    async def supervisor() -> None:
        failed.set()
        raise RuntimeError("supervisor failed during drain")

    async def scenario() -> None:
        task = asyncio.create_task(supervisor())
        draining = asyncio.create_task(drain_background_runtime(task, runtime))
        await failed.wait()
        await asyncio.sleep(0)
        assert not draining.done()
        lease.release()
        outcome = await asyncio.wait_for(draining, 2)
        assert isinstance(outcome.supervisor_error, RuntimeError)

    asyncio.run(scenario())
    assert runtime.lifetime.is_idle()


def test_runtime_drain_keeps_supervisor_cancellation_separate_from_caller(tmp_path) -> None:
    runtime = ExecutionRuntime(
        _settings(tmp_path),
        cast(Callable[[], Session], lambda: nullcontext()),
        ConnectorRegistry(),
    )

    async def supervisor() -> None:
        raise asyncio.CancelledError("supervisor stopped")

    async def scenario() -> None:
        task = asyncio.create_task(supervisor())
        await asyncio.sleep(0)
        outcome = await drain_background_runtime(task, runtime)
        assert not outcome.cancelled
        assert outcome.caller_cancellation is None
        assert isinstance(outcome.supervisor_error, asyncio.CancelledError)
        assert outcome.supervisor_error.args == ("supervisor stopped",)

    asyncio.run(scenario())


def test_idle_submission_failure_preserves_caller_and_supervisor_errors(tmp_path) -> None:
    runtime = ExecutionRuntime(
        _settings(tmp_path),
        cast(Callable[[], Session], lambda: nullcontext()),
        ConnectorRegistry(),
    )
    supervisor_error = ValueError("supervisor failed")

    async def scenario() -> None:
        finish = asyncio.Event()

        async def supervisor() -> None:
            await finish.wait()
            raise supervisor_error

        task = asyncio.create_task(supervisor())
        draining = asyncio.create_task(drain_background_runtime(task, runtime))
        try:
            await asyncio.sleep(0)
            draining.cancel("original caller cancellation")
            await asyncio.sleep(0)
            await asyncio.get_running_loop().shutdown_default_executor()
            finish.set()
            outcome = await asyncio.wait_for(asyncio.shield(draining), 2)
            assert outcome.supervisor_error is supervisor_error
            assert isinstance(outcome.drain_error, RuntimeError)
            assert "Executor shutdown" in str(outcome.drain_error)
            assert outcome.cancelled
            assert outcome.caller_cancellation is not None
            assert outcome.caller_cancellation.args == ("original caller cancellation",)
            assert draining.cancelling() == 1
            with pytest.raises(asyncio.CancelledError) as raised:
                raise_runtime_shutdown_outcome(
                    body_error=None,
                    drain_error=outcome.drain_error,
                    drain_outcome=outcome,
                    disposal_error=None,
                )
            assert raised.value is outcome.caller_cancellation
            assert vars(raised.value)["shutdown_secondary_errors"] == (
                outcome.drain_error,
                supervisor_error,
            )
        finally:
            finish.set()
            await asyncio.gather(task, draining, return_exceptions=True)

    asyncio.run(scenario())


def test_supervisor_cancellation_does_not_mask_an_anyio_timeout(tmp_path) -> None:
    runtime = ExecutionRuntime(
        _settings(tmp_path),
        cast(Callable[[], Session], lambda: nullcontext()),
        ConnectorRegistry(),
    )
    lease = runtime.try_admit_pipeline_operation()
    assert lease is not None

    async def supervisor() -> None:
        raise asyncio.CancelledError("supervisor stopped")

    async def scenario() -> None:
        task = asyncio.create_task(supervisor())
        await asyncio.sleep(0)
        release = threading.Timer(0.05, lease.release)
        release.start()
        try:
            with pytest.raises(TimeoutError):
                with anyio.fail_after(0.01):
                    outcome = await drain_background_runtime(task, runtime)
                    raise_runtime_shutdown_outcome(
                        body_error=None,
                        drain_error=outcome.drain_error,
                        drain_outcome=outcome,
                        disposal_error=None,
                    )
        finally:
            lease.release()
            release.join()

    anyio.run(scenario)
    assert runtime.lifetime.is_idle()


def test_shutdown_outcome_preserves_primary_and_secondary_errors() -> None:
    body_error = ValueError("body failed")
    supervisor_error = RuntimeError("supervisor failed")
    disposal_error = OSError("disposal failed")

    with pytest.raises(ValueError) as raised:
        raise_runtime_shutdown_outcome(
            body_error=body_error,
            drain_error=None,
            drain_outcome=RuntimeDrainOutcome(supervisor_error=supervisor_error),
            disposal_error=disposal_error,
        )

    assert raised.value is body_error
    assert vars(raised.value)["shutdown_secondary_errors"] == (
        supervisor_error,
        disposal_error,
    )


def test_composed_lifespan_clears_readiness_before_worker_drain(tmp_path, monkeypatch) -> None:
    settings = _settings(tmp_path)
    app = create_app(settings)
    composition = app.state.composition
    Base.metadata.create_all(composition.database.engine)
    entered = threading.Event()
    release = threading.Event()
    readiness_during_shutdown: list[bool] = []

    def process_one(db, current_settings, **kwargs):
        entered.set()
        release.wait(3)
        return False

    async def scenario() -> None:
        with monkeypatch.context() as patcher:
            patcher.setattr("app.main.assert_schema_current", lambda *_: None)
            patcher.setattr("app.worker.process_one", process_one)
            async with app.router.lifespan_context(app):
                assert await asyncio.to_thread(entered.wait, 2)

                async def observe_shutdown() -> None:
                    assert await asyncio.to_thread(composition.execution.stop_event.wait, 2)
                    readiness_during_shutdown.append(app.state.ready)
                    release.set()

                observer = asyncio.create_task(observe_shutdown())
            await observer

    try:
        asyncio.run(scenario())
    finally:
        release.set()
        composition.close()
    assert readiness_during_shutdown == [False]


def test_request_execution_keeps_draining_composed_owner(tmp_path) -> None:
    from types import SimpleNamespace

    from starlette.requests import Request

    from app.main import _request_execution

    settings = _settings(tmp_path)
    first = ExecutionRuntime(
        settings, cast(Callable[[], Session], lambda: nullcontext()), ConnectorRegistry()
    )
    second = ExecutionRuntime(
        settings, cast(Callable[[], Session], lambda: nullcontext()), ConnectorRegistry()
    )
    application = SimpleNamespace(
        state=SimpleNamespace(execution=second, runtime_lifecycle="draining", ready=False)
    )
    request = Request(
        {
            "type": "http",
            "app": application,
            "method": "GET",
            "path": "/pipeline",
            "headers": [],
            "query_string": b"",
            "scheme": "http",
            "client": ("test", 1),
            "server": ("test", 80),
        }
    )
    assert first is not second
    assert _request_execution(request) is second


def test_composed_lifespan_drains_worker_before_supervisor_failure_disposal(
    tmp_path, monkeypatch
) -> None:
    settings = _settings(tmp_path)
    app = create_app(settings)
    composition = app.state.composition
    Base.metadata.create_all(composition.database.engine)
    entered = threading.Event()
    release = threading.Event()
    finished = threading.Event()
    observations: list[dict[str, bool]] = []

    def process_one(db, current_settings, **kwargs):
        entered.set()
        try:
            release.wait(3)
        finally:
            finished.set()
        return False

    def start(runtime):
        async def supervisor():
            await asyncio.to_thread(runtime.stop_event.wait)
            raise RuntimeError("supervisor failed during drain")

        return asyncio.create_task(supervisor())

    original_close = composition.close

    def close() -> None:
        observations.append(
            {
                "worker_done": finished.is_set(),
                "pipeline_lock_held": composition.execution.pipeline_lock.locked(),
                "lifetime_idle": composition.execution.lifetime.is_idle(),
            }
        )
        original_close()

    async def scenario() -> None:
        worker = None
        releaser = None
        with monkeypatch.context() as patcher:
            patcher.setattr("app.main.assert_schema_current", lambda *_: None)
            patcher.setattr("app.worker.process_one", process_one)
            patcher.setattr(pipeline_tasks, "start_background_runtime", start)
            patcher.setattr(composition, "close", close)
            with pytest.raises(RuntimeError, match="supervisor failed"):
                async with app.router.lifespan_context(app):
                    worker = asyncio.create_task(
                        asyncio.to_thread(
                            pipeline_tasks.process_pipeline_run_background,
                            composition.execution,
                            "supervisor-failure",
                        )
                    )
                    assert await asyncio.to_thread(entered.wait, 2)

                    async def release_when_stopping() -> None:
                        assert await asyncio.to_thread(composition.execution.stop_event.wait, 2)
                        release.set()

                    releaser = asyncio.create_task(release_when_stopping())
        assert worker is not None
        assert releaser is not None
        await worker
        await releaser

    try:
        asyncio.run(scenario())
    finally:
        release.set()
    assert observations == [
        {"worker_done": True, "pipeline_lock_held": False, "lifetime_idle": True}
    ]


def test_composed_lifespan_disposes_resources_when_startup_fails(tmp_path, monkeypatch) -> None:
    app = create_app(_settings(tmp_path))
    composition = app.state.composition
    closed: list[object] = []
    Base.metadata.create_all(composition.database.engine)

    event.listen(
        composition.database.engine.pool, "close", lambda connection, _: closed.append(connection)
    )

    def fail_after_opening_connection(db) -> None:
        db.execute(text("SELECT 1"))
        raise RuntimeError("demo catalog cleanup failed")

    async def scenario() -> None:
        with monkeypatch.context() as patcher:
            patcher.setattr("app.main.assert_schema_current", lambda *_: None)
            patcher.setattr(
                "app.services.catalogs.clear_demo_catalog_cache", fail_after_opening_connection
            )
            with pytest.raises(RuntimeError, match="demo catalog cleanup failed"):
                async with app.router.lifespan_context(app):
                    pass

    asyncio.run(scenario())
    assert closed


def test_composed_lifespan_keeps_resources_when_idle_drain_fails(tmp_path, monkeypatch) -> None:
    app = create_app(_settings(tmp_path))
    composition = app.state.composition
    Base.metadata.create_all(composition.database.engine)
    runtime = composition.execution
    lease = runtime.try_admit_pipeline_operation()
    assert lease is not None
    close_calls: list[bool] = []
    original_close = composition.close

    def fail_idle() -> None:
        raise RuntimeError("idle drain failed")

    def close() -> None:
        close_calls.append(runtime.lifetime.is_idle())
        original_close()

    runtime.wait_until_pipeline_idle = fail_idle  # type: ignore[method-assign]

    async def scenario() -> None:
        with monkeypatch.context() as patcher:
            patcher.setattr("app.main.assert_schema_current", lambda *_: None)
            patcher.setattr(composition, "close", close)
            with pytest.raises(RuntimeError, match="idle drain failed"):
                async with app.router.lifespan_context(app):
                    pass

    try:
        asyncio.run(scenario())
        assert close_calls == []
        assert not runtime.lifetime.is_idle()
        assert runtime.stop_event.is_set()
    finally:
        lease.release()
        original_close()


def test_composed_startup_failure_drains_admitted_worker_before_disposal(
    tmp_path, monkeypatch
) -> None:
    app = create_app(_settings(tmp_path))
    composition = app.state.composition
    Base.metadata.create_all(composition.database.engine)
    entered = threading.Event()
    release = threading.Event()
    finished = threading.Event()
    disposal_state: list[dict[str, bool]] = []
    worker: threading.Thread | None = None
    original_close = composition.close

    def admitted_worker() -> None:
        try:
            with composition.execution.operation_scope():
                with composition.database.session() as db:
                    db.execute(text("SELECT 1"))
                    entered.set()
                    release.wait(2)
        finally:
            finished.set()

    def fail_initialization(db) -> None:
        nonlocal worker
        worker = threading.Thread(target=admitted_worker)
        worker.start()
        assert entered.wait(1)
        threading.Timer(0.05, release.set).start()
        raise RuntimeError("initialization failed with admitted work")

    def close() -> None:
        disposal_state.append(
            {
                "worker_done": finished.is_set(),
                "lifetime_idle": composition.execution.lifetime.is_idle(),
            }
        )
        original_close()

    async def scenario() -> None:
        with monkeypatch.context() as patcher:
            patcher.setattr("app.main.assert_schema_current", lambda *_: None)
            patcher.setattr("app.services.catalogs.clear_demo_catalog_cache", fail_initialization)
            patcher.setattr(composition, "close", close)
            with pytest.raises(RuntimeError, match="initialization failed with admitted work"):
                async with app.router.lifespan_context(app):
                    pass

    try:
        asyncio.run(scenario())
    finally:
        release.set()
        if worker is not None:
            worker.join(2)
    assert disposal_state == [{"worker_done": True, "lifetime_idle": True}]


def test_composed_supervisor_start_failure_drains_before_disposal(tmp_path, monkeypatch) -> None:
    app = create_app(_settings(tmp_path))
    composition = app.state.composition
    Base.metadata.create_all(composition.database.engine)
    entered = threading.Event()
    release = threading.Event()
    finished = threading.Event()
    disposal_state: list[dict[str, bool]] = []
    worker: threading.Thread | None = None
    original_close = composition.close

    def admitted_worker() -> None:
        try:
            with composition.execution.operation_scope():
                entered.set()
                release.wait(2)
        finally:
            finished.set()

    def fail_supervisor_start(_runtime) -> asyncio.Task[object]:
        nonlocal worker
        worker = threading.Thread(target=admitted_worker)
        worker.start()
        assert entered.wait(1)
        threading.Timer(0.05, release.set).start()
        raise RuntimeError("supervisor start failed with admitted work")

    def close() -> None:
        disposal_state.append(
            {
                "worker_done": finished.is_set(),
                "lifetime_idle": composition.execution.lifetime.is_idle(),
            }
        )
        original_close()

    async def scenario() -> None:
        with monkeypatch.context() as patcher:
            patcher.setattr("app.main.assert_schema_current", lambda *_: None)
            patcher.setattr(pipeline_tasks, "start_background_runtime", fail_supervisor_start)
            patcher.setattr(composition, "close", close)
            with pytest.raises(RuntimeError, match="supervisor start failed with admitted work"):
                async with app.router.lifespan_context(app):
                    pass

    try:
        asyncio.run(scenario())
    finally:
        release.set()
        if worker is not None:
            worker.join(2)
    assert disposal_state == [{"worker_done": True, "lifetime_idle": True}]


def test_composed_startup_preserves_admitted_workers_stop_event(tmp_path, monkeypatch) -> None:
    settings = _settings(tmp_path).model_copy(update={"app_env": "test"})
    app = create_app(settings)
    composition = app.state.composition
    Base.metadata.create_all(composition.database.engine)
    runtime = composition.execution
    original_stop = runtime.stop_event
    worker_entered = threading.Event()
    worker_stopped = threading.Event()
    disposal_state: list[tuple[bool, bool]] = []
    original_close = composition.close

    def worker() -> None:
        with runtime.operation_scope():
            worker_entered.set()
            if original_stop.wait(3):
                worker_stopped.set()

    def close() -> None:
        disposal_state.append((worker_stopped.is_set(), runtime.lifetime.is_idle()))
        original_close()

    async def scenario() -> None:
        started = asyncio.Event()
        leave = asyncio.Event()

        async def lifecycle() -> None:
            async with app.router.lifespan_context(app):
                started.set()
                await leave.wait()

        task = asyncio.create_task(lifecycle())
        try:
            await asyncio.wait_for(started.wait(), 2)
            assert runtime.stop_event is original_stop
            assert app.state.pipeline_stop_event is original_stop
            leave.set()
            await asyncio.wait_for(asyncio.shield(task), 2)
            assert worker_stopped.is_set()
            assert disposal_state == [(True, True)]
        finally:
            original_stop.set()
            leave.set()
            await asyncio.wait_for(task, 2)

    thread = threading.Thread(target=worker)
    try:
        thread.start()
        assert worker_entered.wait(1)
        monkeypatch.setattr(composition, "close", close)
        asyncio.run(scenario())
    finally:
        original_stop.set()
        thread.join(2)
        original_close()
    assert not thread.is_alive()


def test_composed_lifespan_replaces_a_safely_closed_runtime(tmp_path) -> None:
    settings = _settings(tmp_path).model_copy(update={"app_env": "test"})
    app = create_app(settings)
    composition = app.state.composition
    Base.metadata.create_all(composition.database.engine)

    async def scenario() -> None:
        async with app.router.lifespan_context(app):
            first = app.state.execution
        async with app.router.lifespan_context(app):
            second = app.state.execution
            assert second is not first
            assert second.is_accepting()
            assert first.stop_event.is_set()
            assert second.stop_event is not first.stop_event
            assert app.state.pipeline_stop_event is second.stop_event

    asyncio.run(scenario())


def test_composed_lifespan_preserves_body_supervisor_and_disposal_errors(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    app = create_app(settings)
    composition = app.state.composition
    Base.metadata.create_all(composition.database.engine)
    body_error = ValueError("body failed")

    def start(runtime):
        async def supervisor() -> None:
            raise RuntimeError("supervisor failed")

        return asyncio.create_task(supervisor())

    def close() -> None:
        raise OSError("disposal failed")

    async def scenario() -> None:
        with monkeypatch.context() as patcher:
            patcher.setattr("app.main.assert_schema_current", lambda *_: None)
            patcher.setattr(pipeline_tasks, "start_background_runtime", start)
            patcher.setattr(composition, "close", close)
            with pytest.raises(ValueError) as raised:
                async with app.router.lifespan_context(app):
                    raise body_error
        assert raised.value is body_error
        assert vars(body_error)["shutdown_secondary_errors"]
        assert {type(error) for error in vars(body_error)["shutdown_secondary_errors"]} == {
            RuntimeError,
            OSError,
        }

    asyncio.run(scenario())
