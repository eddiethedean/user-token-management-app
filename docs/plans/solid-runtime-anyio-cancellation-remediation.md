# SOLID runtime review: AnyIO cancellation propagation

Status: implemented; A1 is closed. B1 subsequently closed runtime cancellation acceptance in the
[caller cancellation bookkeeping plan](solid-runtime-cancellation-bookkeeping-remediation.md). This
plan follows review of
the [thread completion and session authority implementation](solid-runtime-thread-session-authority-remediation.md).
Implemented and accepted on 2026-09-16.

## Scope and verified evidence

Fix the single P2 finding in `ExecutionRuntime.run_owned_sync`. Preserve the single submitted
executor operation, actual thread-completion ownership, copied runtime context, explicit worker
outcomes, submission-failure cleanup, and session-authority validation. The fix needs no schema,
deployment, new executor, or broader SOLID migration. Preserve the existing dirty worktree and
pre-existing artifacts.

The latest review passed 56 lifecycle/task/SOLID tests and `git diff --check`. Bounded probes showed
that an enclosing AnyIO cancellation scope was shielded during the initial worker wait. Permanent
regressions now cover the fix. The final integrated gate passed 461 application tests with 31
deselected at 83.96% coverage, alongside 25 demo tests:

| Scenario | Current result | Required result |
|---|---|---|
| Cancel a task group while its worker is blocked, then release it | The operation returns worker success | Drain the same worker, then propagate caller cancellation |
| Let `anyio.fail_after()` expire during worker execution | The operation returns successfully and the scope does not raise `TimeoutError` | Finish worker cleanup, propagate cancellation, and allow the outer timeout scope to raise `TimeoutError` |
| Cancel the caller's AnyIO scope, then let the worker fail | The operation raises `ValueError` with no cancellation cause | Caller cancellation is primary; the original worker failure remains its cause or existing secondary evidence |

The original T1 completion-ownership and T2 session-authority fixes remain implemented. A1 resolves
caller-cancellation propagation.

## Implementation and acceptance evidence

`run_owned_sync` now checks for caller cancellation before entering the AnyIO cleanup shield. It
preserves the original cancellation exception through executor cleanup, so AnyIO can recognize its
own cancellation scope and translate an expired `fail_after` scope to `TimeoutError`. The shield
continues to protect the original executor future during drain, while caller cancellation remains
primary and a worker failure is retained as its cause.

The focused lifecycle/task/SOLID suites passed 58 tests. The final `make check` passed Ruff,
formatting, BasedPyright, Hedron and Posit checks, 461 application tests (31 deselected), the 80%
coverage floor at 83.96%, and 25 demo tests. `make hedron-build`, documentation tests, local-link
validation, and `git diff --check` also passed.

## Step 1: add outcome-sensitive failing regressions

Primary file: `tests/test_pipeline_lifecycle.py`.

1. Strengthen `test_anyio_cancellation_drains_owned_worker_without_repeated_shields`. Capture the
   child operation's terminal outcome and require cancellation rather than successful return.
   Re-raise observed cancellation so the surrounding AnyIO scope processes it normally. Retain
   the bounded shield-count and idle-admission assertions.
2. Add an AnyIO task-group cancellation test with a blocked worker and blocked cleanup. Establish
   entry using events; cancel the group; confirm ownership remains live until cleanup is released.
   Assert that the caller does not receive a result and the runtime is idle only after cleanup.
3. Add a real `anyio.fail_after()` regression. Arrange for the deadline to expire while work is
   blocked, then release work using an observer outside that cancelled scope. Require
   `TimeoutError` after cleanup. The timeout is delayed until cleanup, because synchronous work
   cannot safely be abandoned; it must not be silently suppressed.
4. Add a worker-failure-during-scope-cancellation test. Retain a specific error instance raised by
   the worker; assert caller cancellation is primary and that exact instance survives as its
   cause/secondary evidence. Include a session/context cleanup failure where applicable.
5. Cover cancellation while work is queued, repeated direct asyncio cancellation during the
   shielded drain, nested operation scopes, and cancellation racing terminal worker completion.
   Verify active ownership returns to its original baseline exactly once.
6. Preserve positive controls: ordinary success, ordinary failure without caller cancellation,
   worker-originated `CancelledError`, standalone asyncio callers, and a runtime that remains
   accepting after request cancellation unless shutdown independently began.
7. Use events/barriers for worker and cleanup ordering, bound observer waits, and always release
   blocked work in `finally`. Await worker completion before disposing resources. Avoid a test
   whose only proof of cancellation is low shield-call counts or an idle lifetime.

Acceptance: implemented and verified by the permanent success, timeout, and worker-failure regressions.

## Step 2: separate the initial wait from the cancellation drain

Primary file: `app/infrastructure/runtime.py`.

1. Keep executor submission and scope entry/exit unchanged. Continue submitting exactly once,
   carrying copied context, returning explicit worker outcomes after full cleanup, and releasing
   admission deterministically if submission fails.
2. Perform the initial await with `asyncio.shield(completion)` outside an AnyIO shielding scope.
   This protects the executor future from caller cancellation while permitting the surrounding
   AnyIO scope to deliver cancellation to the caller.
3. On the first observed caller cancellation, record that cancellation separately from the worker
   outcome. Enter the AnyIO shield only for the subsequent drain of the original completion future.
   Do not infer caller cancellation from the worker's outcome, create a driver task, cancel the
   executor future, or resubmit the worker.
4. During the drain, shield the executor future from direct asyncio cancellation as well. Consume
   repeated cancellation deliveries using the existing supported task cancellation-count rules,
   while retaining the original caller-cancelled state. Prevent AnyIO level cancellation from
   producing repeated immediate retries or starving the event loop.
5. Await only the original nonterminal completion handle and collect its terminal outcome once.
   Exercise cancellation-before-first-wait and terminal-completion races. If a public cancellation
   checkpoint is necessary to observe an already-cancelled AnyIO scope, place it within the same
   cancellation-handling boundary so it cannot bypass ownership or worker-error collection.
6. After actual worker and runtime context cleanup, release admission through the existing scope
   finalizer. Propagate caller cancellation as primary, retaining the original worker/cleanup
   error as its cause or existing secondary evidence. Without caller cancellation, preserve the
   original worker value or error, including worker-originated cancellation.
7. Let the enclosing AnyIO timeout/cancellation scope apply its own semantics. Do not synthesize
   `TimeoutError` inside the runtime adapter, mutate an outer scope, or stop the application runtime
   because one request was cancelled.

Acceptance: implemented. AnyIO cancellation reaches the initial wait; cleanup remains shielded and
owned; timeouts raise after cleanup; cancellation takes precedence over worker failure; standalone
asyncio and normal worker outcomes retain their behavior.

## Step 3: integrated validation and acceptance records

1. Run the new and strengthened regressions on the final implementation, then the full lifecycle,
   pipeline-task, and SOLID boundary suites. Record observed terminal outcomes, original failure
   identity, cleanup completion, active lease counts, and bounded shield attempts.
2. Verify route/request ownership and global/composed lifespan drain behavior affected by this
   adapter. Preserve T1/T2, executor-submission rejection, captured scheduling settings, and the
   shutdown-before-publication reservation regression.
3. Run `make check` and `make hedron-build`. Record fresh application passed/deselected counts,
   coverage against the unchanged 80% floor, demo results, Ruff/formatting, BasedPyright,
   Hedron/Posit results, and build digest. Earlier passing gates remain historical.
4. Run documentation tests, validate local links in changed records, and run `git diff --check`.
   Preserve existing artifacts, including the pre-existing pipeline lock file.
5. Update this plan, the plan index, the major SOLID plan, and the thread/session implementation
   record together. Close A1 and caller-cancellation acceptance only after permanent regressions
   and the integrated gates pass. Keep the unfinished major SOLID migration explicit.

Definition of done: complete. A cancelled AnyIO caller receives cancellation after actual cleanup;
an expired `fail_after` scope raises `TimeoutError`; worker failures survive as secondary evidence;
the same executor operation remains owned throughout; regression and integrated gates pass.
