# SOLID runtime review: drain completion and owner publication

Status: earlier changes implemented; runtime acceptance reopened by C1–C6 in the
[completion and generation isolation plan](solid-runtime-completion-isolation-remediation.md).
The evidence below is historical and does not close those follow-on findings. This record follows review of the
[owner generations and shutdown outcomes implementation](solid-runtime-owner-outcome-remediation.md).
Reviewed and implemented on 2026-09-16.

Implementation evidence: 445 tests passed with 31 deselected and 84.41% coverage in the final full
gate; the focused lifecycle/task/SOLID suites and five named D1–D5 regressions pass. Ruff,
formatting, BasedPyright, Hedron, Posit, documentation tests, link checks, and `git diff --check`
also pass.

## Findings and acceptance targets

| ID | Priority | Reproduced failure | Required behavior |
|---|---|---|---|
| D1 | P1 | `cancelled or await _wait_for_runtime_idle(...)` skipped the idle wait after cancellation. | Fixed: supervisor completion and admitted-operation completion are established independently of cancellation flags before disposal. |
| D2 | P1 | `_request_execution` treated readiness false as permission to create a global runtime. | Fixed: requests capture the composed runtime and retain it through draining; new work is declined after admission closes. |
| D3 | P2 | Retirement cleared the startup reservation before connector loading and installation. | Fixed: a tokenized startup reservation remains authoritative through publication or abort. |
| D4 | P2 | Standalone final cleanup ignored the cancellation flag returned by its protected idle wait. | Fixed: final-cleanup cancellation is propagated after drain and matching generation release. |
| D5 | P2 | Resource facts retained only registry identity/settings. | Fixed: revision/configuration facts reject changed compatibility resources and runtime generations own registry snapshots. |

The subsequent review passed 42 focused tests but reproduced missing downstream completion,
closed-app global fallback, reservation theft, mutable composed registries, unadmitted direct
operations, and same-owner fact refresh. The new plan is the active acceptance authority.

Scope: operation admission/draining, request runtime resolution, global startup generation
coordination, standalone supervisor cleanup, connector registry snapshots, regression fixtures,
and plan records. Preserve structured URLs, stable session factories, public settings-only worker
entrypoints, independent composed apps, error precedence, queued-run recovery, lease fencing,
schema, providers, saved definitions, routes/fragments, and credential policy. No deployment,
provider network qualification, or unrelated migration is included.

## Completion and ownership contracts

1. Shutdown always waits for the original supervisor handle and zero admitted operations. Boolean
   outcome aggregation is performed after awaited completion, never around a conditional await.
2. Readiness is an availability response, not an owner selector. Lifecycle states distinguish
   not-started, accepting, draining, and closed. A draining/closed app never falls back to globals.
3. Each request captures its owner's runtime before dependencies execute. Settings dependencies,
   cookie policy, session context, connector context, catalog runners, and authoring use that owner
   throughout downstream completion. No late readiness check can change the captured generation.
4. Work admitted before closure may finish under its captured owner; new database work after
   closure declines with the existing service-unavailable response convention before sessions or
   provider I/O. Health/readiness checks retain their current response contracts and zero-I/O drain
   behavior. Session/context cleanup is part of completion proof.
5. A startup reservation is created even when no previous owner exists. Only its token may publish
   the fresh owner or complete its cleanup. Supported database/registry publication uses the same
   synchronization boundary as ownership validation; draining never holds that lock.
6. Registry identity and configuration are separate facts. Generations own immutable provider
   capabilities/factories and captured settings; clear/register/reload cannot alter captured work.
7. Cancellation and terminal child outcomes remain separate. Primary outcome order stays: body
   failure/cancellation, otherwise cleanup caller cancellation, otherwise supervisor/worker failure,
   otherwise cleanup/disposal failure. Secondary exception objects and existing chains are retained.

## Implementation order

| Step | Deliverable | Depends on | Gate |
|---|---|---|---|
| 0 | Named D1–D5 regressions and owner-isolated fixtures | None | Current failures reproduce at actual entrypoints. |
| 1 | Unconditional protected drain (D1) | 0 | Cancellation never bypasses admitted work. |
| 2 | Immutable registry generations (D5) | 0 | Registry changes reject at compatibility boundaries; captured work stays stable. |
| 3 | Request owner capture and closure behavior (D2) | 1–2 | Both lifespans preserve owner and await admitted request cleanup. |
| 4 | Reservation through startup publication (D3) | 1–2 | Compatibility calls cannot publish in a startup gap. |
| 5 | Standalone final-cleanup outcome propagation (D4) | 1, 4 | Cancellation remains observable after safe generation release. |
| 6 | Final gates and records | 1–5 | Each finding has named passing evidence on the final tree. |

## Step 0: regression fixtures

Files: `tests/test_pipeline_tasks.py`, `tests/test_pipeline_lifecycle.py`,
`tests/test_solid_boundaries.py`, relevant UI/authoring tests, and `tests/conftest.py`.

- D1: `test_cancelled_lifespan_drains_real_worker_before_disposal`, parameterized for global and
  composed lifespans. Block the real deferred worker after session entry; hold the supervisor
  pending; cancel the lifespan caller while drain awaits the supervisor; complete that supervisor
  while the worker remains blocked. Assert no disposal until worker/session/context/lock/lifetime
  cleanup, then one disposal and observable cancellation. Include supervisor failure, repeated
  cancellation, and completion-callback cancellation. A manual lease is supplemental coverage.
- D2: `test_inflight_request_keeps_composed_owner_during_shutdown`. Authenticate in B, pause before
  catalog/authoring resolution, begin B shutdown, and resume. Inspect actual session engine, settings,
  provider authority, and resulting read/write behavior. The request completes under an admitted B
  owner or declines before new I/O; it never accesses A. Test IDs present in both databases and IDs
  present only in B so an erroneous global read cannot hide behind a missing-user error.
- D3: `test_startup_reservation_blocks_compatibility_publication`. Start with an idle and an admitted
  temporary owner; pause actual global startup after old drain and during connector preparation.
  Invoke settings-only compatibility work from a separate thread. Assert rejection before I/O,
  retained token, successful single owner publication, and no accepting generation in the gap.
  Include startup with no previous owner, competing startup/installer, and cancellation/failure.
- D4: `test_standalone_cancellation_during_final_idle_wait_is_propagated`. Stop the real public
  settings-only supervisor normally while separately admitted work remains active. Cancel only
  after its final idle wait begins; release the blocked work. Assert `CancelledError`, final
  `task.cancelled()`, no early release, and successful subsequent fresh generation. Add repeated
  cancellation and a prior supervisor failure to check primary/secondary evidence.
- D5: `test_cached_owner_rejects_inplace_registry_changes`. Establish an owner with configured
  providers, then attempt public clear/register/reload under the same settings and registry
  identity. Assert mutation refusal for a sealed generation or rejection at the next compatibility
  boundary before I/O. Attached/admitted work retains original capabilities, factory, and settings.

Use barriers/events and bounded observer waits, not timing sleeps. Keep lifespan entry and exit in
the same async task. Always release blocked work and await original handles in teardown. Fixtures
restore settings cache, database factory/engine, registry, coordinator/token, and environment.
Direct clients must enter lifespan or explicitly install their isolated test owner; a readiness
flag must not serve as a fixture-mode signal.

## Step 1: await completion unconditionally (D1)

Files: `app/services/pipeline_tasks.py`, both lifecycle regression variants.

1. Await `_wait_for_runtime_idle(runtime)` into a separate local variable on every drain path,
   including when caller cancellation was already recorded. Combine cancellation flags afterwards.
2. Retain shielding of original supervisor/idle handles and one terminal-result retrieval. Caller
   cancellation sets shutdown immediately, never abandons a thread, and never retries provider work.
3. Audit asynchronous outcome aggregation for other short-circuit awaits and ignored completion
   results in the touched lifecycle helpers. Do not broaden the change to unrelated services.
4. A drain outcome is constructed only after supervisor and admitted-operation completion. Keep
   failure reporting after that proof; unexpected drain failure remains insufficient for disposal.

Acceptance: blocked real worker/session cleanup, supervisor success/error/cancellation/absence,
single/repeated/completion-race caller cancellation, and one disposal only after actual completion.

## Step 2: capture immutable provider authority (D5)

Files: `app/connectors/registry.py`, `app/infrastructure/runtime.py`, compatibility resource facts
in `app/services/pipeline_tasks.py`, registry/bootstrap tests.

1. Add explicit registry configuration facts, including provider specification identities,
   capabilities, factories, captured owner settings, and a revision advanced by supported changes.
   Snapshot comparisons must not retain mutable dicts by reference. Validate identity/configuration
   rather than treating the same registry object as unchanged authority.
2. Build a read-only generation registry snapshot. Preserve public provider APIs and narrow roles;
   do not instantiate providers or contact services during snapshot validation. Registration-time
   validation remains available for a mutable registry builder before publication.
3. Bind snapshot factories to the captured registry and execution settings. Copying existing bound
   closures alone is insufficient if they still reference a mutable source registry/settings object.
   Retain internal source-factory metadata or an equivalent explicit binding mechanism.
4. Cached legacy validation compares the source registry's captured revision/specifications with
   current facts before admission. Changed facts require rejection or the reserved transition in
   Step 4; no implicit owner refresh legitimizes a changed registry.
5. Supported reload builds a fresh registry generation and publishes only after old admission is
   closed and drained. Old deferred references remain closed and old admitted operations retain
   their immutable snapshot. Update fixtures that directly clear a live generation.

Acceptance: clear, provider addition/removal/replacement, same-identity reload, writer/capability
changes, unchanged positive reuse, frozen factory settings, and independent A/B provider controls.

## Step 3: preserve request generation ownership (D2)

Files: `app/main.py`, `app/database.py`, catalog/authoring infrastructure runners,
`app/infrastructure/runtime.py` only for a narrow admission API, and request/lifecycle tests.

1. Introduce explicit app lifecycle state and capture the owning runtime on request entry. Remove
   readiness-dependent global fallback from `_request_execution` and middleware owner selection.
   Resolve subsequent operation factories from the request's captured owner, not mutable app state.
2. Pin settings, database factory, registry, and cookie/error-response policy consistently to that
   owner. Composed dependency overrides and direct composed clients must never resolve global A.
   Keep an explicit fixture/bootstrap path for clients without lifespan; draining/closed state is
   never that path. Update shared test fixtures instead of recreating accepting globals per request.
3. Admit lifecycle-owned data requests before dependencies open sessions and retain a lease through
   actual downstream request, session/context, response/background cleanup. Reuse the generation
   lifetime through a narrow generalized execution-operation admission method; preserve existing
   pipeline entrypoints. Release in a completion finalizer around the downstream ASGI call, not
   merely when `call_next` returns a streaming response.
4. Set readiness false and close admission as shutdown begins. Already-admitted requests keep their
   owner and complete; new data requests decline before I/O. Health/readiness responses remain
   available without admission or database work. Avoid holding any coordinator lock while waiting.
5. Catalog/authoring work invoked outside the normal request boundary needs its own operation lease
   or an explicit admitted-owner contract; it cannot bypass lifetime accounting by opening sessions
   directly. Nested request/background admissions must release independently without self-await.

Acceptance: late operation resolution after shutdown begins, actual B-only reads/writes, streaming
and background completion, rejected new data requests, zero-I/O readiness during drain, ordinary
global/direct fixture behavior, and A continuing while B shuts down.

## Step 4: reserve through publication (D3)

Files: legacy coordinator in `app/services/pipeline_tasks.py`, global startup in `app/main.py`,
supported database/registry publication helpers, lifecycle/ownership fixtures.

1. Replace the retire-then-unreserved-install sequence with prepare/drain/publish/abort operations
   carrying one reservation token. Preparation reserves even an ownerless coordinator, verifies
   proposed facts, and rejects live lifespan or standalone owners without mutating their state.
2. Close a temporary owner's admission under the lock and retain its runtime/supervisor/token
   throughout drain. Await outside the lock. Never clear the token simply because old work finished.
3. Prepare fresh settings, stable sessions, and provider registry privately. Do not clear/reload
   shared source registry configuration while another generation can still access it. Publish the
   new runtime and compatibility resource facts atomically under the validated token.
4. Settings-only calls and unrelated installers reject while reserved, including after old runtime
   retirement. Only matching-token publication ends the reservation and enables new admission.
   Validate cached snapshot/configuration before installation; same-owner reinstall cannot refresh
   inconsistent facts or erase the retained supervisor handle.
5. Startup failure/cancellation drains any admitted old/new work and cleans up resources owned by
   that attempt before token release. Retain the primary outcome and secondary cleanup evidence.
   Incomplete cleanup leaves a closed diagnosable owner; retry cannot reopen it or steal another token.
6. Keep any retained `retire_legacy_runtime` compatibility wrapper explicitly separate from startup
   publication. Document its completion/release contract so startup cannot accidentally use it as
   an unreserved transition again.

Acceptance: idle/busy/absent temporary owner, compatibility calls in every startup phase, two startups,
different-token install, startup failure/cancellation, old attached tasks, cleanup retry, clean restart,
live-owner rejection, and independent composed app controls.

## Step 5: report final-cleanup cancellation (D4)

Files: standalone supervisor exit path in `app/services/pipeline_tasks.py`, task/lifecycle tests.

1. Save the supervisor's exact terminal result/error/cancellation before final cleanup. Close
   admission, await the original protected work and independent idle wait, and record cancellation
   observed during that wait rather than ignoring its return value.
2. Release only the matching standalone generation after completion proof. Never await the
   supervisor task from itself. Retain a closed owner when drain completion cannot be established.
3. After release, apply shared primary/secondary outcome precedence. A cancellation first delivered
   during final cleanup makes the task cancelled; supervisor failure remains inspectable. Existing
   cancellation/failure must also survive later cleanup errors and repeated cancellation requests.
4. Lifespan-owned supervisors never auto-release lifespan ownership. A subsequent standalone call
   publishes a fresh verified runtime/event/lifetime; stale callbacks decline under their old owner.

## Step 6: final gates and evidence

1. Establish each named regression failing on the current behavior, then passing on its fix. Record
   actual entrypoint/session/provider ownership and completion observations. Manual leases and mocked
   signatures supplement real boundaries rather than close their acceptance.
2. Run task, lifecycle, SOLID boundary, user catalog, pipeline, and affected UI/authoring suites.
3. Run `make check` and `make hedron-build` on the final implementation tree. Record test counts,
   deselections, coverage with the existing 80% floor, demo tests, Ruff/formatting, BasedPyright,
   Hedron, and Posit results. Final code changes require affected validation again.
4. Run documentation tests, validate links in changed plan records, and run `git diff --check`.
   Preserve unrelated worktree changes and remove only artifacts created by this work.
5. Map D1–D5 to named acceptance evidence here. Reconcile the index and earlier major, completion,
   milestone/follow-up, runtime, failure-handling, and owner/outcome records. Historical full-suite
   passes cannot substitute for the new cases; no runtime closure until all five targets pass.

Definition of done: cancellation cannot bypass actual completion or disappear during final cleanup;
requests retain their own generation through shutdown; startup has no unreserved publication gap;
captured provider authority is immutable; named boundary regressions and final repository gates pass.
