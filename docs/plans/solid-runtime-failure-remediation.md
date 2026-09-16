# SOLID runtime review: failure handling and legacy ownership remediation

Status: earlier fixes implemented; runtime acceptance reopened by the
[completion and generation isolation plan](solid-runtime-completion-isolation-remediation.md).
The [owner generations and shutdown outcomes record](solid-runtime-owner-outcome-remediation.md)
preserves the subsequent implementation evidence. This plan was
the active backlog following review of the [runtime/lifecycle implementation](solid-runtime-lifecycle-remediation.md).
The operation lifetime, runtime-only scheduling, early readiness transition, locator validation,
catalog boundary, and normalized authoring fixes remain covered by their compatibility suites.

## Findings and acceptance targets

| ID | Priority | Verified failure | Acceptance target |
|---|---|---|---|
| E1 | P1 | A supervisor that fails during the shielded await escapes before the admitted-operation wait. Composed lifespan disposes while a deferred worker is running and holding its lock. | Supervisor failure is retained while all admitted work drains; disposal follows actual completion. |
| E2 | P1 | Engine/settings A with `SessionLocal` bound to B passes ownership checks and executes recovery in B's session. | The actual session binding, engine, settings URL, registry, and captured runtime agree before any lock/session/provider work. |
| E3 | P2 | A correct password-bearing URL is compared with `str(engine.url)`, whose password is redacted, and rejected. | Valid password/query-bearing owners are accepted; real mismatches reject without disclosing credentials. |
| E4 | P2 | Cancelling a threaded call whose worker subsequently fails reports `RuntimeError`, with the caller task not cancelled. | Caller cancellation remains observable after completion; worker failure remains available as diagnostic evidence. |
| E5 | P2 | A successful settings-only recovery creates an idle legacy runtime with an unset stop event. Global lifespan then rejects its fresh runtime as already active. | Temporary compatibility owners transition safely into startup-owned generations without reopening stale references or losing admitted work. |

The review passed 74 focused tests across pipeline tasks, lifecycle, SOLID boundaries, user catalog,
and pipelines. Separate probes reproduced all five findings. The E1 lifecycle probe observed
`worker_done=false`, `pipeline_lock_held=true`, and `lifetime_idle=false` at disposal. Prior evidence
of 428 passing tests, 31 deselections, 84.27% coverage, and 25 demo tests is historical; it does not
close these findings. Each new scenario needs a named regression before acceptance is restored.

Scope is the existing in-process runtime, database compatibility accessors, both app lifespans,
their fixtures/tests, and plan records. Preserve schema, saved definitions, provider IDs, settings
aliases, public compatibility entrypoints, routes/forms/fragments, CSRF, request metadata, credential
policy, durable queued-run recovery, and lease fencing. No deployment or provider qualification is
required to complete this plan.

## Ownership and failure rules

1. A stop event, failed task, or idle pipeline lock is not proof of completed cleanup. Drain must
   establish supervisor completion and zero admitted operations before resource disposal.
2. Terminal supervisor/worker errors are retained outcomes while cleanup continues. An exception
   arriving through `await shield(...)` cannot bypass the subsequent lifetime wait.
3. Protected work is dispatched once and awaited through its original task handle. Cancellation
   never retries a provider operation or abandons its synchronous thread.
4. Outcome precedence is explicit: original lifespan-body cancellation/failure remains primary;
   otherwise cancellation observed during cleanup remains primary; otherwise supervisor/worker
   failure is primary. Additional errors remain chained or grouped as diagnostic evidence.
5. Accepted legacy work uses an immutable resource snapshot and one shared operation lifetime per
   generation. A mutable global sessionmaker reference alone does not freeze its engine binding.
6. Owner states are explicit: temporary compatibility owner, lifespan-owned owner, transitioning,
   and closed. Replacement must close old admission and drain before publication. Old deferred
   tasks keep their closed generation and decline before I/O.
7. URL comparison uses structured SQLAlchemy URLs with actual password/query components. Validation
   performs no connection attempt and emits no raw URLs or credentials.

## Delivery sequence

| Step | Deliverable | Depends on | Completion gate |
|---|---|---|---|
| 0 | Deterministic E1–E5 regressions and positive controls | None | Original failures reproduce through real boundaries. |
| 1 | Structured database facts and verified resource snapshots (E2/E3) | 0 | Correct owners accepted; conflicting session bindings rejected before I/O. |
| 2 | Shared terminal-outcome handling for protected tasks (E1/E4) | 0 | Every task state drains safely and preserves cancellation/error precedence. |
| 3 | Both lifespans consume drain outcomes before disposal (E1/E4) | 2 | Actual deferred work outlives no disposed resource, including failure/cancellation. |
| 4 | Safe compatibility-to-startup generation transitions (E5) | 1–3 | Idle and busy temporary owners transition without stale work entering the new owner. |
| 5 | Compatibility matrix, final gates, and evidence reconciliation | 1–4 | Named regressions and final-tree gates pass before closure. |

Keep changes and tests reviewable by step. Ownership facts must be fixed before owner publication
is changed; failure-safe draining must be fixed before startup transitions rely on it.

## Step 0: regression fixtures and failure injection

Files: `tests/test_pipeline_tasks.py`, `tests/test_pipeline_lifecycle.py`,
`tests/test_solid_boundaries.py`; update `tests/conftest.py` only for explicit global-owner isolation.

- Add `test_supervisor_failure_during_drain_waits_for_deferred_worker`, parameterized for global and
  composed lifespans. Enter a real scheduled worker, block after session entry, then make the
  supervisor fail while drain is awaiting it. Assert no disposal while blocked; after release,
  assert session/context/lock/lifetime cleanup and one disposal, followed by the original failure.
- Add `test_legacy_owner_rejects_foreign_session_binding`. Use engine A, settings/registry A, and
  a sessionmaker bound to B. Spy on file lock, factory invocation, and connector calls; all remain
  zero on rejection. Include cached settings and an already-cached runtime positive/negative pair.
- Add `test_legacy_owner_accepts_password_and_query_url`. Use structured URL/engine metadata and
  lazy factories without a live PostgreSQL connection. Assert acceptance of matching escaped
  password/query components and rejection of mismatches; captured logs/errors contain no secret.
- Add `test_cancelled_thread_call_preserves_cancellation_when_worker_fails`. Block a dispatched
  callable, cancel its caller, wait for shutdown signaling, then release it into failure. Assert
  `CancelledError`, `task.cancelled()`, one dispatch, and preserved worker-error evidence.
- Add `test_legacy_recovery_owner_transitions_into_global_startup`. Execute successful settings-only
  recovery before entering the actual global lifespan. Verify startup succeeds, owns a fresh
  generation, and drains/disposes normally. Extend with attached and already-admitted old work.
- Coordinate with events/barriers and bounded test waits. Keep lifespan entry/exit in the same
  async task; use observers to release workers or inject cancellation. Teardown always releases
  blocked work, awaits original handles, and restores engine, factory, registry, settings cache,
  legacy coordinator, and environment. Prevent cross-test owner leakage.

## Step 1: verify and freeze database ownership (E2/E3)

Files: `app/database.py`, `app/services/pipeline_tasks.py`, `app/infrastructure/runtime.py` only if
a narrow snapshot type belongs there; legacy fixture/bootstrap accessors as needed.

1. Replace the redacted string comparison with an accessor returning the compatibility engine's
   SQLAlchemy `URL`, or equivalent structured facts. Parse the supplied settings URL once and
   compare its complete components. Keep error messages credential-free.
2. Inspect the actual legacy sessionmaker configuration without creating a session. Require its
   default engine binding to be the checked compatibility engine. Reject foreign per-entity/table
   binds as well: a matching default must not conceal pipeline models routed to B.
3. Do not silently accept an opaque replacement factory whose ownership cannot be established.
   Such callers provide a complete `ExecutionRuntime`; the normal global sessionmaker remains a
   supported settings-only positive control.
4. Snapshot engine, parsed URL, source session-factory identity/configuration, registry, registry
   owner settings, and execution owner under the legacy coordinator's synchronization boundary.
   Build a stable engine-bound session factory preserving the verified factory's session options;
   later `SessionLocal.configure(...)` must not redirect admitted or deferred work.
5. Validate cached owners against current resource facts, not just settings/event identity. Changed
   engine, factory binding, registry, or owner configuration requires an explicit generation
   transition or rejection. A cached runtime cannot legitimize inconsistent current globals.
6. Route supported fixture/bootstrap rebinding through the coordinator. Raw global mutations must
   not become a supported concurrent-rebinding contract. Execution uses the captured stable facts;
   every new compatibility boundary verifies the current generation.
7. Keep explicit runtime execution independent of compatibility globals and restore outer A/B
   contexts after execution. Do not reload providers, open sessions, or contact databases during
   owner validation.

Acceptance includes matching SQLite and password/query-bearing URL metadata, mismatched default
and model-specific session bindings, cached owner consistency, deferred execution after attempted
rebinding, and restoration of outer runtime contexts.

## Step 2: preserve terminal outcomes while completing protected work (E1/E4)

Files: `app/services/pipeline_tasks.py`; retain the existing lifetime and task starter.

1. Introduce a narrow internal task-completion routine or equivalent shared logic used by threaded
   calls and runtime draining. Retain the protected task handle, terminal result/error, and caller
   cancellation observation separately.
2. Shield the same task until it is actually terminal. An awaited worker/supervisor exception is
   recorded as that task's outcome rather than escaping cleanup. Retrieve the final outcome once;
   do not loop repeatedly on a completed failed/cancelled handle.
3. On caller cancellation, immediately call `runtime.begin_shutdown()`. Continue shielding until
   the original task completes. Cover repeated cancellation and completion-callback injection.
4. Distinguish cancellation of the protected child from cancellation of its caller. A child's
   terminal `CancelledError` is an outcome; it must not be mistaken for a new caller cancellation
   solely because shield raised. Cover caller cancellation state explicitly in tests. A cancelled
   `to_thread` wrapper is not proof its callable finished: owned work tasks must remain protected
   from direct cancellation, and any supported externally-cancelled-wrapper case must wait for
   lifetime/callable completion evidence before reporting cleanup complete.
5. `_drainable_thread_call` returns a successful result only when no caller cancellation was
   observed. If cancelled, raise `CancelledError` after completion, chaining worker failure if
   present. Without caller cancellation, preserve the original worker exception/result.
6. `drain_background_runtime` records the supervisor's terminal state, then always waits for the
   lifetime to reach zero, including when the supervisor fails during the await, is already failed,
   is cancelled before startup, or is absent. Keep the lifetime wait in its own shielded task.
7. Keep completion proof distinct from error reporting. Prefer an internal drain outcome returned
   only after supervisor and lifetime completion, with cancellation/error data reported after
   disposal by the lifespan. Do not treat an arbitrary drain exception as proof that resources are
   safe to dispose. No arbitrary deadline permits early disposal.

Required cases: success, ordinary failure, child cancellation, caller cancellation followed by
success/failure, repeated caller cancellation, and cancellation from the completion callback. In
every case there is one dispatch and one terminal outcome, with accepted work fully cleaned up.

## Step 3: lifecycle disposal and error precedence (E1/E4)

Files: both lifespans in `app/main.py`, shared drain/outcome reporting in
`app/services/pipeline_tasks.py`, lifecycle tests.

1. Preserve readiness false as the first shutdown action and invoke the shared drain even without
   a supervisor. Confirm readiness performs zero database work throughout blocked cleanup.
2. Preserve the original exception/cancellation delivered through the lifespan body. Complete
   stop/drain/disposal before propagating it; a later supervisor failure must not replace it.
3. Dispose only after the drain outcome proves actual supervisor and admitted-operation completion.
   Remove the blanket pattern that catches any drain error and immediately disposes anyway.
4. Call disposal once per lifespan. Release the installed legacy owner only after its generation
   is closed and drained. Never make incomplete cleanup look like a released, reusable owner.
5. Apply the defined primary-outcome precedence. Preserve additional supervisor/drain/disposal
   failures as chained or grouped diagnostics. Keep ordinary success and ordinary supervisor-error
   behavior compatible, with no silent cancellation suppression.
6. Run the lifecycle matrix below for both global and composed instances. Use the real background
   entrypoint and session cleanup for deferred-worker ordering; a manually held lifetime token is
   supplemental coverage rather than acceptance for the original E1 failure.

| Lifecycle case | Required observations |
|---|---|
| Supervisor fails while drain awaits it; deferred worker blocked | No disposal until worker/session/context/lock completion; supervisor error then reported. |
| Supervisor already failed/cancelled/completed; deferred work blocked | Same completion requirement regardless of supervisor state. |
| Caller cancels drain once or repeatedly | Stop set, admission closed, handles retained, cancellation reported after cleanup. |
| Caller cancellation and worker/supervisor failure coincide | Cancellation remains primary; secondary failure remains inspectable. |
| Lifespan body fails while supervisor later fails | Body exception remains primary after complete disposal. |
| No supervisor; admitted operation active | Drain waits for the operation and denies new admission. |
| Heartbeat or session cleanup blocked | Lifetime remains active until actual cleanup finishes. |
| A shuts down while B runs | B remains ready and admits work under its independent owner. |

## Step 4: temporary compatibility owner transitions (E5)

Files: legacy owner coordinator in `app/services/pipeline_tasks.py`, global lifespan startup in
`app/main.py`, database/registry bootstrap and fixtures where consistent publication is needed.

1. Identify the provenance of the installed legacy generation. Settings-only immediate/recovery/
   retention/scheduling calls may establish a temporary compatibility owner; global lifespan owns
   an explicitly installed generation. Do not infer provenance from whether its event is set.
2. At global startup, prepare the ownership transition before clearing/rebinding a registry or
   changing shared database configuration. Validate proposed resource facts first.
3. Under the coordinator lock, reserve the transition and close the old temporary owner's admission.
   Mark it transitioning so concurrent settings-only calls cannot create or reuse an accepting
   generation while startup is draining it.
4. Release the coordinator lock before awaiting old admitted work. Never hold that lock while
   workers, sessions, or cleanup terminate. Use the failure-safe drain from Steps 2–3.
5. After completion, reacquire the lock, verify the reserved generation is still current, and publish
   the fresh lifespan-owned runtime/event/lifetime atomically. Configure mutable shared registry
   state only when no accepting old generation can access it.
6. Old attached-but-unstarted tasks retain the old closed runtime and decline before I/O. Their
   durable queued rows are available to the new supervisor; they cannot switch owner references.
7. Reject replacement of an active lifespan-owned owner through an unrelated installer. A temporary
   owner with admitted work transitions by draining it; an idle one transitions immediately after
   admission closure. Preserve ownership checks and independent composed apps.
8. Failure/cancellation during preparation or drain leaves a defined closed/transition state and
   preserves the primary outcome. Retry must not reopen the old runtime, lose a worker, or leave
   a permanent transition reservation after cleanup finishes.
9. Support sequential legacy supervisor generations through explicit close/drain/release handling.
   Never swap stop events on a reused runtime just to avoid a mismatch. Supported restart publishes
   a fresh generation after the prior one has finished.

Acceptance: settings-only recovery followed by startup; matching-event legacy scheduling followed
by startup; old attached tasks after transition; admitted old work during transition; concurrent
compatibility calls versus startup; conflicting active lifespan install; startup cancellation;
sequential supervisor generations; ordinary app restart; independent composed owner controls.

## Step 5: final validation and records

1. Run each named regression against the unfixed behavior, then against its fix. Record the change
   in observable behavior; do not count a mocked function signature as proof of worker execution.
2. Run the affected suites: `test_pipeline_tasks.py`, `test_pipeline_lifecycle.py`,
   `test_solid_boundaries.py`, `test_user_catalog.py`, and `test_pipelines.py`.
3. Run `make check` and `make hedron-build` on the final implementation tree. Record actual test
   counts/deselections, coverage, demo results, Ruff/formatting, BasedPyright, Hedron, and Posit
   matrix results. Preserve the 80% coverage floor. Prior gate counts remain historical.
4. Run documentation tests, validate local links in all changed plan records, and run
   `git diff --check`. Remove only generated artifacts attributable to validation.
5. Reconcile this plan, runtime/lifecycle plan, six-issue plan, completion/follow-up/milestone records,
   major SOLID plan, and index. Restore runtime acceptance only when E1–E5 have named passing
   evidence. Preserve remaining major-refactor migration scope instead of declaring it complete.

Definition of done: supervisor and worker failures cannot bypass actual cleanup; accepted legacy
sessions use the verified engine and stable snapshot; valid structured URL owners work; cancellation
remains observable with diagnostic failure evidence; temporary owners transition safely into startup;
both lifespans and independent owners satisfy the acceptance matrix; all final implementation gates
pass and documentation statuses agree.

## Risks and guardrails

- Stable session snapshots can change internal factory identity. Preserve session configuration and
  assert actual engine/context ownership; update fixtures that assumed unsafe mutable global identity.
- Owner transition locking can deadlock worker cleanup. Reserve/publish under the coordinator lock;
  await draining outside it, and prevent new owner admission while the reservation is active.
- Stale attached tasks can enter reopened resources. Close their generation permanently before
  replacement and preserve durable recovery under the new owner.
- Error handling can conceal cancellation or discard secondary failures. Verify primary outcome and
  diagnostic evidence together, including completion callbacks and repeated cancellation.

Retain regressions when reverting an individual step. Do not restore redacted URL checks, foreign
session acceptance, abandoned thread handles, or disposal before completion to satisfy an old test.

## Implementation evidence

- **E1:** supervisor terminal errors are recorded while the admitted-operation lifetime drains;
  both lifespans dispose only after the drain outcome proves completion.
- **E2:** compatibility validation checks the actual global engine, sessionmaker binding, model
  binding configuration, registry owner, and captured stable session factory before execution.
- **E3:** ownership comparison uses structured SQLAlchemy URLs, preserving password and query
  components without placing credentials in diagnostics.
- **E4:** protected threaded calls retain their task handle, signal shutdown on cancellation, and
  report caller cancellation with worker failures chained as evidence.
- **E5:** temporary compatibility generations are retired and drained before global startup installs
  a fresh owner; old generations cannot be reopened or reused.
- **Final validation:** `make check` passed with 434 tests, 31 deselections, 84.31% coverage, and
  25 demo tests. `make hedron-build`, documentation tests (3 passed), and `git diff --check` also
  passed; Ruff, formatting, BasedPyright, Hedron, and the Posit matrix were clean.
