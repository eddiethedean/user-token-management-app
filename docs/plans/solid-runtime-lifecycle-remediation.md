# SOLID runtime review: five-issue remediation plan

Status: earlier runtime fixes implemented; runtime acceptance reopened by the
[completion and generation isolation plan](solid-runtime-completion-isolation-remediation.md). The
[failure handling and legacy ownership plan](solid-runtime-failure-remediation.md) records the
completed follow-on fixes. Runtime-only scheduling, early readiness, operation admission, and the
catalog/authoring fixes remain implemented and covered by compatibility suites.

## Verified findings and scope

| ID | Priority | Reproduced behavior | Required result |
|---|---|---|---|
| L1 | P1 | A cached-settings compatibility call locked database A while opening B's session and selecting B's registry. | Cached settings satisfy the same database/registry ownership checks as every other accepted input. |
| L2 | P1 | With the supervisor polling and a deferred worker blocked, composed lifespan called `close()` while the worker still held its session and pipeline lock. | Shutdown closes pipeline admission and drains every admitted operation before disposal. |
| L3 | P2 | Scheduling with `stop_event=runtime.stop_event` succeeded initially, then execution rejected the forwarded event as an independent override. | Accepted runtime scheduling dispatches a runtime-only call that executes successfully. |
| L4 | P2 | Cancelling the supervisor from its operation's completion callback left the supervisor done and its stop event unset. | Cancellation signals shutdown regardless of the operation's completion state. |
| L5 | P2 | During blocked-worker shutdown, readiness remained true after the stop event was set. | Both lifespans report unavailable before awaiting drain; readiness cannot query the database during that drain. |

Review locations: `app/services/pipeline_tasks.py` (`_legacy_runtime`, scheduling, threaded waits),
`app/infrastructure/runtime.py` (operation ownership), and both lifespans in `app/main.py`.

The review passed 68 focused tests across `test_solid_boundaries.py`, `test_user_catalog.py`,
`test_pipeline_tasks.py`, and `test_pipelines.py`; separate deterministic probes reproduced all
five findings. The implementation added regressions in `test_pipeline_tasks.py` and
`test_pipeline_lifecycle.py`; the follow-on review added dedicated E1–E5 acceptance cases.

Preserve the schema, saved definitions, provider IDs, settings aliases, CLI, routes, form names,
CSRF, redirects, fragments, credential/cache policy, ownership messages, and lease fencing. Keep
in-process execution, runtime-only catalog construction, locator revalidation, normalized authoring,
and early local ownership checks. This pass does not expand identity/provider/presentation migration
or require deployment, a queue, a new timeout setting, or live-provider qualification.

## Invariants and implementation decisions

1. Accepted legacy work has one explicit owner: supplied settings, the configured session engine,
   registry factories, file-lock URL, pipeline lock, and stop event agree. Cache identity never
   substitutes for checking these facts. Normalization performs no provider reload or database I/O.
2. Pipeline admission and shutdown use one thread-safe lifetime owned by each runtime generation.
   Admission registers work before any pipeline lock, session, connector context, or worker call.
   Shutdown closes admission atomically; work admitted earlier remains registered until cleanup ends.
3. Runtime context binding and operation tracking are distinct. Catalog/authoring context entry must
   not accidentally count as a supervised pipeline worker or introduce nested tracking deadlocks.
4. `stop_event` signals cooperative cancellation. It is not proof that a worker, its session, its
   connector cleanup, or its lease heartbeat has completed. Actual operation completion is required.
5. Once shutdown begins: readiness is false, admission is closed, the owned stop event is set,
   supervisor/poll work finishes, all admitted pipeline work finishes, and only then resources close.
6. Cancellation remains observable after draining and disposal. A completed helper task is not a
   reason to skip the shutdown signal, and repeated cancellation cannot discard completion handles.

## Delivery order

| Step | Deliverable | Dependencies | Gate |
|---|---|---|---|
| 0 | Named regressions for L1–L5 and valid-owner fixtures | None | Each failure reproduces through the real entrypoint. |
| 1 | Strict legacy resource verification (L1) | 0 | Cached/noncached inconsistencies reject before locks, sessions, and factories. |
| 2 | Canonical deferred scheduling (L3) | 1 | Exact matching owner facts execute; conflicts reject before attachment. |
| 3 | Runtime operation admission and registration (L2) | 1–2 | Every pipeline entrypoint registers before I/O and releases after cleanup. |
| 4 | Completion-race-safe cancellation (L4) | 3 | Before/during/after-completion cancellation closes admission and sets stop. |
| 5 | Shared lifecycle drain and early readiness transition (L2/L5) | 3–4 | Both lifespans dispose only after actual work completion and report unavailable while waiting. |
| 6 | Acceptance, repository gates, and status reconciliation | 1–5 | Named regressions and final-tree gates pass before closure claims. |

Keep fixes and their regressions reviewable by step. Production admission must remain closed after
shutdown; a test timeout must never become permission to dispose resources still in use.

## Step 0: establish regression fixtures

Files: `tests/test_solid_boundaries.py`, `tests/test_pipeline_tasks.py`, `tests/conftest.py` where
global-owner isolation is needed; add `tests/test_pipeline_lifecycle.py` for the lifecycle matrix.

1. Create A/B owners with separate file-backed SQLite databases, connector registries, settings,
   pipeline locks, events, and migrated schemas. Record explicit/implicit session factory, session
   engine, registry, factory settings, file-lock URL, and stop-event identity.
2. For L1, retain actual cached settings A while binding legacy engine/session/registry to B. Assert
   rejection and zero lock/session/factory calls. Add the inverse and isolated registry mismatch.
3. For L3, attach a task using matching settings and `runtime.stop_event`, then await the actual
   `BackgroundTasks` object. Merely inspecting its arguments cannot establish successful execution.
4. For L2, wait until the supervisor enters its real polling wait; start a deferred worker through
   the scheduled entrypoint, block it after session entry, and begin lifespan shutdown. Record that
   disposal has not happened until worker/heartbeat/session/context/lock cleanup completes.
5. For L4, locate/capture the in-flight threaded-operation task while its worker is blocked. Attach
   a done callback that cancels the supervisor, release the worker, and assert the stop event is set
   and admission closed even though the operation is already done when cancellation is observed.
6. For L5, observe the stop signal during a blocked drain and call the app's readiness endpoint.
   Assert false state, HTTP 503, and zero readiness-session queries; keep `/health` compatible.
7. Coordinate workers with events/barriers and bounded test timeouts. Enter and exit each lifespan
   in the same async task to preserve context-variable cleanup. Use another task for observation and
   cancellation injection, not for moving an entered lifespan between tasks.
8. In `finally`, release every blocked worker and await real completion; restore globals, registry
   configuration, environment, and settings cache. Avoid sleeps as synchronization and avoid test
   failures that leave executor threads alive.

## Step 1: strict legacy ownership verification (L1)

Files: `app/services/pipeline_tasks.py`, `app/database.py`, `app/connectors/registry.py`, legacy
bootstrap/fixture code only where consistent owner installation is needed.

1. Remove both `settings is canonical_settings` exemptions. Database/registry mismatch rejects for
   cached settings just as it does for an independently constructed Settings object.
2. Compare parsed SQLAlchemy URLs/resource facts rather than password-redacted string renderings.
   Verify the session factory's configured engine agrees with the compatibility engine and supplied
   database URL. Do not open a session to check ownership; no raw URL/credential value enters errors.
3. If the legacy registry owns settings, require the exact registered owner settings. An explicitly
   unowned compatibility registry remains supported only when factory settings resolve from the
   checked legacy runtime, without another active app's context. Do not rebind it during validation.
4. Make legacy bootstrap/fixtures install settings, engine, session binding, registry, and execution
   owner consistently. Tests relying on stale globals after fixture teardown must restore those
   globals or use a complete runtime, rather than causing production checks to be weakened.
5. Normalize once at public boundaries and use runtime-only internal calls. Remove supervisor
   `legacy_mode` dispatch that normalizes again every recovery/janitor cycle and can select a later
   set of globals. Keep settings-only public compatibility entrypoints for a valid legacy owner.
6. Resolve legacy resources at invocation time to preserve intentional CLI/fixture rebinding, but
   capture the verified owner for deferred/threaded execution. Rebinding while admitted legacy work
   is active must reject or wait for the old generation to drain; it cannot change that work's owner.

Acceptance:

- Cached settings with a foreign database or registry reject before any lock, session, or factory.
- Same engine but conflicting registry settings also rejects; all partial overrides stay rejected.
- Valid legacy immediate/recovery/retention/supervisor calls work under no context and outer A/B
  contexts, including thread dispatch, and restore the outer context afterward.
- Accepted URL comparison handles valid password-bearing/query-bearing URLs without logging them.
- Rebinding positive controls use a deliberately installed consistent owner and pass in suite order.

## Step 2: canonical deferred scheduling (L3)

Files: `app/services/pipeline_tasks.py`, `app/ui/routes/pipeline_runs.py`, runtime scheduling tests.

1. Keep the compatibility scheduling signature. With a runtime, supplied settings must be its exact
   settings, and a nonempty event argument must be its exact stop event. Reject conflicts before
   adding a background task; retain the authoritative runtime environment gate.
2. Once validated, dispatch `(runtime, run_id)` with no independent event/resource arguments.
   Do not forward even a matching event into a runtime-only entrypoint that rejects overrides.
3. On the settings-only path, capture a verified legacy runtime at attachment time so deferred work
   cannot later combine request A's settings with rebound legacy resources B. Preserve legitimate
   legacy scheduling and test-mode suppression, not assertions about an unsafe internal tuple.
4. If pipeline admission is closed, do not attach new work. If admission closes after attachment,
   execution must decline before locks/sessions/factories. The durable queued row remains available
   for the next valid runtime generation; do not claim or fail it merely because shutdown began.
5. Keep production routes passing their owning runtime and metadata. A route-specific omission of
   the event cannot substitute for fixing the public matching-event compatibility path.

Acceptance: matching event omitted/provided both execute; mismatched settings/event attach zero
tasks; legacy positive control executes under its captured owner; task attached before shutdown and
executed afterward performs zero I/O; test-mode behavior and durable queued-run recovery remain valid.

## Step 3: runtime-owned pipeline operation lifetime (L2)

Files: `app/infrastructure/runtime.py`, `app/services/pipeline_tasks.py`, `app/bootstrap.py` and
legacy owner accessors if needed. Preserve the completed `LeaseKeeper.stop()` termination behavior.

1. Add a narrow thread-safe lifetime object owned by ExecutionRuntime, with a Condition, admission
   state, and admitted-operation count/tokens. Proposed API: `try_admit_pipeline_operation()`,
   `begin_shutdown()`, and `wait_until_idle()`. Exact names can follow existing conventions.
2. Under the same Condition, admission either registers a token or declines. `begin_shutdown()`
   closes admission and sets the owned event before waking waiters. External event-setting is also
   honored by admission, but production lifecycle/cancellation uses the shared shutdown method.
3. Wrap immediate/deferred execution, pending recovery, and retention in an operation guard before
   taking either lock or opening a session. Guard release runs in `finally`, after session closure,
   lease-thread termination, context restoration, and pipeline-lock release, including exceptions.
4. Declined pending execution returns false; declined immediate/retention returns without I/O.
   Retain immediate nonblocking lock behavior. If recovery/retention was admitted while waiting
   for the pipeline lock, recheck shutdown after acquiring it and exit before opening a session.
5. Track lock-waiting operations as well as executing ones. Do not infer idleness from the pipeline
   lock alone: a worker can be between admission and lock acquisition or in final resource cleanup.
6. Keep context binding separate from admission. Avoid double-counting wrappers/internal calls and
   never let a drain wait count as an operation that it must itself wait for.
7. Ensure compatibility wrappers share the installed legacy owner's lifetime. Creating a new
   independent tracker on every `_legacy_runtime` call would leave legacy deferred work invisible
   to the global lifespan. A compatibility owner binding is scoped to the existing legacy globals;
   composed apps retain independent trackers, events, locks, and settings.
8. Use one stop event per runtime generation; alias `app.state.pipeline_stop_event` to it rather
   than replacing it after lifetime creation. On supported app restart, publish a fresh runtime
   generation only after the old one has drained. Old attached tasks keep the old closed runtime;
   do not reopen it or allow stale work to use a newly restarted app's resources.

Acceptance:

- Admission racing shutdown either wins and must drain, or loses and performs zero I/O.
- Immediate success, lock deferral, recovery, retention, worker failure, and context failure each
  release exactly their own token; no negative counts, leaked registrations, or cross-app tracking.
- A blocked worker or heartbeat keeps the lifetime nonidle until its actual session cleanup ends.
- Closing A leaves B admitting/running normally. Old-generation tasks remain declined after restart.

## Step 4: completion-race-safe cancellation (L4)

Files: `app/services/pipeline_tasks.py`; use the lifetime shutdown method from Step 3.

1. On every caught caller CancelledError, call `runtime.begin_shutdown()` before examining the
   operation's done/cancelled state. Completion state cannot identify who caused cancellation.
2. Retain the explicit task handle and shield it while waiting. If the synchronous operation is
   still active, continue waiting for that same handle; do not create another operation or retry
   the provider/database work. If it completed, retrieve its result/exception once.
3. Record caller cancellation and raise it after actual operation completion. Worker failure without
   caller cancellation propagates its original error. When caller cancellation and worker failure
   coincide, retain the worker error as diagnostic/chained evidence while preserving cancellation.
4. Add an outer supervisor cancellation/finalization boundary so stop signaling/admission closure
   covers cancellation between cycles, during poll wait, and at operation completion. Normal stop
   and supervisor failure must not permit a later pipeline cycle.
5. Continue accounting for pending, janitor, and threaded poll waits. Do not remove their shielding
   or use a cancelled wrapper as evidence that the synchronous callable has finished.
6. Repeated cancellation during drain only records additional cancellation; it must not cancel the
   protected worker task, lose its handle, spin forever on an already-cancelled handle, or skip drain.
7. Handle production supervisor cancellation before its coroutine starts through a shared task
   starter that installs a done callback closing the runtime on cancellation/failure. An async
   coroutine's `finally` cannot run if its task is cancelled before initial execution. Both
   lifespans must use this starter and still close admission through their drain helper; retain
   the public coroutine for compatibility with callers that manage their own lifetime.

Acceptance: cancel before dispatch, during worker/retention/poll, from the worker-completion
callback, and between cycles; also cancel a task created by the production starter before it first
runs. Each case sets stop, closes admission, drains accepted work, releases locks/sessions, and
reports cancellation. Completion-callback coverage must be deterministic.

## Step 5: shared lifecycle stop/drain and readiness (L2/L5)

Files: `app/main.py`, `app/services/pipeline_tasks.py`, both-lifespan acceptance tests.

1. In both lifespans, set readiness false as the first shutdown action. It stays false for the
   entire drain, including normal exit, worker failure, caller cancellation, and repeated cancel.
2. Use one shared stop-and-drain helper taking the owned runtime and an optional supervisor task.
   Invoke it even without a supervisor: deferred work and closed admission still require handling.
3. The helper calls `begin_shutdown()`, retains/shields the supervisor handle, and waits for both
   supervisor/poll completion and the runtime's actual admitted-operation count to reach zero.
   Await blocking Condition waits through an explicit protected thread-task handle; these waits
   use no database and must not count as pipeline operations.
4. Handle a supervisor already done, already cancelled, or failed without skipping admitted work.
   Record its terminal result/error, finish draining the runtime, then return/rethrow appropriately.
5. Keep drain cancellation-resilient. Lifespan cancellation cannot cancel owned work or move disposal
   ahead of it. Retain cancellation/failure until cleanup completes, then dispose once and preserve
   the original caller/body failure; do not silently suppress it or replace it with a later drain
   error. Retain additional cleanup errors as diagnostic/chained evidence.
6. Only after drain completes call `engine.dispose()` or `composition.close()`. Assert disposal
   ordering against deferred, recovery, retention, poll, heartbeat, session, context, and lock cleanup.
7. Readiness handlers already check state before database access. Verify they now return HTTP 503
   and perform zero session/query work throughout drain; leave liveness behavior compatible.

Acceptance matrix:

| Case | Required observations |
|---|---|
| Supervisor polling; deferred worker blocked | Ready false; admission closed; stop set; no disposal until worker/session/lock cleanup. |
| Recovery or retention blocked | No new cycles; drain waits for actual operation completion. |
| Lease heartbeat blocked | Worker lifetime remains active; disposal waits for heartbeat session termination. |
| Cancellation during drain, including repeated requests | Handles retained; resources close once after completion; cancellation remains observable. |
| Supervisor already completed/cancelled/failed | Active deferred work still drains; original termination result is preserved. |
| No supervisor task | Admission still closes and any admitted deferred work drains. |
| Work attached before shutdown starts | Execution after admission closes declines before I/O; durable run remains queued. |
| Work enters as shutdown begins | Registered-and-drained or denied; never unregistered database work after disposal. |
| A shutting down; B running | B stays ready and operates under B's independent lifetime. |
| Supported app restart | Fresh generation starts; old generation stays closed and old tasks cannot enter it. |

Run the applicable matrix for the global and composed lifespans; a fix in one is insufficient.

## Step 6: final gates and accurate evidence

1. Run affected regressions after each step. Keep all five original probe scenarios as named tests
   through real entrypoints, plus valid-owner, ordinary-shutdown, and failure positive controls.
2. Keep the earlier catalog/locator/authoring/ownership suites passing. Do not alter their security
   or persistence contracts while introducing the narrow pipeline lifetime.
3. Run the focused boundary/catalog/runtime/pipeline suites, then `make check`, `make hedron-build`,
   and `git diff --check` on the final code tree. Record actual counts, deselections, demo results,
   coverage, Ruff/formatting, BasedPyright, Hedron results, and Posit matrix. Keep the 80% floor.
4. Verify changed Markdown links and documentation tests. Reconcile this plan, six-issue plan,
   completion/follow-up/milestone records, major SOLID plan, and index so their status claims agree.
5. Mark L1–L5 closed only against named passing regressions. Label prior gate results historical;
   no completion claim based solely on the old suite count, a stop flag, or a cancelled coroutine.

Definition of done: accepted work uses a consistent verified owner; matching-event scheduling
executes; shutdown denies new work and drains actual pipeline/lease/session cleanup before disposal;
every cancellation path signals shutdown; readiness is unavailable throughout drain; both lifespans
and independent apps satisfy the matrix; all final-tree repository gates pass.

## Risks and guardrails

- Strict legacy checks may expose stale fixture/bootstrap state. Repair that state explicitly;
  retain valid rebinding controls and do not restore cached-settings exemptions.
- A tracker can miss operations or create deadlocks. Register before I/O, release after cleanup,
  keep admission separate from context binding, and exclude the drain's own wait from active work.
- Per-call legacy runtimes can hide work from shutdown. Share a captured compatibility owner lifetime
  and reject unsafe owner/event replacement while work is active.
- App restart can reopen old task references. Use fresh runtime generations and preserve the old
  closed generation until all its admitted operations have terminated.
- Remote work may delay shutdown while completing cooperative cleanup. Existing connector/database
  timeouts remain the bounds; runtime disposal cannot be forced by an arbitrary drain deadline.
- Timing-only tests can pass without exercising the race. Use completion callbacks/events and
  observable cleanup ordering; always release blocked workers in test teardown.

Rollback should retain the new regressions and implemented catalog/authoring fixes. Do not restore
mixed ownership or early disposal to preserve a test that depended on inconsistent globals.

## Implementation evidence; runtime acceptance closed

- **L1:** `_legacy_runtime` compares normalized database URLs, enforces the legacy registry owner,
  and rejects a foreign cached owner before opening a session or acquiring a lock.
- **L2:** `ExecutionRuntime` owns a thread-safe admission lifetime. Immediate, recovery, and
  retention work register before I/O; both lifespans close admission and wait for the supervisor and
  every admitted operation before disposal.
- **L3:** validated scheduling captures the owning runtime and dispatches the runtime-only worker
  signature, so a matching event is never forwarded as an independent override.
- **L4:** threaded waits keep explicit task handles and call `begin_shutdown()` on every cancellation,
  including the completion-callback race and cancellation before supervisor startup.
- **L5:** both lifespans set readiness false before beginning the drain; readiness therefore returns
  unavailable without opening a database session throughout shutdown.
- **Final validation:** `make check` passed with 434 tests, 31 deselections, 84.31% coverage, and
  25 demo tests. `make hedron-build`, the documentation tests (3 passed), and `git diff --check`
  also passed; Ruff, formatting, BasedPyright, Hedron, and the Posit matrix were clean.
