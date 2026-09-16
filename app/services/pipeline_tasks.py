"""In-process pipeline execution and retention tasks."""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from collections.abc import Callable
from contextlib import AbstractContextManager
from contextvars import copy_context
from dataclasses import dataclass
from typing import cast

from anyio import CancelScope
from anyio.lowlevel import checkpoint_if_cancelled
from fastapi import BackgroundTasks
from sqlalchemy.engine import URL, make_url
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.connectors.registry import ConnectorRegistry
from app.infrastructure.persistence.database import (
    clone_session_factory,
    session_factory_implementation_matches,
)
from app.infrastructure.runtime import ExecutionRuntime, RuntimeAdmissionError

SessionFactory = Callable[[], AbstractContextManager]

log = logging.getLogger(__name__)
_BACKGROUND_PIPELINE_LOCK = threading.Lock()
_LEGACY_RUNTIME_LOCK = threading.Lock()
_LEGACY_RUNTIME: ExecutionRuntime | None = None
_LEGACY_SUPERVISOR_TASK: asyncio.Task[object] | None = None
_LEGACY_OWNER_KIND: str | None = None
_LEGACY_TRANSITIONING = False
_LEGACY_TRANSITION_TOKEN: object | None = None
_LEGACY_GENERATION = 0


@dataclass(frozen=True)
class RuntimeDrainOutcome:
    """Terminal supervisor outcome after all admitted work has drained."""

    supervisor_error: BaseException | None = None
    drain_error: BaseException | None = None
    cancelled: bool = False
    caller_cancellation: asyncio.CancelledError | None = None


def raise_runtime_shutdown_outcome(
    *,
    body_error: BaseException | None,
    drain_error: BaseException | None,
    drain_outcome: RuntimeDrainOutcome | None,
    disposal_error: BaseException | None,
) -> None:
    """Apply shutdown precedence while retaining every secondary exception."""

    candidates: list[BaseException] = []

    def retain(error: BaseException | None) -> None:
        if error is not None and not any(error is candidate for candidate in candidates):
            candidates.append(error)

    retain(drain_error)
    if drain_outcome is not None:
        retain(drain_outcome.drain_error)
        retain(drain_outcome.supervisor_error)
    retain(disposal_error)
    primary: BaseException | None = body_error
    if primary is None and drain_outcome is not None and drain_outcome.cancelled:
        primary = drain_outcome.caller_cancellation or asyncio.CancelledError()
    if primary is None and drain_outcome is not None and drain_outcome.supervisor_error is not None:
        primary = drain_outcome.supervisor_error
    if primary is None:
        primary = (
            drain_error
            or (drain_outcome.drain_error if drain_outcome is not None else None)
            or disposal_error
        )
    if primary is None:
        return
    secondary = tuple(error for error in candidates if error is not primary)
    if secondary:
        setattr(primary, "shutdown_secondary_errors", secondary)  # noqa: B010
        primary.add_note(
            "Shutdown secondary errors: " + ", ".join(type(error).__name__ for error in secondary)
        )
    if isinstance(primary, asyncio.CancelledError):
        cause = next(
            (error for error in secondary if not isinstance(error, asyncio.CancelledError)), None
        )
        if cause is not None:
            raise primary from cause
    raise primary


@dataclass(frozen=True)
class _LegacyResourceFacts:
    """Immutable facts used to validate one compatibility generation."""

    engine: object
    database_url: object
    session_factory: object
    session_options: tuple[tuple[str, object], ...]
    registry: object
    registry_settings: object
    settings_identity: object
    settings_values: tuple[tuple[str, str], ...]
    registry_revision: int
    registry_facts: tuple[tuple[str, str, int], ...]


@dataclass(frozen=True)
class _CompletionResult:
    """The terminal result of work kept alive through cancellation."""

    value: object = None
    error: BaseException | None = None
    caller_cancellation: asyncio.CancelledError | None = None


def _settings_values(settings: Settings) -> tuple[tuple[str, str], ...]:
    values = settings.model_dump() if hasattr(settings, "model_dump") else vars(settings)
    return tuple(sorted((str(key), repr(value)) for key, value in values.items()))


def _factory_options(factory: SessionFactory) -> tuple[tuple[str, object], ...]:
    options = getattr(factory, "kw", {})
    return tuple(sorted((str(key), value) for key, value in options.items() if key != "bind"))


def _legacy_resource_facts(
    settings: Settings,
) -> tuple[_LegacyResourceFacts, object, SessionFactory, object]:
    from app.connectors.registry import legacy_registry, legacy_registry_settings
    from app.database import (
        legacy_database_engine,
        legacy_database_url,
        legacy_session_engine,
        legacy_session_factory,
    )

    try:
        supplied_value = settings.database_url
        global_value = legacy_database_url()
        supplied_url = (
            supplied_value if isinstance(supplied_value, URL) else make_url(str(supplied_value))
        )
        global_url = global_value if isinstance(global_value, URL) else make_url(str(global_value))
    except Exception as exc:
        raise ValueError(
            "Legacy background execution settings do not own the global database."
        ) from exc
    if supplied_url != global_url:
        raise ValueError("Legacy background execution settings do not own the global database.")
    database_engine = legacy_database_engine()
    if legacy_session_engine() is not database_engine:
        raise ValueError("Legacy background execution settings do not own the session factory.")
    source_factory = legacy_session_factory()
    options = dict(getattr(source_factory, "kw", {}))
    binds = options.get("binds") or {}
    if any(bind is not database_engine for bind in binds.values()):
        raise ValueError("Legacy background execution settings do not own session bindings.")
    registry = legacy_registry()
    owner_settings = legacy_registry_settings()
    if owner_settings is not None and owner_settings is not settings:
        raise ValueError("Legacy background execution settings do not own the global registry.")
    facts = _LegacyResourceFacts(
        engine=database_engine,
        database_url=global_url,
        session_factory=source_factory,
        session_options=_factory_options(source_factory),
        registry=registry,
        registry_settings=owner_settings,
        settings_identity=settings,
        settings_values=_settings_values(settings),
        registry_revision=registry.revision,
        registry_facts=registry.configuration_facts(),
    )
    return facts, database_engine, source_factory, registry


def _facts_match(facts: _LegacyResourceFacts, current: _LegacyResourceFacts) -> bool:
    return facts == current


def _runtime_matches_legacy_resources(
    runtime: ExecutionRuntime,
    facts: _LegacyResourceFacts,
    database_engine: object,
    source_factory: SessionFactory,
    registry: ConnectorRegistry,
) -> bool:
    """Check a proposed compatibility owner without opening resources."""

    factory = runtime.sessions
    options = getattr(factory, "kw", None)
    if not isinstance(options, dict):
        return False
    if options.get("bind") is not database_engine:
        return False
    binds = options.get("binds") or {}
    if any(bind is not database_engine for bind in binds.values()):
        return False
    if not session_factory_implementation_matches(factory, source_factory):
        return False
    if _factory_options(cast(SessionFactory, factory)) != _factory_options(source_factory):
        return False
    if runtime.settings is not facts.settings_identity:
        return False
    if _settings_values(runtime.execution_settings) != facts.settings_values:
        return False
    if not runtime.connectors.sealed:
        return False
    if runtime.connectors.configuration_facts() != registry.configuration_facts():
        return False
    return runtime.connectors.settings_facts() == _settings_values(runtime.execution_settings)


def _stable_session_factory(
    source_factory: SessionFactory, database_engine: object
) -> SessionFactory:
    if not isinstance(source_factory, sessionmaker):
        raise ValueError("Legacy background execution requires a SQLAlchemy session factory.")
    return clone_session_factory(source_factory, database_engine)


def _submit_sync(function: Callable[..., object], *args: object) -> asyncio.Future[object]:
    """Submit synchronous work while preserving the caller's context variables."""

    context = copy_context()

    def invoke() -> _CompletionResult:
        try:
            return _CompletionResult(value=context.run(function, *args))
        except BaseException as exc:
            return _CompletionResult(error=exc)

    return asyncio.get_running_loop().run_in_executor(None, invoke)


def _completion_result(
    completion: asyncio.Future[object],
    caller_cancellation: asyncio.CancelledError | None,
) -> _CompletionResult:
    """Read a completed future without losing the caller's cancellation object."""

    try:
        value = completion.result()
    except BaseException as exc:
        result = _CompletionResult(error=exc)
    else:
        result = value if isinstance(value, _CompletionResult) else _CompletionResult(value=value)
    return _CompletionResult(
        value=result.value,
        error=result.error,
        caller_cancellation=caller_cancellation,
    )


def _observe_task(task: asyncio.Task[object]) -> asyncio.Future[object]:
    """Convert supervisor termination into data before a caller observes it."""

    completion = asyncio.get_running_loop().create_future()

    def capture(done: asyncio.Task[object]) -> None:
        if completion.done():
            return
        try:
            result = done.result()
        except BaseException as exc:
            completion.set_result(_CompletionResult(error=exc))
        else:
            completion.set_result(_CompletionResult(value=result))

    task.add_done_callback(capture)
    return completion


async def _await_completion(
    completion: asyncio.Future[object], on_cancellation: Callable[[], None]
) -> _CompletionResult:
    """Observe caller cancellation once, then shield the required drain to completion."""

    caller_cancellation: asyncio.CancelledError | None = None
    try:
        await checkpoint_if_cancelled()
        await asyncio.shield(completion)
    except asyncio.CancelledError as exc:
        caller_cancellation = exc
        on_cancellation()
    except BaseException:
        return _completion_result(completion, caller_cancellation)
    else:
        return _completion_result(completion, caller_cancellation)

    while not completion.done():
        with CancelScope(shield=True):
            try:
                await asyncio.shield(completion)
            except asyncio.CancelledError as exc:
                if caller_cancellation is None:
                    caller_cancellation = exc
                on_cancellation()
            except BaseException:
                break
    return _completion_result(completion, caller_cancellation)


async def _drainable_thread_call(
    runtime: ExecutionRuntime,
    function: Callable[..., object],
    *args: object,
) -> object:
    """Keep a handle to synchronous work through cancellation."""

    outcome = await _await_completion(_submit_sync(function, *args), runtime.begin_shutdown)
    if outcome.caller_cancellation is not None:
        raise outcome.caller_cancellation from outcome.error
    if outcome.error is not None:
        raise outcome.error
    return outcome.value


async def drain_background_runtime(
    task: asyncio.Task[object] | None, runtime: ExecutionRuntime
) -> RuntimeDrainOutcome:
    """Stop and drain a supervisor before its owned resources are disposed."""

    runtime.begin_shutdown()
    task_outcome = _CompletionResult()
    if task is not None:
        task_outcome = await _await_completion(_observe_task(task), runtime.begin_shutdown)
    idle_outcome = await _wait_for_runtime_idle(runtime)
    caller_cancellation = task_outcome.caller_cancellation or idle_outcome.caller_cancellation
    return RuntimeDrainOutcome(
        supervisor_error=task_outcome.error,
        drain_error=idle_outcome.error,
        cancelled=caller_cancellation is not None,
        caller_cancellation=caller_cancellation,
    )


async def _wait_for_runtime_idle(runtime: ExecutionRuntime) -> _CompletionResult:
    """Wait for admitted work through repeated caller cancellation."""

    try:
        completion = _submit_sync(runtime.wait_until_pipeline_idle)
    except BaseException as exc:
        # Submission failed before there was an idle-wait completion to observe.
        # Keep earlier supervisor/caller outcomes and retain the failed owner.
        return _CompletionResult(error=exc)
    return await _await_completion(completion, runtime.begin_shutdown)


def start_background_runtime(runtime: ExecutionRuntime) -> asyncio.Task[object]:
    """Start a supervisor whose pre-start cancellation still closes its runtime."""

    global _LEGACY_SUPERVISOR_TASK, _LEGACY_OWNER_KIND

    with _LEGACY_RUNTIME_LOCK:
        if _LEGACY_RUNTIME is runtime:
            if _LEGACY_OWNER_KIND not in {None, "lifespan"}:
                raise RuntimeError("A temporary runtime cannot start a lifespan supervisor.")
            _LEGACY_OWNER_KIND = "lifespan"
        task = asyncio.create_task(run_background_runtime(runtime=runtime))
        if _LEGACY_RUNTIME is runtime:
            _LEGACY_SUPERVISOR_TASK = task

    def on_done(completed: asyncio.Task[object]) -> None:
        if completed.cancelled() or completed.exception() is not None:
            runtime.begin_shutdown()

    task.add_done_callback(on_done)
    return task


def _reject_partial_runtime_overrides(
    *,
    runtime: ExecutionRuntime | None,
    settings: Settings | None = None,
    session_factory: SessionFactory | None = None,
    registry=None,
    lock: threading.Lock | None = None,
    stop_event: threading.Event | None = None,
) -> None:
    """Prevent callers from mixing app-owned resources with legacy globals."""

    if runtime is not None and any(
        value is not None for value in (settings, session_factory, registry, lock, stop_event)
    ):
        raise ValueError("ExecutionRuntime calls cannot include independent resource overrides.")
    if runtime is None and any(value is not None for value in (session_factory, registry, lock)):
        raise ValueError(
            "Background execution requires a complete ExecutionRuntime when overriding resources."
        )


def _legacy_runtime(settings: Settings, stop_event: threading.Event | None) -> ExecutionRuntime:
    """Adapt a settings-only compatibility call to the global owner explicitly."""

    global _LEGACY_RUNTIME, _LEGACY_OWNER_KIND, _LEGACY_GENERATION
    global _LEGACY_TRANSITIONING, _LEGACY_TRANSITION_TOKEN, _LEGACY_SUPERVISOR_TASK

    with _LEGACY_RUNTIME_LOCK:
        if _LEGACY_RUNTIME is None and not _LEGACY_TRANSITIONING:
            _LEGACY_OWNER_KIND = None
            _LEGACY_SUPERVISOR_TASK = None
            _LEGACY_TRANSITIONING = False
            _LEGACY_TRANSITION_TOKEN = None
        if _LEGACY_TRANSITIONING:
            raise RuntimeError("A legacy runtime transition is already in progress.")
        facts, database_engine, source_factory, registry = _legacy_resource_facts(settings)
        existing = _LEGACY_RUNTIME
        if existing is not None:
            if existing.settings is not settings:
                raise ValueError(
                    "Legacy background execution settings do not own the active runtime."
                )
            if stop_event is not None and stop_event is not existing.stop_event:
                raise ValueError(
                    "Legacy background execution event does not own the active runtime."
                )
            existing_facts = getattr(existing, "_legacy_resource_facts", None)
            if existing_facts is None or not _facts_match(existing_facts, facts):
                raise ValueError("Legacy background execution resources changed during ownership.")
            return existing
        stable_factory = cast(
            Callable[[], Session], _stable_session_factory(source_factory, database_engine)
        )
        runtime = ExecutionRuntime(
            settings=settings,
            sessions=stable_factory,
            connectors=cast(ConnectorRegistry, registry).snapshot(settings),
            stop_event=stop_event if stop_event is not None else threading.Event(),
            pipeline_lock=_BACKGROUND_PIPELINE_LOCK,
        )
        runtime._legacy_resource_facts = facts  # type: ignore[attr-defined]
        _LEGACY_RUNTIME = runtime
        _LEGACY_OWNER_KIND = "temporary"
        _LEGACY_GENERATION += 1
        return runtime


def install_legacy_runtime(
    runtime: ExecutionRuntime, *, transition_token: object | None = None
) -> None:
    """Bind the process-global compatibility owner for one runtime generation."""

    global _LEGACY_RUNTIME, _LEGACY_SUPERVISOR_TASK, _LEGACY_OWNER_KIND
    global _LEGACY_TRANSITIONING, _LEGACY_TRANSITION_TOKEN
    with _LEGACY_RUNTIME_LOCK:
        if _LEGACY_TRANSITIONING and _LEGACY_TRANSITION_TOKEN is not transition_token:
            raise RuntimeError("A legacy runtime transition is already in progress.")
        if transition_token is not None and _LEGACY_TRANSITION_TOKEN is not transition_token:
            raise RuntimeError("The legacy runtime transition token is no longer valid.")
        if _LEGACY_RUNTIME is not None and _LEGACY_RUNTIME is not runtime:
            raise RuntimeError("A legacy runtime is already active; drain it before replacement.")
        if not runtime.is_accepting():
            raise RuntimeError("A stopped legacy runtime cannot be installed.")
        if _LEGACY_RUNTIME is runtime:
            if _LEGACY_OWNER_KIND == "closed":
                raise RuntimeError("A closed legacy runtime cannot be reinstalled.")
        facts, database_engine, source_factory, registry = _legacy_resource_facts(runtime.settings)
        if not _runtime_matches_legacy_resources(
            runtime, facts, database_engine, source_factory, cast(ConnectorRegistry, registry)
        ):
            raise ValueError("Legacy runtime resources do not match compatibility ownership.")
        try:
            with runtime.publication_scope():
                if _LEGACY_RUNTIME is runtime:
                    existing_facts = getattr(runtime, "_legacy_resource_facts", None)
                    if existing_facts is None or existing_facts != facts:
                        raise ValueError("Legacy runtime resources changed during ownership.")
                    if (
                        transition_token is None
                        and _LEGACY_OWNER_KIND == "lifespan"
                        and _LEGACY_SUPERVISOR_TASK is not None
                    ):
                        return
                    return
                _LEGACY_OWNER_KIND = None
                _LEGACY_SUPERVISOR_TASK = None
                _LEGACY_RUNTIME = runtime
                runtime._legacy_resource_facts = facts  # type: ignore[attr-defined]
                _LEGACY_OWNER_KIND = "lifespan"
                if transition_token is not None:
                    _LEGACY_TRANSITIONING = False
                    _LEGACY_TRANSITION_TOKEN = None
        except RuntimeAdmissionError as exc:
            raise RuntimeError("A stopped legacy runtime cannot be installed.") from exc


def release_legacy_runtime(runtime: ExecutionRuntime) -> None:
    """Release a drained process-global compatibility owner."""

    global _LEGACY_RUNTIME, _LEGACY_SUPERVISOR_TASK, _LEGACY_OWNER_KIND
    with _LEGACY_RUNTIME_LOCK:
        if _LEGACY_RUNTIME is runtime:
            _LEGACY_RUNTIME = None
            _LEGACY_SUPERVISOR_TASK = None
            _LEGACY_OWNER_KIND = None


def mark_legacy_runtime_closed(runtime: ExecutionRuntime) -> None:
    """Keep a failed-cleanup generation visible for a later coordinated retry."""

    global _LEGACY_OWNER_KIND
    with _LEGACY_RUNTIME_LOCK:
        if _LEGACY_RUNTIME is runtime:
            _LEGACY_OWNER_KIND = "closed"


async def begin_legacy_startup() -> object:
    """Drain the old owner and reserve the publication slot for startup."""

    global _LEGACY_RUNTIME, _LEGACY_SUPERVISOR_TASK, _LEGACY_OWNER_KIND
    global _LEGACY_TRANSITIONING, _LEGACY_TRANSITION_TOKEN
    with _LEGACY_RUNTIME_LOCK:
        runtime = _LEGACY_RUNTIME
        supervisor = _LEGACY_SUPERVISOR_TASK
        if _LEGACY_TRANSITIONING:
            raise RuntimeError("A legacy runtime transition is already in progress.")
        if runtime is None:
            transition_token = object()
            _LEGACY_TRANSITIONING = True
            _LEGACY_TRANSITION_TOKEN = transition_token
            return transition_token
        if _LEGACY_OWNER_KIND == "lifespan":
            raise RuntimeError("A live lifespan-owned runtime cannot be retired by startup.")
        _LEGACY_TRANSITIONING = True
        transition_token = object()
        _LEGACY_TRANSITION_TOKEN = transition_token
        runtime.begin_shutdown()
    try:
        outcome = await drain_background_runtime(supervisor, runtime)
    except BaseException:
        with _LEGACY_RUNTIME_LOCK:
            if _LEGACY_RUNTIME is runtime and _LEGACY_TRANSITION_TOKEN is transition_token:
                _LEGACY_OWNER_KIND = "closed"
            if _LEGACY_TRANSITION_TOKEN is transition_token:
                _LEGACY_TRANSITIONING = False
                _LEGACY_TRANSITION_TOKEN = None
        raise
    if outcome.drain_error is not None:
        with _LEGACY_RUNTIME_LOCK:
            if _LEGACY_RUNTIME is runtime and _LEGACY_TRANSITION_TOKEN is transition_token:
                _LEGACY_OWNER_KIND = "closed"
            if _LEGACY_TRANSITION_TOKEN is transition_token:
                _LEGACY_TRANSITIONING = False
                _LEGACY_TRANSITION_TOKEN = None
        raise_runtime_shutdown_outcome(
            body_error=None,
            drain_error=outcome.drain_error,
            drain_outcome=outcome,
            disposal_error=None,
        )
    with _LEGACY_RUNTIME_LOCK:
        if _LEGACY_RUNTIME is runtime and _LEGACY_TRANSITION_TOKEN is transition_token:
            _LEGACY_RUNTIME = None
            _LEGACY_SUPERVISOR_TASK = None
            _LEGACY_OWNER_KIND = None
    if outcome.cancelled:
        abort_legacy_startup(transition_token)
        cancellation = outcome.caller_cancellation or asyncio.CancelledError()
        raise cancellation from outcome.supervisor_error
    if outcome.supervisor_error is not None:
        abort_legacy_startup(transition_token)
        raise outcome.supervisor_error
    return transition_token


def abort_legacy_startup(transition_token: object) -> None:
    """Release a reserved startup publication slot after startup failed."""

    global _LEGACY_TRANSITIONING, _LEGACY_TRANSITION_TOKEN
    with _LEGACY_RUNTIME_LOCK:
        if _LEGACY_TRANSITION_TOKEN is not transition_token:
            return
        _LEGACY_TRANSITIONING = False
        _LEGACY_TRANSITION_TOKEN = None


async def retire_legacy_runtime() -> None:
    """Drain and release a temporary compatibility generation."""

    transition_token = await begin_legacy_startup()
    abort_legacy_startup(transition_token)


def _runtime_for_call(
    settings: Settings | ExecutionRuntime,
    *,
    stop_event: threading.Event | None = None,
    session_factory: SessionFactory | None = None,
    registry=None,
    lock: threading.Lock | None = None,
) -> ExecutionRuntime:
    runtime = settings if isinstance(settings, ExecutionRuntime) else None
    _reject_partial_runtime_overrides(
        runtime=runtime,
        session_factory=session_factory,
        registry=registry,
        lock=lock,
        stop_event=stop_event,
    )
    if runtime is not None:
        return runtime
    return _legacy_runtime(cast(Settings, settings), stop_event)


def schedule_pipeline_run(
    background_tasks: BackgroundTasks,
    settings: Settings,
    run_id: str,
    stop_event: threading.Event | None = None,
    runtime: ExecutionRuntime | None = None,
) -> None:
    """Attach a queued transfer to the response that created it."""
    if runtime is not None:
        if (settings is not runtime.settings and settings is not runtime.execution_settings) or (
            stop_event is not None and stop_event is not runtime.stop_event
        ):
            raise ValueError("Scheduled runtime calls cannot include conflicting owner facts.")
        settings = runtime.execution_settings
    if runtime is None and settings.app_env == "test":
        return
    resolved_runtime = runtime or _legacy_runtime(settings, stop_event)
    if resolved_runtime.execution_settings.app_env != "test":
        background_tasks.add_task(process_pipeline_run_background, resolved_runtime, run_id)


def _try_acquire_pipeline_lock() -> bool:
    """Avoid consuming a Starlette thread while another in-app task is running."""
    acquired = _BACKGROUND_PIPELINE_LOCK.acquire(blocking=False)
    if not acquired:
        log.debug("Background pipeline task deferred to the in-process supervisor")
    return acquired


def process_pipeline_run_background(
    settings: Settings | ExecutionRuntime,
    run_id: str,
    stop_event: threading.Event | None = None,
    session_factory: SessionFactory | None = None,
    registry=None,
    lock: threading.Lock | None = None,
) -> None:
    """Claim and execute one run after its enqueue response has been sent."""
    from app.database import sqlite_worker_lock
    from app.worker import process_one

    runtime = _runtime_for_call(
        settings,
        stop_event=stop_event,
        session_factory=session_factory,
        registry=registry,
        lock=lock,
    )
    current_settings = runtime.execution_settings
    current_stop_event = runtime.stop_event
    current_factory = runtime.sessions
    current_lock = runtime.pipeline_lock
    operation = runtime.try_admit_pipeline_operation()
    if operation is None:
        return
    try:
        if not current_lock.acquire(blocking=False):
            return
        try:
            with runtime.context():
                with sqlite_worker_lock(current_settings.database_url, "pipeline"):
                    with current_factory() as db:
                        process_one(
                            db,
                            current_settings,
                            run_id=run_id,
                            stop_event=current_stop_event,
                            session_factory=current_factory,
                        )
        finally:
            current_lock.release()
    except Exception:
        # Background task failures happen after the response has been sent;
        # the durable run row and event feed contain the operator-visible state.
        log.exception("Background pipeline run failed", extra={"run_id": run_id})
    finally:
        operation.release()


def process_pending_pipeline_run_background(
    settings: Settings | ExecutionRuntime,
    stop_event: threading.Event | None = None,
    session_factory: SessionFactory | None = None,
    registry=None,
    lock: threading.Lock | None = None,
) -> bool:
    """Recover one queued or expired run from the in-process supervisor."""
    from app.database import sqlite_worker_lock
    from app.worker import process_one

    runtime = _runtime_for_call(
        settings,
        stop_event=stop_event,
        session_factory=session_factory,
        registry=registry,
        lock=lock,
    )
    current_settings = runtime.execution_settings
    factory = runtime.sessions
    stop_event = runtime.stop_event
    current_lock = runtime.pipeline_lock

    operation = runtime.try_admit_pipeline_operation()
    if operation is None:
        return False
    try:
        with current_lock:
            if stop_event.is_set():
                return False
            try:
                with runtime.context():
                    with sqlite_worker_lock(current_settings.database_url, "pipeline"):
                        with factory() as db:
                            return process_one(
                                db,
                                current_settings,
                                stop_event=stop_event,
                                session_factory=factory,
                            )
            except Exception:
                log.exception("Background pipeline recovery cycle failed")
                return False
    finally:
        operation.release()


def run_pipeline_janitor_background(
    settings: Settings | ExecutionRuntime,
    session_factory: SessionFactory | None = None,
    registry=None,
    lock: threading.Lock | None = None,
) -> None:
    """Run retention cleanup without requiring an external janitor service."""
    from app.database import sqlite_worker_lock
    from app.services.pipeline_runs import janitor

    runtime = _runtime_for_call(
        settings,
        session_factory=session_factory,
        registry=registry,
        lock=lock,
    )
    current_settings = runtime.execution_settings
    factory = runtime.sessions
    current_lock = runtime.pipeline_lock
    operation = runtime.try_admit_pipeline_operation()
    if operation is None:
        return
    try:
        with current_lock:
            if runtime.stop_event.is_set():
                return
            try:
                with runtime.context():
                    with sqlite_worker_lock(current_settings.database_url, "pipeline"):
                        with factory() as db:
                            counts = janitor(db, current_settings)
                log.info("Background pipeline janitor completed", extra=counts)
            except Exception:
                log.exception("Background pipeline janitor failed")
    finally:
        operation.release()


async def run_background_runtime(
    settings: Settings | None = None,
    stop_event: threading.Event | None = None,
    session_factory: SessionFactory | None = None,
    registry=None,
    runtime: ExecutionRuntime | None = None,
) -> None:
    """Supervise queued transfers and periodic retention inside the web process."""
    global _LEGACY_OWNER_KIND, _LEGACY_SUPERVISOR_TASK
    next_janitor = 0.0
    standalone = runtime is None
    if runtime is not None:
        _reject_partial_runtime_overrides(
            runtime=runtime,
            settings=settings,
            session_factory=session_factory,
            registry=registry,
            stop_event=stop_event,
        )
        active_runtime = runtime
    else:
        _reject_partial_runtime_overrides(
            runtime=None,
            session_factory=session_factory,
            registry=registry,
            lock=None,
        )
        if settings is None or stop_event is None:
            raise ValueError("Legacy background execution requires settings and a stop event.")
        active_runtime = _legacy_runtime(settings, stop_event)
        current_task = asyncio.current_task()
        with _LEGACY_RUNTIME_LOCK:
            if _LEGACY_RUNTIME is not active_runtime:
                raise RuntimeError("Legacy runtime changed before supervisor registration.")
            if _LEGACY_OWNER_KIND == "lifespan":
                raise RuntimeError("A lifespan-owned runtime cannot start a standalone supervisor.")
            if _LEGACY_SUPERVISOR_TASK is not None and not _LEGACY_SUPERVISOR_TASK.done():
                raise RuntimeError("A standalone legacy supervisor is already active.")
            _LEGACY_OWNER_KIND = "standalone"
            _LEGACY_SUPERVISOR_TASK = current_task
    settings = active_runtime.execution_settings
    stop_event = active_runtime.stop_event
    body_error: BaseException | None = None
    try:
        while not stop_event.is_set():
            await _drainable_thread_call(
                active_runtime, process_pending_pipeline_run_background, active_runtime
            )
            if stop_event.is_set():
                break
            now = time.monotonic()
            if now >= next_janitor:
                await _drainable_thread_call(
                    active_runtime, run_pipeline_janitor_background, active_runtime
                )
                next_janitor = now + settings.pipeline_janitor_interval_seconds
            await _drainable_thread_call(
                active_runtime, stop_event.wait, settings.pipeline_background_poll_seconds
            )
    except BaseException as exc:
        body_error = exc
        raise
    finally:
        active_runtime.begin_shutdown()
        if standalone:
            idle_outcome = await _wait_for_runtime_idle(active_runtime)
            drain_outcome = RuntimeDrainOutcome(
                drain_error=idle_outcome.error,
                cancelled=idle_outcome.caller_cancellation is not None,
                caller_cancellation=idle_outcome.caller_cancellation,
            )
            if idle_outcome.error is None:
                release_legacy_runtime(active_runtime)
            else:
                mark_legacy_runtime_closed(active_runtime)
            raise_runtime_shutdown_outcome(
                body_error=body_error,
                drain_error=idle_outcome.error,
                drain_outcome=drain_outcome,
                disposal_error=None,
            )
