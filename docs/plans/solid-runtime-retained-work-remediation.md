# SOLID runtime review: retained work and explicit ownership

Status: earlier changes implemented; the R1–R7 follow-on runtime remediation is closed in the
[admission, cancellation, and validated publication plan](solid-runtime-admission-cancellation-remediation.md).
The evidence below is historical context for this record. This record follows review of the
[completion and generation isolation implementation](solid-runtime-completion-isolation-remediation.md).
Reviewed and implemented on 2026-09-16. Subsequent review found readiness admission, cancellation,
child context, settings authority, metadata validation, and fresh-publication gaps.

## Findings and required results

| ID | Priority | Reproduced failure | Acceptance target |
|---|---|---|---|
| F1 | P1 | Cancelling a request through the full FastAPI/Hedron stack finishes the request while its borrowed `asyncio.to_thread` worker is running; the runtime lifetime already reports idle. | Scheduled and running owned work retains admission through worker, session, and context cleanup. Request cancellation or transport failure cannot allow early resource disposal. |
| F2 | P1 | A direct client for unstarted composed app B returns global database A; `_request_execution` also constructs A's runtime. | Unstarted production/composed apps reject data work before I/O. Only an explicitly established fixture owner permits fixture requests, and it captures its own resources. |
| F3 | P2 | Initialization raises an SQLAlchemy error; the app says `closed`, but the retained execution runtime still admits direct operations. | Startup failure closes runtime admission, leaves readiness false, retains the original exception, and cannot expose an accepting partial generation. |
| F4 | P2 | A production request observes changed `SettingsDep` values alongside the original captured `request.state.settings`. | Middleware, authentication, CSRF, cookies, route policy, error rendering, and deferred work use the same captured settings generation. |
| F5 | P2 | A draining app returns 503 for `/content/b/health` but 200 for `/health`. | Liveness classification uses the normalized route path. Mounted health remains available; mounted readiness is unavailable without database I/O during shutdown. |
| F6 | P2 | Registering a provider calls its factory once; snapshotting calls it a second time. | Publication copies validated immutable provider metadata and binds factories to captured settings/registry without constructing providers or performing provider I/O. |
| F7 | P2 | Reinstalling a stopped temporary legacy owner succeeds and promotes it to `lifespan`; retirement then rejects it as a live owner. | Same-owner installation rejects stopped/closed admission and otherwise preserves original facts, kind, generation, supervisor, and reservation state. |

Review baseline: 44 focused lifecycle/task/SOLID tests passed, and separate probes reproduced all
seven findings. Historical compatibility gates for that implementation were:
447 passed, 31 deselected, 83.83% coverage, 25 demo tests, and build digest
`c0123a9179acac33da4470eaec37879f5df3a884f43e67faa4d512c78670a929`.

Scope: operation retention, request-owned synchronous work, lifecycle selection and startup
failure, request settings, mounted liveness, connector publication, legacy same-owner validation,
fixtures, boundary tests, and records. Preserve existing routes, schema, provider protocols,
credential policy, queued-run recovery, independent app instances, shutdown error precedence,
settings-only CLI/worker APIs, and the single runtime lifetime counter. No deployment or schema
migration is required.

## Design decisions

1. Admission belongs to a runtime generation. A request, nested operation, and its scheduled
   workers can retain that admission; a copied context alone is not ownership.
2. Retain work before scheduling it. Acquiring a reference only after a thread starts leaves a
   cancellation-before-entry race. Retention lasts through all owned cleanup, not just the last
   provider call or response body.
3. Closing an outer scope prevents stale copies of that handle from borrowing. An already-retained
   child may finish and perform nested work under its own live handle during runtime shutdown.
4. Request completion waits on that request's original worker handles, never global runtime idle
   while the request still holds its own admission. This avoids waiting on itself or unrelated work.
5. Request cancellation remains the caller-visible outcome after cleanup. Cancelling one request
   does not call `runtime.begin_shutdown()` or retire the app. Preserve worker/cleanup failures as
   secondary evidence, without replacing the original cancellation or transport error.
6. App lifecycle is explicit: `not_started`, `starting`, `accepting`, `draining`, `closed`, and
   scoped `fixture`. Missing lifecycle is not fixture authorization. Production and composed data
   requests require an accepting owner; already-admitted work may finish during draining.
7. Captured request execution and settings are the authority for dependencies and scheduling.
   Late reads of mutable app state or the settings cache cannot change that owner.
8. Provider construction occurs during explicit registration or actual provider resolution.
   Snapshot publication copies metadata and creates bound factory wrappers without invoking them.
9. Installation is publication, not a way to reopen a stopped runtime. Same-owner installation is
   a verified no-op; fresh publication remains coordinated by the matching startup token.

## Delivery sequence

| Step | Deliverable | Findings | Depends on |
|---|---|---|---|
| 0 | Failing regressions using real ASGI, lifespan, dependencies, and coordinator resources | F1–F7 | None |
| 1 | Retained operation scopes and request-owned worker completion | F1 | 0 |
| 2 | Strict lifecycle resolution, fixture ownership, and closed failed-startup generations | F2, F3 | 0–1 |
| 3 | Shared request settings resolver and normalized liveness classification | F4, F5 | 0, 2 |
| 4 | Constructor-free sealed connector publication | F6 | 0; integrate with 2–3 |
| 5 | Idempotent same-owner installation and stopped-admission rejection | F7 | 0–2, 4 |
| 6 | Compatibility checks, final repository gates, and reconciled records | F1–F7 | 1–5 |

Complete the two P1 boundaries first. Keep changes reviewable by boundary and preserve the
existing dirty worktree; do not remove earlier refactor work to make new fixtures simpler.

## Step 0: preserve the reproduced failures as regressions

Files: `tests/test_pipeline_lifecycle.py`, `tests/test_pipeline_tasks.py`,
`tests/test_solid_boundaries.py`, `tests/test_connector_registry.py`,
`tests/test_connector_runtime.py`, `tests/conftest.py`, and a focused request-ownership test
module if separating full-stack scenarios improves readability.

Use event barriers and bounded observer waits rather than timing guesses. In every teardown,
release blocked workers, await original handles, and restore app/coordinator/global settings and
database state. Diagnostic routes belong only to isolated test app instances.

| Finding | Required regression | Assertions |
|---|---|---|
| F1 | `test_cancelled_request_waits_for_owned_authoring_cleanup` | Through the real middleware stack, block an admitted authoring worker with an open owned session, cancel the request, begin lifespan shutdown, and prove lifetime non-idle/no disposal until session/context cleanup ends. Preserve cancellation. |
| F1 | `test_owned_worker_reserves_admission_before_thread_entry` | Cancel between task creation and thread entry. Work either never starts and releases its reservation, or remains retained until actual cleanup; it never runs under disposed resources. |
| F1 | `test_borrowed_scope_retains_until_last_child_finishes` | Parent completion cannot release the group's last lifetime ownership while a child is active. Nesting, concurrent close, stale copied contexts, and mismatched owners have deterministic behavior. |
| F2 | `test_unstarted_composed_app_declines_before_global_io` | With A active and B not started, real B data requests and direct request dependency resolution decline. Engine/session/provider spies show zero A/B I/O. Explicit fixture and accepting B are positive controls. |
| F3 | `test_failed_startup_closes_direct_admission` | Parameterize global/composed startup failure at validation, schema/role initialization, and supervisor startup. Ready is false, app and runtime are closed, direct catalog/authoring reject before session creation, and the startup exception remains primary. |
| F4 | `test_request_dependencies_keep_captured_generation_settings` | In global and composed apps, pause an admitted request, mutate/replace source settings, resume dependency/auth/error/deferred paths, and verify they all use the captured generation. |
| F5 | `test_mounted_health_remains_available_during_shutdown` | Parameterize root, Connect, Workbench direct/proxy, and external-base mounts across lifecycle states. Health returns 200; readiness returns 503 without opening sessions outside accepting state. |
| F6 | `test_snapshot_does_not_invoke_registered_factory` | A factory spy is called at registration, remains untouched during snapshot/runtime publication, and is called once on explicit provider resolution under captured settings/registry. |
| F7 | `test_stopped_same_owner_reinstall_is_rejected_without_mutation` | Use real resource facts. Parameterize closed lifetime and stop-event-only stopping, owner kinds, and supervisor handles. Rejection preserves all coordinator state and does not prevent valid retirement. |

Add error variants for provider/session-close failure, response-start/body send failure, disconnect,
repeated cancellation, and successful original response background cleanup. Inspect real resource
authority and cleanup order; signature mocks alone do not satisfy acceptance.

## Step 1: retain admission through owned worker completion (F1)

Files: `app/infrastructure/runtime.py`, `app/main.py`, catalog/authoring adapters,
`app/ui/routes/pipeline_save.py`, `pipeline_preview.py`, `pipeline_datasets.py`, `pipeline.py`,
`security.py`, and `pipeline_context.py`. Use a focused infrastructure helper module if needed.

1. Replace the borrowed scope's unretained lease reference with a shared admission group. The
   group owns one lifetime lease and tracks live scope handles/retentions under a lock. Closing
   a scope is idempotent; release the lifetime lease exactly once when all retained handles close.
2. Check handle liveness and increment retention atomically. A released handle cannot grant
   ownership even if copied into another task/thread. Existing live children can nest during
   draining; another runtime must obtain its own admission and a closed runtime rejects it.
3. Add one request-owned synchronous-work helper. Reserve a child handle before enqueueing,
   keep the original task handle, bind the captured runtime/settings/registry in the worker,
   and release the child in the worker's finalizer after session and context cleanup.
4. Await the original worker through shielding and cleanup draining. Handle task cancellation
   and AnyIO cancellation scopes without a busy loop; repeated cancellation cannot abandon the
   original handle. Propagate the original cancellation afterward and retain worker failures.
   Do not copy the supervisor helper's app-wide shutdown behavior into request cancellation.
5. Replace request `asyncio.to_thread` calls that use provider/session/runtime resources with the
   helper. Audit both value-only operation adapters and `with_user_session`, since the latter
   currently opens a session without its own retained operation scope. Pure presentation-only
   calls may stay unchanged if they do not own runtime resources; record the audit result.
6. Have the ASGI boundary retain and drain its registered request-owned work before its final
   admission release/unbinding, including error/cancellation paths. Invoke downstream once.
   Never retry provider effects and never wait for global runtime idle while owning a lease.
7. Keep Starlette/FastAPI background and dependency cleanup inside the completion boundary.
   Verify synchronous endpoints/dependencies and deferred outbox delivery through the real stack;
   adapt their ownership where framework cancellation can abandon runtime work.

Acceptance: request cancellation, send failure, and disconnect cannot make the runtime idle or
permit disposal while scheduled/running workers or owned cleanup remain. Happy-path nested work
does not add a separate lifetime counter, and cancelling one request leaves the app accepting.

## Step 2: make lifecycle and fixture ownership authoritative (F2, F3)

Files: `app/main.py`, `app/database.py`, `app/bootstrap.py`, runtime admission state,
catalog/authoring factory wiring, `tests/conftest.py`, and lifecycle/boundary tests.

1. Set `not_started` at construction for both app types. Initialize composition identity
   immediately, while preventing data requests until a successful lifespan publishes its owner.
   Health remains available and readiness does not create a session before startup.
2. Centralize lifecycle-aware request runtime resolution. Prefer a captured owner for admitted
   work. Reject uncaptured requests to not-started/starting/draining/closed apps before I/O.
   Do not infer fixture compatibility from `APP_ENV=test`, absent attributes, or readiness.
3. Restrict any compatibility path to explicitly scoped fixture mode. Establish an isolated
   fixture execution generation with captured session factory, registry, and settings; restore
   previous fixture/app state on teardown. Lifespan test clients still enter normal startup.
4. Make `_request_execution`, `get_db`, operation factories, and scheduling consume the same
   resolved/captured owner. In HTTP dependencies, remove late global factory fallbacks. CLI and
   settings-only worker adapters retain their explicit process compatibility entrypoints.
5. Put all startup preparation/publication, including supervisor startup, inside a failure
   boundary. On failure, set readiness false and call `begin_shutdown()` on every partial
   execution generation before publishing `closed`. Keep cleanup safe if runtime creation itself
   failed and no execution reference exists.
6. If startup scheduled work, close admission and drain those original handles before disposal.
   Abort only the matching startup token; release installed legacy ownership only after proven
   drain/cleanup. Keep failed-cleanup references closed and diagnosable for a coordinated retry.
7. Preserve the startup exception/cancellation as primary and retain cleanup failures as secondary
   evidence using the existing shutdown-outcome convention. Explicit restart publishes a fresh
   runtime rather than clearing the marker or reopening a stopped generation.

Acceptance: unstarted B never inherits A; failed startup leaves both app and runtime unavailable;
direct adapters reject before sessions; admitted B work finishes under B during drain; fixture,
global/composed lifespan, startup retry, and queued-run recovery behavior remain compatible.

## Step 3: use captured settings and normalized liveness (F4, F5)

Files: `app/dependencies.py`, `app/main.py`, `app/database.py`, settings-consuming route wiring,
`app/services/pipeline_tasks.py`, and request/security/Posit tests.

1. Introduce one request settings dependency used by `SettingsDep` for both app types. It returns
   the captured owner's execution settings and validates lifecycle ownership rather than calling
   the mutable settings cache for an ordinary HTTP request.
2. Resolve explicit fixture settings through its fixture owner. Keep `get_settings()` unchanged
   as the CLI/bootstrap configuration API; do not add a request parameter or ContextVar-dependent
   cache behavior to that process configuration loader.
3. Replace the composed-only override with the shared resolver, or wire both apps identically
   if maintaining the existing dependency identity is necessary. Preserve deliberate test
   overrides while preventing lifecycle checks from being bypassed by normal routing.
4. Audit direct `get_settings()` and `app.state.settings` lookups in middleware, authentication,
   exception handlers, cookie rotation, route policy, and deferred scheduling. Use request-captured
   settings throughout HTTP work; ensure frozen execution settings do not break legacy identity
   validation by forwarding the runtime through runtime-aware scheduling paths.
5. In ownership middleware, classify health/readiness with `get_route_path(scope)`, matching
   security dispatch. Use the normalized route path consistently for lifecycle gating.
6. Keep readiness database checks restricted to accepting state and account for any in-progress
   check so disposal cannot race it. Starting/draining/closed/not-started readiness returns 503
   without querying a disposed or global engine; health remains a data-free liveness endpoint.

Acceptance: all settings consumers under an admitted request use one captured generation after
source mutation/cache replacement. Health/readiness contracts hold under supported mount paths,
with request IDs, cookie paths, security headers, redirects, and error fragments preserved.

## Step 4: publish connector generations without construction (F6)

Files: `app/connectors/registry.py`, `app/infrastructure/runtime.py`, `app/bootstrap.py`, both
lifespans, and registry/runtime/composition tests.

1. Extract factory binding from `register()` into a helper that only creates a callable wrapper.
   Registration remains responsible for its one explicit construction, capability discovery,
   validation, and source revision update.
2. Snapshot the source specification tuple and captured settings. Copy validated immutable
   capabilities/specifications directly, retaining raw factory metadata and binding wrappers to
   the destination registry/settings. Do not call `register()` or any provider factory to copy.
3. Validate copied metadata against captured settings without provider construction. Preserve
   provider names, capability values, writer-setting declarations, raw-factory identity facts,
   and the existing revision/fact semantics needed by legacy compatibility validation.
4. Seal the completed registry before publication. `register`, `clear`, and reload refuse
   mutation before altering settings, ownership, specifications, or revisions. A rejected reload
   must leave the sealed generation untouched.
5. Capture a snapshot once per fresh runtime generation. Reuse a sealed generation only when
   its captured settings authority matches the runtime; otherwise reject or create a correctly
   rebound metadata snapshot without constructors. Avoid A's factories with B's execution settings.
6. Verify real provider resolution receives the captured settings and generation registry;
   source builder clear/register/reload and source settings mutation cannot alter the old runtime.

Acceptance: publication performs zero provider factory calls/network effects; normal registration
and explicit resolution have expected invocation counts; sealed mutation refuses atomically;
capabilities, extension writer flags, fake/live providers, and independent compositions still work.

## Step 5: make same-owner installation a checked no-op (F7)

Files: `app/infrastructure/runtime.py`, `app/services/pipeline_tasks.py`, startup publication,
and coordinator/lifecycle tests.

1. Expose a read-only, synchronized admission-state check through the existing lifetime. Include
   the stop-event state, since event-only stopping also prevents admission. The query must not
   acquire a lease merely to test acceptance.
2. Under the coordinator lock, validate reservation-token ownership and existing runtime identity
   before resource inspection or state mutation. Rejected publication preserves the token, owner
   kind, supervisor, generation, and captured facts.
3. For an identical installed owner, reject closed/stopped admission regardless of its kind.
   Compare current real resource facts with the original capture; missing or changed capture is
   not permission to refresh ownership. If valid and accepting, return without assignments or
   promoting `temporary` to `lifespan`.
4. For fresh publication, verify the proposed runtime is accepting and owns the current compatibility
   resources, then publish under the matching token. Any intended temporary-to-lifespan handoff
   must be explicit and coordinated; incidental reinstall must never perform that transition.
5. Coordinate admission closure/publication lock ordering so racing shutdown and install cannot
   reopen or misclassify a generation. Do not hold coordinator/lifetime locks across awaits or
   provider/session operations; wait for draining outside the coordinator lock.
6. Preserve retirement of stopped temporary owners and live-lifespan retirement rejection. A
   stopped/failed owner can be drained and released, followed by a fresh verified startup.

Acceptance: unchanged accepting reinstall is a true no-op; stopped/closed/changed-facts/different-
token attempts reject without mutation; supervisor identity survives; temporary retirement works;
fresh matching-token publication succeeds after drain; concurrency and cancellation do not steal
the reservation or leave a stopped owner falsely classified as live.

## Step 6: compatibility gates and acceptance records

1. Run each boundary's regressions while implementing that boundary. Verify the original failure
   first, then the corrected completion/resource observations; keep positive controls for preserved
   behavior. Run focused changed-path checks after the final code adjustment.
2. Run lifecycle/task/SOLID, connector registry/runtime, catalog/authoring/pipeline, auth/security,
   email-background, UI interaction, smoke/server, and Posit mount suites on the integrated tree.
3. Run `make check` and `make hedron-build`. Record exact passed/deselected counts, coverage against
   the existing 80% floor, demo tests, Ruff, formatting, BasedPyright, Hedron, Posit, and build digest.
   Preserve existing external provider/deployment qualification rules.
4. Run `tests/test_documentation.py`, validate local links in changed plan records, and run
   `git diff --check`. Remove only artifacts created by this implementation and preserve unrelated
   pre-existing untracked files and refactor edits.
5. Update this plan, the index, major SOLID status, and prior completion/isolation record together.
   Keep historical validation results explicitly historical; close F1–F7 only when each named
   boundary regression and the final gates pass on the final tree.

Definition of done: scheduled/running work and cleanup retain admission until completion;
unstarted/failed/closed apps never inherit globals; request configuration remains generation-owned;
mounted liveness works; connector publication is constructor-free and sealed; stopped reinstall
cannot change ownership; all seven regression targets and final compatibility gates pass.
