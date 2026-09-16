# SOLID runtime review: thread completion and session authority

Status: implemented; T1/T2 and follow-on A1 acceptance are closed. The
[AnyIO cancellation propagation plan](solid-runtime-anyio-cancellation-remediation.md) records
the final cancellation evidence. This plan follows review of the
[worker/publication implementation](solid-runtime-worker-publication-remediation.md).
Implementation and gates recorded on 2026-09-16.

## Scope and verified evidence

Fix the two P1 findings from the latest review. Preserve existing request and worker behavior,
instance-owned runtimes, settings-only compatibility, resource publication synchronization,
captured scheduling settings, and shutdown exception precedence. Preserve the dirty worktree and
pre-existing artifacts. No schema change, new worker pool, deployment, or broader SOLID migration
is required.

The review passed 40 lifecycle/task tests and `git diff --check`. Separate bounded probes reproduced
both failures. The permanent regressions below now cover the fixes. The final integrated gate passed
459 application tests with 31 deselected at 83.98% coverage, alongside 25 demo tests.

| ID | Priority | Reproduced failure | Required result |
|---|---|---|---|
| T1 | P1 | Cancel the request and its `asyncio.to_thread` driver after worker entry. Runtime drain returns with zero active leases while the thread's cleanup has not finished. Cancelling only the driver also releases admission early. | Admission and cancellation cleanup depend on actual synchronous completion, including session and runtime context cleanup. A cancelled async task is insufficient evidence of thread completion. |
| T2 | P1 | A real sessionmaker declares engine A but uses a subclass of the source session class whose `get_bind()` returns engine B. Installation succeeds and the installed session routes to B. | Compatibility publication accepts the verified source session implementation and supported stable clones; it rejects additional session behavior that changes database authority. |

T1 resolved W1's completion-ownership acceptance. T2 resolved W2's proposed-resource authority
acceptance. The existing W3 publication guard, W4 deterministic submission cleanup, and W5 captured
scheduling behavior remain covered and passing.

## Implementation and acceptance evidence

The results below record the T1/T2 gate. Follow-on A1 verification fixed AnyIO cancellation
suppression during the initial worker wait; final outcome evidence is recorded in the linked plan.

`run_owned_sync` now submits a single executor operation and waits on its shielded completion future;
it does not create a cancellable `asyncio.to_thread` driver task. The worker returns an explicit
value-or-error outcome only after runtime context cleanup. Cancellation drains that same operation
before releasing admission, while executor submission failure still releases admission immediately.

Legacy publication now compares the normalized underlying SQLAlchemy session implementation, not
only subclass relationship. SQLAlchemy's generated thin sessionmaker layers are accepted, while a
proposal adding `get_bind()` behavior is rejected before it can publish. Shared factory cloning keeps
the source implementation intact for both compatibility and standard runtime construction.

The focused lifecycle/task suites passed 41 tests. The final `make check` passed Ruff, formatting,
BasedPyright, Hedron and Posit checks, 459 application tests (31 deselected), the 80% coverage floor
at 83.98%, and 25 demo tests. `make hedron-build`, documentation tests, local-link validation, and
`git diff --check` also passed.

## Delivery order

| Step | Deliverable | Depends on |
|---|---|---|
| 1 | Permanent failing reproductions and positive controls for T1/T2 | None |
| 2 | Executor completion and explicit worker outcomes | Step 1 |
| 3 | Source session implementation validation and shared clone rules | Step 1 |
| 4 | Integrated regression gate and reconciled acceptance records | Steps 2–3 |

Each behavioral change and its regressions are complete. This plan does not close the unfinished
major SOLID migration.

## Step 1: preserve both failures as regressions

Primary files: `tests/test_pipeline_lifecycle.py` and `tests/test_pipeline_tasks.py`.

### T1: completion ownership

1. Use real events to establish worker entry, blocked work, session cleanup, context cleanup, and
   final completion. Begin runtime drain while work or cleanup is blocked. Assert that drain and
   resource disposal remain pending until cleanup completes.
2. Reproduce independent driver cancellation and simultaneous request/driver cancellation on the
   current implementation. Release the worker in `finally` and await completion before disposing
   databases or ending the event loop. Use bounded observer waits; avoid sleep-only race proofs.
3. Include a shutdown test that cancels the operation's asyncio tasks as a group. After removing
   the driver, adapt this test to assert that there is no independently cancellable driver task,
   the request still drains its submitted work, and cleanup precedes disposal. Do not force tests
   to depend on a driver that the fix deliberately removes.
4. Assert both root and nested ownership: the worker's lease remains live during cleanup, a nested
   operation preserves its parent's lease, and all counts return to baseline exactly once.
5. Retain submission exceptions and tracebacks while checking immediate admission release and
   context restoration. Test rejection at the actual submission boundary used by the final code.

### T2: session authority

1. Build distinct real engines and real sessionmakers. Create a proposed session class extending
   the source factory's class with `get_bind()` redirected to the foreign engine. Match the
   declared bind, settings, options, and connector registry so class behavior is the only mismatch.
2. Require installation to reject before opening a session or constructing a provider. Instrument
   invocation to prove validation itself performs no session/provider I/O.
3. Snapshot coordinator runtime, owner kind, supervisor, generation, reservation/token, and the
   proposal's captured facts. Rejection must preserve every field, including a matching active
   startup reservation. Demonstrate reservation abort/retry afterward.
4. Add positive controls for the source factory, `app.database.stable_session_factory()`, the
   compatibility clone constructor, matching connector snapshots, global lifespan startup, and
   instance-owned runtimes outside the legacy publication boundary.

Record the failures before implementation. Keep test cleanup reliable even when an assertion fails.

## Step 2: replace driver-task completion with executor completion (T1)

Primary files: `app/infrastructure/runtime.py`, `tests/test_pipeline_lifecycle.py`, and any affected
submission tests. The route adapter in `app/ui/routes/pipeline_context.py` should retain its API.

1. Remove the extra `asyncio.create_task(asyncio.to_thread(...))` driver from `run_owned_sync`.
   Submit once through the running loop's executor API and await its completion future directly
   through `asyncio.shield`. Reuse the loop's default executor; do not introduce a separate pool,
   depend on private loop internals, or change the public call signature.
2. Capture `contextvars.copy_context()` at submission and execute the worker wrapper in that
   context. Preserve the existing active operation scope, runtime database binding, connector
   registry binding, captured settings, and restoration of previous context values.
3. Return an explicit worker outcome containing either a value or the original exception. Catch
   worker `BaseException` at the wrapper's outer boundary, after callable and runtime-context
   cleanup have unwound. A worker-originated `CancelledError` must be outcome data until the
   caller deliberately propagates it; it must not mark the completion future cancelled.
4. Keep the whole post-admission interval under deterministic `try/finally`. If executor submission
   fails, close the acquired scope immediately, restore context, and propagate the submission
   error even while its traceback is retained. No worker, coroutine, or extra task should escape.
5. Never cancel the submitted executor future during caller cancellation. Keep it private and
   shield each await. Drain the same submitted operation, including work queued before thread
   entry, until its outcome is available. Do not create replacement workers or polling threads.
6. Keep an AnyIO cancellation shield around the cancellation drain. Handle repeated direct
   `Task.cancel()` requests without spinning or discarding the original completion handle. Track
   caller cancellation separately from worker failure and preserve existing cancellation-count
   behavior at the surrounding task boundary.
7. Close the operation scope only after actual wrapper completion. Replace `task.done()` as the
   proof of cleanup with the outcome from the noncancelled executor completion future. If any
   async driver is retained instead, it must have an independent actual-thread completion signal
   and submission handle; a terminal driver alone must never authorize scope release.
8. After cleanup, preserve outcome precedence: caller cancellation is primary, with worker or
   cleanup failure retained as its cause/secondary evidence. Otherwise return the original value
   or propagate the original worker exception. Request cancellation must not stop the runtime.
9. Update the existing task-factory rejection regression to exercise executor submission rejection.
   Keep the retained-traceback assertion. Add a control proving a rejected asyncio task factory
   cannot prevent this operation from executing once it no longer needs a driver task.

Required outcome matrix:

| Scenario | Expected outcome and ownership |
|---|---|
| Success or ordinary worker failure | Original value/error after full cleanup; lease released once |
| Worker-originated cancellation | Original cancellation propagated after cleanup; no terminal-handle retry loop |
| Caller cancellation during queued work, active work, or cleanup | Same operation drains; cancellation propagates after cleanup; runtime remains accepting unless shutdown separately began |
| Repeated asyncio or AnyIO level cancellation | Drain continues with bounded await attempts and event-loop progress |
| Worker failure during caller cancellation | Caller cancellation remains primary; original failure evidence survives |
| Executor submission rejection | No worker runs; scope and context restored immediately with retained traceback |
| Nested operation during runtime shutdown | Existing retained ownership remains valid; parent and child release independently |
| Cancellation of operation tasks during shutdown | No cancelled driver can make drain finish before thread cleanup or resource disposal |

Acceptance: every submitted synchronous operation retains ownership through actual cleanup;
cancellation never releases admission based on an async driver's terminal state or loses worker
failure evidence; submission rejection has no ownership leak.

## Step 3: validate the source session implementation (T2)

Primary files: `app/services/pipeline_tasks.py`, `app/database.py`, and session-authority tests.

1. Replace the broad `issubclass(factory_class, source_class)` authorization rule. Session class
   behavior is part of database authority, alongside the declared engine, mapped binds, and
   factory options. Matching those options does not authorize a different routing implementation.
2. Define a narrow compatibility contract: accept a supported real sessionmaker using the source
   session class itself or a verified thin class layer produced by the supported stable clone
   constructors. Reject arbitrary factory subclasses/callables or additional session subclasses
   that introduce behavior beyond that source implementation.
3. Account for SQLAlchemy's generated session class: ordinary sessionmaker clones create a new
   class layer, so class object identity alone would reject valid startup. Inspect the installed
   dependency and document the supported generated-layer shape. Validate its direct base and
   permitted metadata conservatively; reject extra methods, attributes, or mixins that can alter
   construction/routing. Include `get_bind()` and `__init__` changes in negative controls.
4. Share the clone construction/validation rule between `app.database.stable_session_factory()`
   and the compatibility clone in `pipeline_tasks`. Preserve source options and mapped binds.
   Prefer one documented helper over two divergent interpretations of a stable clone.
5. Preserve an intentionally custom source session implementation as the trusted source where
   supported. Its generated clones must retain that exact behavior; proposals cannot introduce
   an additional override. Do not discover authority by constructing a session or invoking
   `get_bind()` during publication.
6. Keep provider/settings/options checks and the final publication guard. Complete session
   implementation validation before assigning any ownership or fact-capture fields. Preserve
   transition-token checks and same-owner capture validation.
7. Keep this restriction at legacy publication. Independent composed runtimes can retain their
   supported injected factories; this fix must not impose global authority on instance-owned apps.
8. Verify startup and retirement using valid clones, then perform an ordinary session operation
   as a positive control. The foreign-routing proposal must fail installation, and the validator
   must perform zero session/provider invocations for both acceptance and rejection.

Acceptance: a proposal cannot add database-routing behavior under another factory's verified
authority; supported startup clones still publish; rejection preserves all coordinator state.

## Step 4: integrated verification and acceptance records

1. Run the new regressions and the full lifecycle/task/SOLID boundary suites on the integrated
   implementation. Include real global and composed app lifespan tests, request/background work,
   context restoration, session-close failure, and shutdown disposal ordering.
2. Rerun the existing W3/W4/W5 tests for shutdown-before-publication, rejected submission with a
   retained traceback, frozen scheduling settings, conflicting owner facts, and reservation retry.
3. Run `make check` and `make hedron-build`. Record fresh test counts, deselections, coverage against
   the unchanged 80% floor, demo results, Ruff/formatting, BasedPyright, Hedron/Posit results, and
   build digest. Do not reuse the prior full gate as acceptance for these fixes.
4. Run documentation tests, validate local links in changed records, and run `git diff --check`.
   Preserve pre-existing files, including the existing pipeline lock artifact.
5. Update this plan, the plan index, the major SOLID plan, and the worker/publication record together.
   Close T1/T2 and the reopened W1/W2 acceptance only after the final gates pass. Keep historical
   results dated and distinguish them from fresh evidence. Keep the remaining SOLID migration open.

Definition of done: real thread cleanup precedes drain completion and disposal; worker and caller
cancellation remain distinct; a foreign-routing session implementation cannot publish; valid
compatibility clones and independent apps still work; regressions and integrated gates pass.
