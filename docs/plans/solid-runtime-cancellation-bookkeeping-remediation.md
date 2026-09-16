# SOLID runtime review: caller cancellation bookkeeping

Status: implemented; B1 is closed. The [full-refactor remediation](solid-full-refactor-review-remediation.md)
and [drain-failure remediation](solid-runtime-drain-failure-remediation.md) record later runtime
acceptance evidence. The
[AnyIO cancellation propagation implementation](solid-runtime-anyio-cancellation-remediation.md)
remains implemented; this record adds the caller-accounting acceptance evidence. Planned and
implemented on 2026-09-16.

## Scope and reproduced finding

Fix the single P2 finding in `ExecutionRuntime.run_owned_sync`, identified here as B1. Preserve
executor submission, runtime/session context, admission lifetime, worker outcomes, and the initial
AnyIO cancellation checkpoint. This is a focused runtime fix; the broader SOLID migration remains
unfinished. Preserve the existing dirty worktree and pre-existing pipeline lock artifact.

The review passed 65 lifecycle, pipeline-task, SOLID-boundary, and connector-runtime tests, plus
Ruff and `git diff --check`. A bounded probe reproduced the following sequence:

1. A task enters `asyncio.timeout()` and starts an owned synchronous worker.
2. Another task calls `Task.cancel("independent task cancellation")` before the timeout expires.
3. The worker stays blocked while the adapter catches cancellation and drains its completion.
4. The timeout expires and issues its own cancellation during that drain.
5. After worker cleanup, the adapter re-raises the original caller cancellation. The surrounding
   timeout incorrectly changes it to `TimeoutError`.

Both cancellation handlers call `caller.uncancel()`. They erase cancellation counts that the
enclosing timeout uses to distinguish its own request from independent requests. Re-raising an
exception does not restore those counts. The timeout's exit handler therefore concludes that its
request was the only cancellation. A separate in-memory copy of the adapter with only these calls
removed produced the expected original `CancelledError`, with worker cleanup and idle ownership
preserved. That diagnostic is evidence for the proposed fix, not permanent regression coverage.

## Implementation and acceptance evidence

`run_owned_sync` now records and defers cancellation while draining the submitted executor future,
without calling `Task.uncancel()`. The calling timeout or cancellation scope therefore retains its
own cancellation accounting. The initial AnyIO checkpoint, shielded drain, explicit worker outcome,
copied context, and operation-scope finalizer remain unchanged.

Four permanent lifecycle cases now cover the finding: two independent-cancellation plus timeout
cases for worker success and worker failure, a timeout-only control, and repeated direct
cancellation. They use public timeout rescheduling and event-controlled worker/cleanup ordering.
They assert terminal exception classification, cancellation counts, one worker invocation,
worker-error identity, cleanup completion, and idle admission.

The final focused suites passed 69 tests. `make check` passed Ruff, formatting, BasedPyright,
Hedron, Posit, 465 application tests with 31 deselected at 84.00% coverage, and 25 demo tests.
Documentation tests, local-link validation, and `git diff --check` passed. `make hedron-build`
produced digest `c0123a9179acac33da4470eaec37879f5df3a884f43e67faa4d512c78670a929`.

## Required behavior

| Scenario | Required terminal outcome | Ownership and bookkeeping |
|---|---|---|
| Independent `Task.cancel()` followed by timeout expiry during drain | Original `CancelledError`, including its message; no conversion to `TimeoutError` | Cleanup finishes first; external cancellation count remains available to the caller's owning scopes |
| Timeout expiry without independent cancellation | Outer `asyncio.timeout()` raises `TimeoutError` | Same worker drains; the timeout consumes its own cancellation count |
| Repeated independent cancellation during drain | Original caller cancellation remains primary | Each external request remains reflected in the task count; no resubmission or early lease release |
| Independent cancellation, timeout expiry, and worker/cleanup failure | Original caller cancellation with the exact worker error as its cause | Failure survives full cleanup and cancellation classification |
| AnyIO scope cancellation or `fail_after()` expiry | Existing cancellation/timeout behavior | AnyIO manages its own cancellation counts; drain stays shielded without a busy retry loop |
| Success, ordinary worker failure, worker-originated cancellation, or submission rejection | Existing outcomes | Context and admission are released exactly once |

Synchronous work still must finish before cancellation reaches the caller. A timeout can remain
pending beyond its deadline while the adapter drains owned work; this fix changes classification,
not that cleanup guarantee.

## Step 1: add deterministic failing regressions

Primary file: `tests/test_pipeline_lifecycle.py`.

1. Add `test_independent_cancellation_survives_timeout_during_owned_worker_drain` using a worker
   with threading events for entry, work release, cleanup entry, and cleanup release. The child
   task enters `asyncio.timeout(None)` and exposes that timeout object to an observer outside it.
2. Wait for worker entry, call `operation.cancel()` with a distinctive message, and yield to let
   the child receive the first cancellation. Assert the operation remains pending, its runtime
   remains owned, and its external cancellation count has not been consumed. This count assertion
   directly catches the reviewed bookkeeping defect; terminal outcome assertions remain essential.
3. Use the public `timeout.reschedule(loop.time())` API to expire the timeout while the worker is
   blocked. Wait with a bound until `timeout.expired()` is true. Assert the operation remains
   pending and ownership is still live. Use scheduling checkpoints and events to establish
   ordering, without relying on a short wall-clock gap between two timers.
4. Release work but hold cleanup. Confirm neither the operation nor a runtime drain can finish
   until cleanup is released. Then release cleanup and require the original `CancelledError`
   message, a cancelled task, and idle runtime ownership. The timeout should have consumed only
   its own request, leaving the independent request reflected in `Task.cancelling()`.
5. Add a timeout-only control with the same controlled expiry and cleanup ordering. Require
   `TimeoutError`, completed cleanup, idle ownership, and cancellation bookkeeping restored to
   the task's entry baseline by the timeout itself.
6. Add a repeated independent cancellation case during drain. Establish receipt of each request
   through scheduling checkpoints and task-count assertions. Require the first cancellation
   message, retained independent counts, one worker invocation, and no early cleanup release.
7. Parameterize the independent-cancellation/timeout regression with worker success and a specific
   worker failure instance. In the failure case, require `CancelledError.__cause__` to be that
   instance. Use the existing context-cleanup failure fixture if one is available; otherwise
   cover cleanup failure with a small context-manager fixture in this test module.
8. Always release work and cleanup events in `finally`, bound observer waits, and await child/drain
   tasks before disposing resources. Put safety bounds on worker event waits so a failing test
   cannot leave an executor thread blocked indefinitely. Cleanup must also run when assertions
   fail against the current implementation.

Acceptance: implemented. The former bookkeeping behavior changes independent cancellation into
`TimeoutError`; the permanent regressions now preserve caller classification, timeout ownership,
repeated cancellation, and worker-error retention.

## Step 2: preserve caller-owned cancellation accounting

Primary file: `app/infrastructure/runtime.py`, inside `run_owned_sync`.

1. Remove `caller.uncancel()` from both the initial cancellation handler and the drain retry
   handler. Remove the now-unused `caller = asyncio.current_task()` local. Do not restore counts
   by issuing synthetic `Task.cancel()` calls; that would introduce additional cancellation
   deliveries and alter the caller's owning scopes.
2. Continue recording the first `CancelledError` separately from the worker outcome. Preserve
   that exact exception, its cancellation message, and its eventual worker-error cause.
3. Keep the first `checkpoint_if_cancelled()` and `asyncio.shield(completion)` outside the AnyIO
   shield. Keep `CancelScope(shield=True)` around subsequent completion waits so AnyIO level
   cancellation cannot repeatedly interrupt drain.
4. Continue catching repeated direct asyncio cancellation during drain and retrying the same
   shielded future without changing task cancellation counts. Catching a delivered cancellation
   allows subsequent awaits; an outstanding count alone does not continually redeliver it.
5. Preserve single submission, copied context, explicit terminal worker outcomes, actual thread
   and context cleanup, and the existing outer scope finalizer. Do not cancel the executor future
   or create another driver task.
6. Add a short comment explaining that the adapter defers cancellation during cleanup and leaves
   cancellation accounting to caller-owned timeout/cancellation scopes. Avoid accessing private
   asyncio or AnyIO cancellation state or synthesizing `TimeoutError` in this adapter.

Acceptance: implemented. Caller counts remain available to enclosing scopes; existing AnyIO
timeout, worker-error-cause, worker-originated cancellation, submission rejection, and thread-drain
tests retain their behavior. Worker cleanup and lease release occur before the final caller
exception.

## Step 3: validate the final implementation and update acceptance

1. Run the new regressions and the complete focused suites:

   ```sh
   .venv/bin/python -m pytest tests/test_pipeline_lifecycle.py tests/test_pipeline_tasks.py tests/test_solid_boundaries.py tests/test_connector_runtime.py
   ```

2. Check AnyIO scope cancellation and `fail_after()` controls specifically after removing manual
   count changes. Confirm repeated direct cancellation cannot release ownership early and does
   not cause a busy retry loop. Retain ordinary success/failure and shutdown-drain controls.
3. Run `make check` once on the final code, covering Ruff, formatting, BasedPyright, Hedron/Posit,
   the full application suite with the unchanged 80% coverage floor, and the demo suite. Record
   fresh passed/deselected counts, coverage, and demo results; the earlier 65-test review is
   historical evidence, not final acceptance.
4. Run `make hedron-build`, documentation tests, changed-document local-link validation, and
   `git diff --check`. Record the build digest and any environment-dependent skips.
5. Update this plan, the plan index, the major SOLID plan, and the A1 status note together. Close
   B1 and runtime cancellation acceptance only after permanent regressions and integrated checks
   pass. Retain previous implementation evidence and the unfinished migration status.

## Definition of done

B1 is complete. Independent cancellation followed by timeout expiry remains the original
`CancelledError`; timeout-only expiry becomes `TimeoutError`; cancellation counts remain owned by
calling scopes; worker failures survive as causes; and cleanup, context, and admission ownership
remain correct through repeated cancellation. Permanent regressions and final integrated checks
passed.
