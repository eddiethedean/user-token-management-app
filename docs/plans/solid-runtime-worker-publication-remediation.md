# SOLID runtime review: worker completion, publication, and captured scheduling

Status: implemented; W1–W5 are closed. T1/T2 follow-on acceptance is recorded in the
[thread completion and session authority plan](solid-runtime-thread-session-authority-remediation.md).
This record follows review of the
[admission and cancellation implementation](solid-runtime-admission-cancellation-remediation.md).
Earlier gate recorded on 2026-09-16; follow-on implementation and acceptance completed the same day.

## Scope and verified evidence

Resolve the five actionable findings in the latest review. Preserve the existing dirty worktree,
earlier SOLID changes, routes, schema, provider protocols, queued-run recovery, settings-only
CLI/worker compatibility, and shutdown exception precedence. No deployment or migration is needed.

The review passed 58 lifecycle/task/SOLID/connector tests and `git diff --check`, then reproduced
the failures below. They are now covered by permanent regressions. The final integrated gate passed
458 tests with 31 deselected at 83.97% coverage, alongside 25 demo tests.

| ID | Priority | Verified failure | Required result |
|---|---|---|---|
| W1 | P1 | A worker raising `CancelledError` leaves a terminal worker task that cleanup awaits repeatedly. The probe reached 100 retries before being stopped. | Terminal worker outcomes are collected once; cancellation never creates an unbounded retry loop or releases admission before actual worker cleanup. |
| W2 | P1 | Installation accepts a runtime whose session engine is foreign and whose registry contains zero providers, while globals contain four. | The proposed runtime's actual session, provider, and settings authority matches verified compatibility resources before publication. |
| W3 | P2 | A real thread stops the runtime during validation; installation still publishes it as a live `lifespan` owner. | Publication and `begin_shutdown()` have a defined ordering under shared synchronization. A proposal stopped before publication is rejected without coordinator mutation. |
| W4 | P2 | Task-factory failure leaves admission active while the exception traceback is retained; dropping the exception releases it through generator finalization. | Submission failure releases owned admission immediately and restores context independently of traceback lifetime or garbage collection. |
| W5 | P2 | Mutating source `app_env` from development to test changes attached tasks from one to zero while captured runtime settings remain development. | Runtime scheduling uses captured execution settings for all behavioral decisions and forwards only the captured runtime to deferred execution. |

Positive control from review: a full-stack readiness query retains admission through a blocked
database check and direct request cancellation. Preserve that behavior alongside mounted health,
readiness rejection during drain, retained child nesting, and constructor-free registry snapshots.

## Implementation and acceptance evidence

The initial implementation passed its recorded gate. Follow-on T1/T2 fixes now ensure admission is
retained through actual executor cleanup and reject foreign-routing session behavior. The final
acceptance evidence is recorded in the linked thread/session plan.

Worker-originated cancellation completes once
without a retry loop, and rejected submission closes admission immediately. Compatibility
publication validates the proposed session factory, connector metadata, and captured settings before
it records ownership; its final admission check runs under the runtime publication guard. Runtime
scheduling uses `execution_settings`, and routes forward that captured generation.

The focused lifecycle/task/SOLID suites passed 55 tests. The final `make check` passed Ruff,
formatting, BasedPyright, Hedron and Posit checks, 458 application tests (31 deselected), the 80%
coverage floor at 83.97%, and 25 demo tests. `make hedron-build`, documentation tests, local-link
validation, and `git diff --check` also passed.

## Delivery order

| Step | Deliverable | Findings | Depends on |
|---|---|---|---|
| 0 | Permanent failing reproductions with reliable cleanup | W1–W5 | None |
| 1 | Explicit worker outcomes and protected submission/cleanup | W1, W4 | 0 |
| 2 | Proposed-resource validation without session or provider I/O | W2 | 0 |
| 3 | Atomic admission check and coordinator publication | W3; integrate W2 | 2 |
| 4 | Captured scheduling decisions and compatible runtime forwarding | W5 | 0; integrate 1–3 |
| 5 | Integrated gates and reconciled acceptance records | W1–W5 | 1–4 |

Keep implementation and regression evidence together by boundary. Complete the two P1 behaviors
before recording any acceptance closure. This plan covers runtime remediation; the broader SOLID
migration remains unfinished.

## Step 0: preserve each failure as a regression

Files: `tests/test_pipeline_lifecycle.py`, `tests/test_pipeline_tasks.py`,
`tests/test_solid_boundaries.py`, and connector/runtime tests where appropriate.

1. Add focused regressions for all five findings and record their failure on the current tree.
2. Use events/barriers to establish worker entry, session cleanup, validation, shutdown, and
   publication order. Bound observer waits and release every blocked worker in `finally`.
3. Keep the W1 reproduction in a bounded subprocess or add an explicit retry-count escape in the
   test harness until the fix exists. An in-process timeout cannot interrupt a loop that never
   yields. After remediation, test normal heartbeat progress as well as bounded await attempts.
4. Retain the actual scheduling exception and traceback throughout the W4 admission assertion.
   Do not allow reference collection to conceal the leak.
5. Use distinct real SQLAlchemy engines and real sessionmakers for W2. Register real capability
   metadata in separate registries; do not use signature-only session or registry mocks.
6. Snapshot and restore coordinator runtime, owner kind, supervisor, generation, reservation/token,
   captured facts, app state, dependency overrides, and source settings. Await original worker
   completion before disposing databases. Register diagnostic routes only on isolated apps.

| Regression target | Required assertions and controls |
|---|---|
| `test_owned_worker_cancellation_is_terminal` | A synchronous worker raising `CancelledError` terminates without repeated shields; caller cancellation is distinguished from worker cancellation; admission returns idle after cleanup. |
| `test_cancelled_worker_handle_keeps_running_thread_owned` | If an internal async driver can be independently cancelled while its thread runs, completion ownership survives that cancellation and drain waits for the thread's `finally` cleanup. |
| `test_submission_failure_releases_scope_with_traceback_retained` | Reject task/executor submission; retain the exception; admission is idle, context is restored, and no worker ran. Include nested-scope failure preserving the parent's admission. |
| `test_legacy_publication_rejects_foreign_proposed_resources` | Reject foreign engine, session binds/options, mismatched provider specifications/factories, and captured settings; preserve every coordinator field and perform zero session/provider calls. Valid stable clones and matching snapshots install. |
| `test_shutdown_wins_before_legacy_publication` | Pause validation and stop from a real thread. If shutdown wins, reject without consuming the reservation; if publication wins, observe a valid accepting publication before later shutdown. No sleep-only race assertions. |
| `test_runtime_schedule_uses_captured_environment` | Development-to-test source mutation still schedules; test-to-development source mutation stays disabled. Deferred execution sees the captured runtime/settings. Legacy settings-only behavior and conflicting-owner rejection remain covered. |

Acceptance: every reported defect has a permanent regression that would fail on the reviewed tree,
with positive controls for valid publication, normal worker completion, and existing compatibility.

## Step 1: collect explicit worker outcomes and protect submission (W1, W4)

Files: `app/infrastructure/runtime.py`, `app/ui/routes/pipeline_context.py`, and worker/lifecycle tests.

1. Put the complete interval after successful scope entry under `try/finally`: callable wrapper
   creation, executor/task submission, awaiting, outcome selection, and cleanup. Deterministically
   close the acquired scope on every submission failure. Close any rejected coroutine that was
   created but never scheduled so it does not emit an unawaited-coroutine warning.
2. Represent worker result/failure separately from caller cancellation. Capture worker
   `BaseException` outcomes, including a worker-originated `CancelledError`, so they cannot be
   mistaken for a fresh cancellation request delivered to the caller's await.
3. Preserve a completion handle that represents the synchronous callable and its full session,
   registry, and context cleanup. Prefer one executor completion future returning an explicit
   worker outcome, with copied context applied in the worker, over an extra cancellable async
   driver task. Preserve `asyncio.to_thread` context-propagation behavior if replacing it.
4. If retaining an async driver, independently track actual thread completion. A cancelled driver
   does not prove its executor thread has stopped. Never release admission merely because that
   driver is terminal; handle submission-before-entry races using the actual submission handle.
5. Await only nonterminal completion handles. Collect terminal outcomes exactly once. Remove the
   unconditional retry of a terminal cancelled handle; repeated cancellation of the caller may
   resume the same shielded wait but must never create replacement workers.
6. Keep the AnyIO shield around cleanup and shield the original completion handle from direct
   asyncio caller cancellation. Preserve support for standalone asyncio callers, repeated
   `Task.cancel()`, and cancellation coinciding with worker success or failure.
7. After actual worker cleanup, propagate caller cancellation as primary, attaching worker/cleanup
   failures as secondary evidence. Without caller cancellation, propagate the worker's original
   result or exception, including worker-originated cancellation. Do not stop the application
   runtime because one request is cancelled.
8. Verify successful results, ordinary failure, worker cancellation, caller cancellation before
   entry, repeated cancellation, AnyIO level cancellation, session-close failure, rejected
   submission, context restoration, and parent/child lease counts.

Acceptance: cancellation has a bounded completion path; synchronous work and cleanup remain owned;
submission errors leave no leaked scope, orphan task, or unawaited coroutine even with retained errors.

## Step 2: validate proposed compatibility resources (W2)

Files: `app/services/pipeline_tasks.py`, `app/database.py`, `app/connectors/registry.py`, and authority tests.

1. Keep transition-token and existing-owner identity checks before resource inspection. Obtain
   verified global compatibility facts, then validate the proposed runtime against them before
   assigning owner fields or capturing facts on the proposal.
2. Inspect the proposed sessionmaker's actual engine, mapped binds, session class, and relevant
   factory options. Compare configuration to the verified global factory, allowing the stable
   clones used by normal startup rather than requiring factory object identity.
3. Reject foreign or uninspectable proposed factories at the legacy publication boundary. Do not
   open a session to discover ownership. General instance-owned execution may still use its
   supported callable factories outside this compatibility publication boundary.
4. Compare the proposal's sealed provider specifications and raw factory identity facts with the
   verified source registry, and compare its captured registry settings with runtime execution
   settings and the proposed configuration. Accept matching snapshots; reject missing, extra,
   changed, or foreign providers/factories and mismatched settings authority.
5. Perform these checks using captured metadata only. Do not construct providers, perform network
   calls, or invoke session factories. Compare appropriate snapshot facts rather than assuming a
   snapshot's revision equals its mutable builder's revision.
6. Store the original verified authority only after successful publication. On same-owner
   installation, require the original capture to exist and match; do not refresh mismatched or
   missing captures or promote a temporary owner through incidental reinstall.
7. Prove rejected proposals leave reservation, generation, runtime, kind, supervisor, source
   resources, and proposal fact capture unchanged. Keep global lifespan startup, stable session
   clones, valid extension snapshots, and temporary-owner transitions as positive controls.

Acceptance: an installed compatibility runtime cannot use database B or registry B under verified
global authority A; validation has zero session/provider side effects and valid startup still succeeds.

## Step 3: synchronize publication with shutdown (W3)

Files: `app/services/pipeline_tasks.py`, `app/infrastructure/runtime.py`, and threaded coordinator tests.

1. Define publication's linearization point and document one lock order: coordinator lock before
   runtime lifetime condition. Audit existing startup, retirement, supervisor, and shutdown paths
   for the reverse order. Never hold either lock across an await, session operation, or provider I/O.
2. Integrate proposed-resource validation with a final accepting-state check and publication under
   the same lifetime synchronization used by `begin_shutdown()`. Expose a narrow runtime guard
   if needed instead of scattering access to private lifetime internals.
3. Under that guard, validate lifetime admission and the stop event, preserve token ownership,
   then commit the runtime, original facts, kind, and reservation completion together. A second
   standalone `is_accepting()` call followed by unlocked assignments is insufficient.
4. If shutdown wins, reject without changing any coordinator or proposal capture fields. If
   publication wins, allow subsequent shutdown of that valid generation through the existing
   cleanup protocol. Do not reopen stopped admission or misclassify failed proposals as live owners.
5. Test fresh and same-owner proposals, matching/invalid tokens, already-set stop events, closed
   lifetime admission, live supervisors, and both race orderings using real threads and barriers.
   After rejection, demonstrate reservation abort/retry and valid retirement still work.

Acceptance: synchronized shutdown cannot produce a proposal that was already stopped when published;
rejection preserves ownership and reservation state, and no lock-order deadlock is introduced.

## Step 4: bind scheduling decisions to captured settings (W5)

Files: `app/services/pipeline_tasks.py`, `app/ui/routes/pipeline_runs.py`, and scheduling/route tests.

1. When an execution runtime is supplied, use `runtime.execution_settings` for environment and
   all other behavioral scheduling decisions. Source `runtime.settings` may remain a compatibility
   identity but must not determine whether runtime-owned work is attached or dispatched.
2. Define accepted settings arguments explicitly: support the original owning settings object
   for compatibility and the runtime's captured request settings where appropriate; reject foreign
   objects and conflicting stop events. Do not silently accept an independently equal settings object.
3. Update runtime-aware routes to pass captured request settings or a runtime-only scheduling
   adapter. Remove the route's need to read mutable `execution.settings` to satisfy an identity check.
   Ensure middleware, enqueue policy, dispatch, and deferred execution use the same generation.
4. Forward only the captured runtime and run ID to deferred execution. Preserve settings-only
   legacy adaptation, configured test-mode suppression, demo inline execution, idempotent enqueue,
   supervisor recovery, and existing stop-event conflict validation.
5. Cover mutation in both environment directions, settings-cache replacement, independent A/B
   apps, compatibility arguments, real route scheduling, and worker observations of captured
   values. Configure intended generations explicitly; do not rely solely on mutable fixture mode.

Acceptance: source settings/cache/app-state changes cannot alter dispatch policy for an existing
generation; correct new configuration requires a fresh runtime or deliberate fixture setup.

## Step 5: integrated validation and records

1. Run all named regressions on the final integrated tree. Record terminal outcomes, active lease
   counts, cleanup completion, proposed authority, rejection-state equality, and race ordering.
2. Run focused lifecycle/task/SOLID/connector suites, global/composed lifespan tests, mounted
   readiness/Posit coverage, pipeline enqueue/scheduling/recovery, catalog/authoring, authentication,
   email background work, and full-response cleanup tests affected by the runtime changes.
3. Run `make check` and `make hedron-build`. Record fresh application passed/deselected counts,
   coverage against the existing 80% floor, demo results, Ruff/formatting, BasedPyright, Hedron/Posit
   results, and build digest. Earlier passing gates remain historical evidence.
4. Run documentation tests, actual local-link validation for changed records, and `git diff --check`.
   Preserve pre-existing files and remove only new artifacts attributable to this implementation.
5. Update this plan, the plan index, major SOLID plan, and prior admission/cancellation record
   together. Close W1–W5 only after the permanent regressions and final gates pass. Keep the broader
   unfinished SOLID migration explicit.

Definition of done: worker cancellation terminates without spinning; actual worker cleanup and
submission failures release admission deterministically; proposed resources are verified before
atomic publication; dispatch uses captured settings; all five regressions and final gates pass.
