"""Instance-owned execution dependencies for background and deferred work."""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar, copy_context
from copy import deepcopy
from dataclasses import dataclass, field

from anyio import CancelScope
from anyio.lowlevel import checkpoint_if_cancelled
from sqlalchemy.orm import Session

from app.config import Settings
from app.connectors.registry import ConnectorRegistry, registry_context, settings_facts


class RuntimeAdmissionError(RuntimeError):
    """Raised when work is requested from a runtime that is no longer accepting it."""


_ACTIVE_OPERATION_SCOPE: ContextVar[RuntimeOperationScope | None] = ContextVar(
    "access_registry_runtime_operation_scope", default=None
)


class PipelineOperationLease:
    """One admitted operation that must be released after all owned cleanup."""

    def __init__(self, lifetime: PipelineOperationLifetime) -> None:
        self._lifetime = lifetime
        self._released = False

    def release(self) -> None:
        with self._lifetime._condition:
            if self._released:
                return
            self._released = True
            self._lifetime._release_locked()

    def retain(self) -> PipelineOperationLease:
        """Create a separately releasable lease for delegated work."""

        with self._lifetime._condition:
            if self._released:
                raise RuntimeAdmissionError("The parent operation lease is already released.")
            self._lifetime._retain_locked()
            return PipelineOperationLease(self._lifetime)

    def __enter__(self) -> PipelineOperationLease:
        return self

    def __exit__(self, *_: object) -> None:
        self.release()


class PipelineOperationLifetime:
    """Coordinate admission and draining for one runtime generation."""

    def __init__(self) -> None:
        self._condition = threading.Condition()
        self._accepting = True
        self._active = 0

    def try_admit(self, stop_event: threading.Event) -> PipelineOperationLease | None:
        with self._condition:
            if not self._accepting or stop_event.is_set():
                return None
            self._active += 1
            return PipelineOperationLease(self)

    def begin_shutdown(self, stop_event: threading.Event) -> None:
        with self._condition:
            self._accepting = False
            stop_event.set()
            self._condition.notify_all()

    def wait_until_idle(self) -> None:
        with self._condition:
            while self._active:
                self._condition.wait()

    def is_idle(self) -> bool:
        with self._condition:
            return self._active == 0

    def _retain_locked(self) -> None:
        self._active += 1

    def _release_locked(self) -> None:
        if self._active <= 0:
            raise RuntimeError("Pipeline operation lifetime was released too many times.")
        self._active -= 1
        if self._active == 0:
            self._condition.notify_all()


@dataclass
class RuntimeOperationScope:
    """One runtime admission that may be borrowed by nested synchronous work."""

    runtime: ExecutionRuntime
    lease: PipelineOperationLease
    owner: bool = True
    active: bool = True

    def close(self) -> None:
        if not self.active:
            return
        self.active = False
        if self.owner:
            self.lease.release()


@dataclass(frozen=True)
class _WorkerOutcome:
    """The terminal result of synchronous work after its runtime cleanup."""

    value: object = None
    error: BaseException | None = None


@dataclass
class ExecutionRuntime:
    """Resources captured by one application instance and its worker tasks."""

    settings: Settings
    sessions: Callable[[], Session]
    connectors: ConnectorRegistry
    stop_event: threading.Event = field(default_factory=threading.Event)
    pipeline_lock: threading.Lock = field(default_factory=threading.Lock)
    lifetime: PipelineOperationLifetime = field(default_factory=PipelineOperationLifetime)
    execution_settings: Settings = field(init=False)

    def __post_init__(self) -> None:
        """Freeze settings used by admitted work for this runtime generation."""

        self.execution_settings = deepcopy(self.settings)
        if not self.connectors.sealed:
            self.connectors = self.connectors.snapshot(self.execution_settings)
        elif self.connectors.settings_facts() != settings_facts(self.execution_settings):
            raise ValueError("A published connector generation is bound to different settings.")

    def try_admit_pipeline_operation(self) -> PipelineOperationLease | None:
        return self.lifetime.try_admit(self.stop_event)

    def is_accepting(self) -> bool:
        with self.lifetime._condition:
            return self.lifetime._accepting and not self.stop_event.is_set()

    @contextmanager
    def publication_scope(self) -> Iterator[None]:
        """Keep shutdown from racing a coordinator publication decision."""

        with self.lifetime._condition:
            if not self.lifetime._accepting or self.stop_event.is_set():
                raise RuntimeAdmissionError("The execution runtime is no longer accepting work.")
            yield

    @contextmanager
    def operation_scope(self) -> Iterator[RuntimeOperationScope]:
        """Admit one operation or borrow the live admission of this runtime."""

        current = _ACTIVE_OPERATION_SCOPE.get()
        if current is not None and current.runtime is self and current.active:
            borrowed = RuntimeOperationScope(self, current.lease.retain())
            token = _ACTIVE_OPERATION_SCOPE.set(borrowed)
            try:
                yield borrowed
            finally:
                _ACTIVE_OPERATION_SCOPE.reset(token)
                borrowed.close()
            return
        lease = self.try_admit_pipeline_operation()
        if lease is None:
            raise RuntimeAdmissionError("The execution runtime is no longer accepting work.")
        scope = RuntimeOperationScope(self, lease)
        token = _ACTIVE_OPERATION_SCOPE.set(scope)
        try:
            yield scope
        finally:
            _ACTIVE_OPERATION_SCOPE.reset(token)
            scope.close()

    def begin_shutdown(self) -> None:
        self.lifetime.begin_shutdown(self.stop_event)

    def wait_until_pipeline_idle(self) -> None:
        self.lifetime.wait_until_idle()

    async def run_owned_sync(self, function: Callable[..., object], *args: object) -> object:
        """Run synchronous work with a lease that survives request cancellation."""

        scope_manager = self.operation_scope()
        scope: RuntimeOperationScope | None = None
        try:
            try:
                scope = scope_manager.__enter__()
                worker_context = copy_context()

                def invoke() -> _WorkerOutcome:
                    token = _ACTIVE_OPERATION_SCOPE.set(scope)
                    try:
                        with self.context():
                            return _WorkerOutcome(value=function(*args))
                    except BaseException as exc:
                        return _WorkerOutcome(error=exc)
                    finally:
                        _ACTIVE_OPERATION_SCOPE.reset(token)

                def invoke_with_context() -> _WorkerOutcome:
                    try:
                        return worker_context.run(invoke)
                    except BaseException as exc:
                        return _WorkerOutcome(error=exc)

                completion = asyncio.get_running_loop().run_in_executor(None, invoke_with_context)
            except BaseException:
                # Executor submission can fail before a thread owns any cleanup.
                # The enclosing finally closes the acquired admission immediately.
                raise
            cancelled = False
            caller_cancellation: asyncio.CancelledError | None = None

            async def await_completion() -> _WorkerOutcome:
                """Observe caller cancellation, then drain the submitted worker."""

                nonlocal caller_cancellation, cancelled
                try:
                    await checkpoint_if_cancelled()
                    return await asyncio.shield(completion)
                except asyncio.CancelledError as exc:
                    cancelled = True
                    caller_cancellation = exc
                while not completion.done():
                    try:
                        with CancelScope(shield=True):
                            return await asyncio.shield(completion)
                    except asyncio.CancelledError:
                        # Cleanup defers cancellation, but caller-owned timeout and
                        # cancellation scopes must retain their cancellation counts.
                        pass
                return completion.result()

            outcome = await await_completion()
            if cancelled:
                assert caller_cancellation is not None
                raise caller_cancellation from outcome.error
            if outcome.error is not None:
                raise outcome.error
            return outcome.value
        finally:
            if scope is not None:
                scope_manager.__exit__(None, None, None)

    @contextmanager
    def context(self) -> Iterator[ExecutionRuntime]:
        """Bind this runtime while synchronous work executes in any thread."""

        from app.database import bind_runtime, unbind_runtime

        session_token = bind_runtime(self)
        try:
            with registry_context(self.connectors, self.execution_settings):
                yield self
        finally:
            unbind_runtime(session_token)
