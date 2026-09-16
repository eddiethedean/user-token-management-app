# SOLID runtime review: owner generations and shutdown outcomes

Status: earlier fixes implemented; runtime acceptance reopened by C1–C6 in the
[completion and generation isolation plan](solid-runtime-completion-isolation-remediation.md).
Implementation evidence below is historical and does not close the active findings. This is the
earlier remediation record following review of the
[failure handling and legacy ownership implementation](solid-runtime-failure-remediation.md).
Reviewed on 2026-09-16. Retain the earlier implementation work and evidence as historical context.

## Findings and acceptance targets

| ID | Priority | Reproduced behavior | Required result |
|---|---|---|---|
| R1 | P1 | Create cached owner A, mutate the same settings object and replace global engine/factory with B: compatibility validation accepts the call but returns A's sessions. The pipeline file lock follows B's settings. | Cached ownership facts remain consistent; changed settings, engine, factory configuration, or registry require rejection or an explicit drained transition before work. |
| R2 | P1 | Enter a second global lifespan while the first is live: startup retires the first runtime, leaves the first app ready, and installs the second owner. Second shutdown disposes their shared engine. | A live lifespan owner cannot be replaced by unrelated startup; rejection leaves its readiness, admission, supervisor, registry, and engine unchanged. |
| R3 | P2 | A supervisor completion callback cancels the draining caller: the caller has a cancellation request, but the drain outcome reports `cancelled=False`. | Caller cancellation remains observable after supervisor/lifetime completion, including completion races and repeated cancellation. |
| R4 | P2 | The lifespan body raises, then disposal raises: the disposal exception replaces the saved body failure. Both lifespans have this ordering. | Body failure/cancellation remains primary; disposal is attempted once after proven drain and its failure remains inspectable. |
| R5 | P2 | The body and supervisor both fail: the body exception is propagated without the supervisor error in its cause, context, or notes. | Every secondary failure is retained while the primary exception and cancellation semantics are preserved. |
| R6 | P2 | A settings-only supervisor exits and leaves its closed runtime installed; a second call with a fresh event fails ownership validation. | A completed standalone legacy supervisor closes/drains/releases its generation, allowing a fresh verified generation without reopening stale tasks. |

Review evidence: 80 affected tests passed and `git diff --check` passed. Separate deterministic
probes reproduced all six failures. The earlier full gate (434 tests, 31 deselections, 84.31%
coverage, 25 demo tests) is historical and does not establish acceptance for these cases.

Scope: legacy database/registry ownership, runtime generation coordination, protected task
completion, both app lifespans, regression fixtures, and plan records. Preserve explicit independent
runtimes, public settings-only entrypoints, structured URL validation, admitted-operation draining,
durable queued-run recovery, worker lease fencing, schema, provider IDs, UI/API behavior, and
credential policy. No deployment or unrelated migration is included.

## Runtime contracts

1. One generation owns its engine-bound session factory, registry, execution settings, stop event,
   pipeline lock, and operation lifetime. Owner identity and captured resource facts are separate.
   Validation has no database, provider, or file-lock side effects and exposes no credential values.
2. Temporary compatibility, standalone supervisor, and lifespan ownership are explicit provenance.
   Event state or lifetime idleness cannot establish provenance or authorize replacement.
3. Generation lifecycle states are accepting, transitioning, closed/draining, and released; a
   disposal failure may retain a closed cleanup-failed owner. A reservation belongs to a unique
   transition token. Publication and release compare the token/generation, not only settings.
4. Replacement closes admission and reserves ownership under the coordinator lock, drains outside
   the lock, then publishes under the same reservation. No concurrent compatibility call can fill
   a publication gap. Old deferred tasks keep their permanently closed runtime.
5. Supervisor/task terminal outcome and caller cancellation are independent facts. A completed or
   cancelled wrapper alone is not proof its underlying synchronous work has finished.
6. Disposal follows supervisor completion and zero admitted operations, including session/context/
   lock cleanup. An unexpected drain error is not completion proof and does not authorize disposal.
7. Error precedence: original body exception/cancellation; otherwise caller cancellation during
   cleanup; otherwise supervisor/worker failure; otherwise drain/disposal failure. All other failures
   remain diagnostic evidence. When several cleanup failures exist without an earlier primary,
   choose the first observed failure and retain the rest. A child cancellation is a terminal child
   outcome; it must not manufacture a caller cancellation request.

## Delivery order

| Step | Work | Dependencies | Gate |
|---|---|---|---|
| 0 | Regression fixtures and named R1–R6 failures | None | Each current failure reproduces at its actual boundary. |
| 1 | Capture and verify generation resource facts (R1) | 0 | Cached changes reject before I/O; stable captured work retains its owner. |
| 2 | Preserve caller cancellation and terminal outcomes (R3) | 0 | Completion races, repeated cancellation, and child outcomes behave correctly. |
| 3 | Share shutdown outcome reporting across lifespans (R4/R5) | 2 | Disposal ordering and primary/secondary evidence pass for both lifespans. |
| 4 | Reserve provenance-aware startup transitions (R2) | 1–3 | Live owner rejection and idle/busy temporary transitions are atomic. |
| 5 | Complete standalone supervisor generations (R6) | 4 | Sequential restart succeeds; concurrent replacement is rejected. |
| 6 | Final compatibility gates and evidence reconciliation | 1–5 | Final tree passes gates and each finding has named evidence. |

## Step 0: regression coverage

Files: `tests/test_pipeline_tasks.py`, `tests/test_pipeline_lifecycle.py`,
`tests/test_solid_boundaries.py`, and narrowly scoped ownership fixtures in `tests/conftest.py`.

- R1: `test_cached_legacy_owner_rejects_changed_resource_facts`, parameterized for settings URL
  mutation plus A/B rebind, engine replacement at the same URL, source factory replacement,
  default/model binding changes, and registry replacement/configuration changes. Exercise an actual
  settings-only worker boundary; assert zero session creation, file-lock entry, and provider work on
  rejection. Include unchanged owner reuse and matching password/query URL positive controls.
- R2: `test_second_global_lifespan_rejects_live_owner_without_mutation`. Enter actual nested global
  lifespan contexts in the same async task. Assert second startup fails before connector reload or
  shared disposal, first stays ready and admitting, and its original supervisor remains installed.
  Separately verify ordinary restart after first exit and independent composed A/B apps.
- R3: `test_drain_records_caller_cancellation_at_supervisor_completion`. Attach a supervisor done
  callback that cancels the caller before shield resumes. Parameterize success/failure/child
  cancellation and assert cancellation after cleanup. Add repeated caller cancellation while a real
  admitted worker is blocked, plus child cancellation without caller cancellation.
- R4: `test_lifespan_disposal_failure_preserves_primary_outcome`, parameterized for global/composed
  lifespans and body failure/body cancellation/cleanup cancellation/no earlier failure. Count drain
  and disposal; block real worker/session cleanup and assert no early disposal. Inject disposal
  failure after the actual close action, inspect primary identity and secondary evidence.
- R5: `test_lifespan_body_failure_retains_supervisor_error`, parameterized for both lifespans,
  body failure/cancellation, and supervisor already failed/fails during shutdown. Assert the same
  body exception survives and supervisor evidence is programmatically inspectable. Include combined
  body, supervisor, and disposal failures and preexisting body exception chaining.
- R6: `test_sequential_legacy_supervisor_generations_restart`. Run the real public settings-only
  supervisor twice with distinct events and normal worker boundaries against an isolated database.
  Verify fresh lifetime/factory/runtime ownership, exactly one supervisor per generation, queued
  recovery, and stale deferred work declining before I/O. Cover ordinary failure and cancellation
  of the first generation, preserving their outcome before the next invocation.

Use events/barriers, completion callbacks, and bounded observer waits rather than timing sleeps.
Keep lifespan entry/exit in the same async task. In teardown, release blocked work, await retained
handles, restore settings cache/global engine/factory/registry/coordinator, and dispose only owned
test resources. Manual lifetime tokens supplement real worker/session cleanup coverage.

## Step 1: verify captured ownership (R1)

Files: `app/database.py`, `app/services/pipeline_tasks.py`,
`app/infrastructure/runtime.py` if a small snapshot type belongs there; ownership fixture helpers.

1. Introduce an internal immutable resource-facts record: source engine identity and structured URL,
   source sessionmaker identity/class/options/default and model binds, registry identity and owner
   settings identity, plus the settings values relevant to execution. Do not log fingerprints or
   raw settings/URLs. Preserve legitimate session class and factory options in the captured factory.
2. Capture/verify current facts and cached-generation facts inside the coordinator synchronization
   boundary. Matching settings identity alone does not suffice. Reject changed facts before
   admission, file locks, sessions, registry reload, or provider calls; unchanged facts reuse the
   same generation. Reject opaque unverifiable factories and foreign model-specific binds.
3. Build a stable factory from verified facts for temporary and global lifespan owners; the global
   lifespan must not retain mutable `SessionLocal` as its execution factory. Capture execution
   settings so later mutation of the source Settings instance cannot redirect an admitted worker's
   file lock or policy. Keep source-owner identity in coordinator metadata; align app-owned settings
   and scheduling validation with the captured execution settings rather than weakening checks.
4. Route supported fixture/bootstrap rebinding through an explicit coordinated operation requiring
   no accepting owner or a completed reserved transition. Do not promise safety for arbitrary raw
   concurrent assignments. A new compatibility boundary still rejects changed current facts.
5. Preserve independent explicit-runtime context restoration and complete structured URL comparison.

Acceptance: A/B settings/session/file-lock agreement, same-URL engine changes, cached registry changes,
factory option/class preservation, deferred/admitted work after attempted rebinding, and zero I/O
on rejected validation. No owner is silently replaced to accommodate a mismatch.

## Step 2: unify protected completion (R3)

Files: `app/services/pipeline_tasks.py`, task/lifecycle regression tests.

1. Share narrow terminal-result/cancellation observation logic between `_drainable_thread_call` and
   supervisor draining. Retain the original task handle, terminal result/error, and caller
   cancellation observation separately; dispatch once and consume the final child outcome once.
2. When shield raises `CancelledError`, inspect caller cancellation state as well as child state
   before any `task.done()` shortcut. Capture cancellation arriving from a completion callback,
   including simultaneous child cancellation. Do not clear requests with `uncancel()` or classify
   a cancelled child alone as caller cancellation.
3. On caller cancellation, close admission immediately and keep shielding the same task. Record
   repeated requests without abandoning work. Retain the independent protected lifetime wait and
   return a drain outcome only once supervisor and admitted-work completion are established.
4. Keep direct cancellation of internal thread wrappers outside the supported caller API; ensure
   owned handles remain protected. Where an externally cancelled supervisor is supported, lifetime
   cleanup remains mandatory and any synchronous callable completion evidence must still be awaited.

Acceptance: successful results, ordinary errors, child cancellation, caller cancellation followed by
success/error, simultaneous child/caller cancellation, repeated requests, and completion callbacks.
Threaded calls remain cancelled after cleanup with worker failure retained; drain records equivalent
facts for the lifespan to report after disposal.

## Step 3: preserve all shutdown outcomes (R4/R5)

Files: `app/main.py`, a small shared runtime shutdown/outcome helper, lifecycle tests.

1. Use the same stop/drain/dispose/report sequence for global and composed lifespans. Set readiness
   false first, save the exact body exception, and gather supervisor/cancellation/drain facts.
2. Attempt the owned disposal callback exactly once only after drain completion proof. Capture
   disposal exceptions instead of letting them escape ahead of outcome selection. Do not retry
   disposal automatically, dispatch another worker, or dispose after an unproven drain failure.
3. Select the primary according to the runtime contracts. Re-raise the original body exception
   object when present; preserve `CancelledError` and final task cancellation semantics. Without an
   earlier primary, ordinary supervisor and disposal errors keep their original type/identity.
4. Attach secondary exception objects through explicit chaining or an appropriate
   `BaseExceptionGroup` as diagnostic cause; do not rely on incidental `__context__`, silently drop
   supervisor outcomes, or replace cancellation with a group as the primary. Preserve any existing
   primary chain in the diagnostics and avoid credential-bearing textual summaries.
5. Global release must follow closed/drained cleanup. If disposal fails, retain a defined closed
   cleanup-failed owner that refuses new work/replacement until an explicit cleanup path succeeds;
   do not publish an accepting replacement from an unconditional `finally` release. Composed
   resources remain independently owned and closed to admission on cleanup failure.

Acceptance matrix for both lifespans: clean exit; body failure/cancellation; supervisor success,
already/during-drain failure, or cancellation; single/repeated cleanup cancellation; disposal failure;
and combinations of body, supervisor, and disposal failures. Observe actual session/context/lock/
lifetime completion before one disposal, retained secondary objects, and zero database readiness work.

## Step 4: reserve startup ownership (R2)

Files: coordinator in `app/services/pipeline_tasks.py`, global startup in `app/main.py`, supported
database/registry bootstrap helpers, ownership tests.

1. Replace the bare runtime/task globals' implicit ownership with a coordinator record carrying
   generation ID, provenance, lifecycle state, captured facts, supervisor handle, and transition
   token. Preserve private compatibility access only where needed; do not expand public API surface.
2. Global startup acquires a reservation before shared registry/configuration mutation. Reject a
   live lifespan owner or live standalone supervisor without stopping it or changing readiness.
   Permit transition from a temporary compatibility owner after closing its admission atomically.
3. Retain the old supervisor handle throughout drain; never clear it before completion proof.
   Drain outside the coordinator lock. During a reservation, new settings-only calls/installers
   fail deterministically before I/O; none can publish an owner between retirement and installation.
4. After drain, verify the token and revalidate proposed resource facts before publishing the new
   captured runtime/event/lifetime atomically. Old queued callbacks retain the closed generation.
   Ready becomes true only once ownership and startup initialization succeed.
5. Startup failure/cancellation cleans up its own reservation/resources while preserving the primary
   outcome. It cannot release another generation or revive the old owner. Repeated install of the
   same owner must not erase a live supervisor handle; different-owner install remains guarded.
6. Owner release verifies provenance/generation and closed/drained cleanup evidence. Document an
   explicit recovery route for cleanup-failed ownership instead of guessing from stop-event state.

Acceptance: live nested-global rejection, clean restart, idle/busy temporary owner to actual startup,
concurrent startup/compatibility calls, attached old tasks, startup cancellation/failure, same-owner
reinstall protection, cleanup-failure refusal, and independent composed app controls.

## Step 5: close standalone generations (R6)

Files: `run_background_runtime` and coordinator/task starters in `app/services/pipeline_tasks.py`.

1. Register a public settings-only supervisor as standalone provenance with its actual task handle
   before its first await. A second active standalone invocation rejects without stopping the first.
   Lifespan-owned supervisors remain registered under their lifespan owner and never auto-release it.
2. On standalone supervisor termination, close admission, finish protected dispatched work, and wait
   for all admitted operations. Do not await the supervisor task from itself; use the lifetime wait
   directly/shared completion logic for its final cleanup. Preserve failure/cancellation precedence.
3. Release only that standalone generation after completion proof, then propagate its original
   result/error/cancellation. A subsequent public invocation creates a fresh verified generation
   with its supplied event. Never swap events on a reused runtime or reopen the old lifetime.
4. Closed deferred references decline before I/O; durable queued work is recovered by the next
   generation. If terminal cleanup cannot complete, retain a closed diagnosable owner rather than
   making restart appear safe. Public one-shot temporary owners still transition through Step 4.

## Step 6: validation and records

1. Run each named regression on the current implementation to establish its failure, then on the
   fix. Record real boundary behavior and positive controls, not just signatures or manual leases.
2. Run `tests/test_pipeline_tasks.py`, `tests/test_pipeline_lifecycle.py`,
   `tests/test_solid_boundaries.py`, `tests/test_user_catalog.py`, and `tests/test_pipelines.py`.
3. Run `make check` and `make hedron-build` on the final implementation tree. Record actual test and
   deselection counts, coverage with the existing 80% floor, demo tests, Ruff/formatting,
   BasedPyright, Hedron, and Posit results. Rerun affected gates after any final code changes.
4. Run documentation tests, validate local links in changed plan records, and run `git diff --check`.
   Preserve unrelated worktree changes and remove only artifacts attributable to this work.
5. Map R1–R6 to named passing tests and final-tree evidence here. Reconcile the major, completion,
   milestone/follow-up, earlier runtime, six-issue, failure-handling, and index records. Historical
   passing gates remain historical; restore runtime acceptance only after all six findings close.

## Implementation evidence

- **R1:** Compatibility generations now capture structured database, engine, session-factory,
  registry, and settings facts under the owner lock. Cached fact changes reject before worker I/O;
  stable session factories and frozen execution settings prevent later rebinding.
- **R2:** Owner provenance distinguishes temporary, standalone, lifespan, transitioning, and closed
  generations. Live lifespan retirement is rejected; temporary transitions reserve ownership while
  draining outside the coordinator lock.
- **R3:** Protected supervisor/thread handles retain terminal outcomes and independently observe caller
  cancellation, including completion-callback races and repeated cancellation. Idle waits remain
  shielded until admitted work reaches zero.
- **R4/R5:** Both lifespans use shared shutdown precedence. Disposal follows proven drain completion;
  body failures remain primary and supervisor, drain, and disposal failures are retained as structured
  secondary diagnostics.
- **R6:** Standalone settings-only supervisors register their task, close and drain their generation,
  release it after completion, and permit a fresh event/runtime generation on sequential restart.
- **Regression evidence:** 23 focused pipeline/lifecycle tests pass, including cached-owner mutation,
  completion-callback cancellation, live-owner retirement, sequential restart, and combined body /
  supervisor / disposal failures.
- **Final gates:** `make check` passed with 440 tests, 31 deselections, 84.46% coverage, and 25
  demo tests. `make hedron-build` passed with digest `c0123a9179acac33da4470eaec37879f5df3a884f43e67faa4d512c78670a929`.

Definition of done: cached calls cannot mix owners; unrelated startup cannot retire a live lifespan;
caller cancellation survives completion races; both lifespans preserve primary and secondary errors
after actual cleanup; sequential standalone supervisors use fresh safely released generations; all
named acceptance scenarios and final gates pass. This plan requires no external approval to implement.
