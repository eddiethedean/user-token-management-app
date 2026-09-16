# SOLID runtime review: completion and generation isolation

Status: earlier changes implemented; runtime acceptance reopened by F1–F7 in the
[retained work and explicit ownership plan](solid-runtime-retained-work-remediation.md).
The evidence below is historical and does not close those follow-on findings. This record follows review of the
[drain completion and owner publication implementation](solid-runtime-drain-publication-remediation.md).
Implemented on 2026-09-16. Subsequent review found retained-worker, lifecycle, settings, mounted
liveness, constructor, and installation gaps despite the repository gates below passing.

## Findings and acceptance targets

| ID | Priority | Verified failure | Required behavior |
|---|---|---|---|
| C1 | P1 | A request lease reports idle while original downstream background work is blocked. An ASGI send failure leaves the lease active indefinitely. | The lease covers the complete downstream ASGI invocation and releases exactly once on success, error, disconnect, or cancellation. |
| C2 | P1 | Closing composed app B clears its lifecycle marker; subsequent operation resolution creates an accepting global runtime for database A. | Closed and failed-cleanup apps reject data work before I/O and retain their generation identity. Fixture compatibility is explicit. |
| C3 | P2 | Calling startup preparation twice with no published runtime accepts both calls and replaces the first reservation token. | Reservation ownership is checked before every ownerless or retired-owner branch; a competing attempt cannot replace or release another token. |
| C4 | P2 | Clearing the composed source registry changes its execution runtime's provider count from four to zero. | Every execution generation owns sealed provider specifications/factories and captured settings, independent of mutable composition builders. |
| C5 | P2 | A direct catalog runner executes a query after its runtime has begun shutdown while the lifetime reports idle. Authoring has the same unaccounted session boundary. | Direct operations obtain admission before sessions or provider work; operations under an existing admission borrow that same owner's live scope. |
| C6 | P2 | Changed registry facts are initially rejected, but reinstalling the same owner overwrites captured facts and makes compatibility validation succeed. | Same-owner installation verifies original facts without refreshing them or changing the supervisor handle, generation, or closed status. |

Review evidence: 42 focused task/lifecycle/SOLID tests passed and `git diff --check` passed; separate
probes reproduced all six findings. The implementation below closes those probes with generation-owned
admission, lifecycle, and compatibility boundaries.

Scope: request completion/admission, lifecycle ownership, startup reservation validation, sealed
connector generations, direct catalog/authoring operation boundaries, same-owner installation,
fixtures, and remediation records. Preserve the earlier unconditional drain, standalone cancellation
propagation, stable session factories, structured URLs, settings-only worker APIs, independent app
instances, queued-run recovery, shutdown error precedence, schema, provider contracts, UI fragments,
and credential policy. No deployment, schema migration, broad route decomposition, or provider
network qualification is included.

## Ownership and completion contracts

1. An admitted request finishes only when the original downstream ASGI invocation finishes,
   including response streaming, original background tasks, dependency/session cleanup, and owned
   task cleanup. A response object or final body message alone does not prove completion.
2. Every acquired lease has one completion finalizer. Transport failure and cancellation cannot
   leak admission or permit disposal while owned work is still running.
3. App states distinguish not-started, starting, accepting, draining, and closed. Cleanup failure
   leaves a closed, diagnosable generation. Removing an app-state reference never enables globals.
4. Runtime selection is captured before dependencies execute. Already-admitted work may finish
   during draining under its original owner; new work declines before sessions or provider I/O.
5. A reservation token has one owner. Prepare, publish, and abort validate it under the coordinator
   lock; completion waits occur outside that lock.
6. Published registry specifications are immutable. Factories bind the generation registry and
   frozen execution settings; source builder mutations cannot change that generation.
7. Catalog/authoring operations are accounted for whether called through HTTP or directly. Borrowed
   admission must belong to the same runtime and remain live; copied/stale context is not admission.
8. Same-owner installation is idempotent only when captured resource facts remain identical. It
   cannot refresh stale facts, reopen admission, or replace the retained supervisor handle.

## Implementation sequence

| Step | Deliverable | Findings | Dependency |
|---|---|---|---|
| 0 | Regressions at actual ASGI, lifespan, composition, and operation boundaries | C1–C6 | None |
| 1 | Token ownership checks and immutable same-owner installation | C3, C6 | 0 |
| 2 | Sealed registries for every execution generation | C4 | 0–1 |
| 3 | General operation scopes and direct operation admission | C5 | 0, 2 |
| 4 | Pure ASGI ownership boundary and explicit lifecycle/fixture modes | C1, C2 | 1–3 |
| 5 | Final compatibility gates and reconciled records | C1–C6 | 1–4 |

Implement and validate each boundary before combining the changes. Preserve unrelated worktree
changes; do not remove existing refactor work to simplify fixtures.

## Step 0: failing regressions and isolated fixtures

Files: `tests/test_pipeline_lifecycle.py`, `tests/test_pipeline_tasks.py`,
`tests/test_solid_boundaries.py`, `tests/test_user_catalog.py`, `tests/test_pipelines.py`, affected
UI suites, and `tests/conftest.py`. Add a dedicated request-runtime test module if it improves clarity.

- C1: `test_request_drain_waits_for_original_background_cleanup`. Use the actual application ASGI
  stack and an original response background task blocked after opening an owner-scoped session.
  Begin shutdown after the final response body is sent. Assert the lifetime is non-idle and no
  disposal occurs until background work, its session, and context cleanup finish.
- C1: `test_request_send_failure_releases_admission` and
  `test_request_cancellation_drains_before_release`. Fail transport sends at response start/body,
  disconnect during streaming, and cancel while downstream work is active. Assert eventual lease
  release, no early disposal, original failure/cancellation retention, and no duplicate cleanup.
- C2: `test_closed_composed_app_never_resolves_global_owner`, parameterized for successful shutdown,
  drain failure, and disposal failure. Request catalog/authoring work through app B's real stack;
  use engine/session/provider spies to prove no I/O against A or B after closure. Include duplicate
  user IDs across A/B, an ID only in B, and an already-admitted B request resumed during draining.
- C3: `test_competing_startup_preserves_reservation`, parameterized for no previous owner and an
  old owner already drained. Pause the first actual startup before publication; attempt a second
  preparation, a different-token installer, and stale-token abort. Assert the first token remains
  current and only its matching publication or completed abort can release the slot.
- C4: `test_composed_generation_survives_source_registry_changes`. Capture a composed runtime,
  then clear/register/replace/reload its source builder and mutate source settings. Inspect actual
  provider instances and capabilities, not just provider counts. Assert the old generation remains
  unchanged and attempts to mutate its sealed registry fail.
- C5: `test_direct_operation_is_rejected_after_runtime_shutdown`, parameterized for catalog and
  authoring. Assert rejection before session creation/query/provider calls. In a second scenario,
  block a legitimately admitted direct operation and prove shutdown waits through its session
  cleanup. Test nesting, mismatched owner scopes, and a stale copied context.
- C6: `test_same_owner_reinstall_rejects_changed_resource_facts`. Mutate source registry settings,
  specifications, session factory/options, and engine binding after installation. Assert reinstall
  rejects without changing original facts, owner kind, token, or supervisor. Positive controls cover
  unchanged reinstall and matching-token publication of a fresh generation after a completed drain.

Use event barriers and bounded observer waits. Always release blocked work and await original
handles in teardown. Restore global engine/factory, settings cache, registry, lifecycle state, and
coordinator/token. Tests using direct Hedron adapters must explicitly select an isolated fixture
owner; shared state left by an earlier lifespan is not fixture-mode authorization.

## Step 1: protect reservation and installation facts (C3, C6)

Files: `app/services/pipeline_tasks.py`, startup in `app/main.py`, coordinator tests.

1. Move the reservation check before the `runtime is None` branch in `begin_legacy_startup`.
   Reserve ownerless startup only when no token is already held. Keep the same token after old
   runtime retirement until publication or validated abort.
2. Make prepare/publish/abort ownership checks precede resource preparation or state mutation.
   A different-token attempt must not reset owner kind, supervisor, or reservation state.
3. Capture proposed facts at the coordinated publication boundary. Validate against current
   compatibility resources under the same lock, then publish owner/facts/token completion atomically.
   Supported source resource publication must not race that validation.
4. For an existing identical runtime, compare current facts against its original captured facts.
   Reject changes without overwriting facts. Preserve the exact supervisor handle and owner kind;
   reject closed/stopped reinstall rather than marking it accepting or lifespan-owned again.
5. Only a fresh, verified generation under the matching token can replace retired resources.
   Keep the public retirement wrapper separate from startup publication and preserve cancellation
   and cleanup error behavior from the preceding remediation.

Acceptance: competing prepare on ownerless/retired/busy coordinators; stale/different-token publish
and abort; unchanged reinstall; every changed-facts variant; failure/cancellation followed by a
clean retry; no token stolen and no supervisor/facts silently replaced.

## Step 2: seal every execution registry (C4)

Files: `app/connectors/registry.py`, `app/infrastructure/runtime.py`, `app/bootstrap.py`, both
lifespans in `app/main.py`, registry/composition tests.

1. Separate mutable registry building from published generation authority. Add a sealed snapshot
   whose public `clear`, `register`, and reload paths refuse mutation. Preserve revision/fact APIs
   on source builders for compatibility validation.
2. Build snapshot factories from retained raw factory metadata, binding them to the captured
   generation registry and frozen settings. Copy immutable provider capabilities/specifications;
   avoid copying bound closures that still capture source registry/settings. Do not re-run provider
   constructors merely to snapshot already-validated metadata or perform network I/O at publication.
3. Capture the sealed snapshot consistently at the execution-runtime construction boundary so
   global, legacy, bootstrap, and composed lifespan generations all receive it. Avoid repeated
   snapshotting or generation-local factories accidentally rebound to a builder.
4. Keep `ApplicationComposition.connectors` explicitly a builder or explicitly published authority;
   update identity-based tests to assert execution ownership and provider behavior. Request binding
   uses `execution.connectors`, never the mutable builder.
5. Reload prepares a new builder privately and becomes effective only through fresh generation
   publication after old admission closes and drains. Old runtime references remain closed or
   continue under their immutable authority if already admitted.

Acceptance: source clear/add/remove/replace/reload, snapshot mutation refusal, frozen factory
settings/capabilities, unchanged reuse, no publication-time factory side effects, independent A/B
registries, and original fake/live capability and writer-flag behavior.

## Step 3: account for direct and nested operations (C5)

Files: `app/infrastructure/runtime.py`, `app/infrastructure/catalog_factory.py`,
`app/infrastructure/pipeline_authoring.py`, operation factory wiring, operation tests.

1. Expose a narrow execution-operation scope using the existing lifetime. Preserve pipeline
   admission entrypoints as compatibility wrappers rather than create separate drain counters.
2. A direct operation obtains its own lease before context binding/session creation, and releases
   after provider, session, and context cleanup in a finalizer. Decline closed runtime work using
   the established unavailable-operation convention before any I/O.
3. An operation inside a live scope for the same owner may borrow that admission. This lets an
   already-admitted request resolve catalog/authoring after shutdown begins without requesting new
   admission. Only the outer scope releases its lease.
4. Track scope identity/liveness explicitly. A different runtime, released scope, or copied context
   retained past outer completion cannot borrow admission; a closed owner then rejects it. Keep
   transport objects out of the value-only catalog/authoring ports.
5. Wrap both catalog and authoring adapters in this operation scope. Verify nested callbacks,
   lookup/validation exceptions, provider errors, and session-close errors do not leak leases or
   replace the original error unnecessarily.

Acceptance: direct rejection after closure; drain waits for admitted direct work; nested work
finishes under the same owner during draining; stale/mismatched borrowing refuses; all cleanup
paths release exactly once; no self-await or deadlock with background pipeline leases.

## Step 4: complete requests at the ASGI boundary (C1, C2)

Files: `app/main.py`, a focused ASGI middleware module if appropriate, `app/database.py`, request
settings/factory wiring, `tests/conftest.py`, ASGI/lifespan/UI tests.

1. Introduce pure ASGI ownership middleware. Handle HTTP requests and pass lifespan/WebSocket
   scopes through. Capture the runtime, frozen execution settings, session factory, sealed registry,
   and operation scope before routing/dependencies; bind them for the entire downstream invocation.
2. Await the original downstream ASGI application once and release/unbind in its completion
   finalizer. Remove the replacement response-background release callback. Do not use response
   headers, final body messages, or the wrapper's `background` attribute as completion proof.
3. Ensure middleware ordering places ownership outside the downstream security/session/routing
   stack while preserving security headers, cookies, request IDs, budgets, mounted paths, and error
   rendering. Security dispatch consumes the captured owner rather than selecting by readiness.
4. Verify transport exception, cancellation, and disconnect do not abandon owned synchronous work.
   Retain/await original task handles where needed before lease release; never retry a downstream
   invocation or provider side effect to obtain completion.
5. Keep lifecycle state closed after successful or failed shutdown. Retain the closed runtime or
   a diagnostic owner reference as needed for cleanup retry. Startup failures also leave closed
   state; readiness false remains an availability signal, not a global-fallback selector.
6. Make not-started fixture compatibility explicit through a scoped fixture-owner helper or
   composition mode. Update direct `page`/`htmx` fixtures to establish/reset that isolated owner;
   preserve tests that genuinely need lifespan startup by letting them enter lifespan normally.
   Never treat closed/draining state or `APP_ENV=test` alone as fixture compatibility.
7. Resolve `_request_execution`, database dependencies, settings, catalog/authoring factories, and
   scheduling from the request's captured owner. No late lookup of mutable app state can switch an
   in-flight request to a newer generation or globals. A direct composed client has its composed
   owner or rejects until startup; it cannot inherit A.
8. New data requests to starting/draining/closed apps decline before I/O. Already-admitted requests
   finish under their original scope. Health stays available and readiness returns unavailable
   during drain/closure without opening sessions. Restart creates a fresh generation explicitly.

Acceptance: original background completion, streamed body/send failures, disconnect/cancellation,
session/context cleanup, both lifespans and failed cleanup, late operation resolution under B,
A remains active, explicit direct fixtures, restarted generations, and existing UI/security/Posit
mount contracts. Inspect actual engine/settings/provider authority in A/B tests.

## Step 5: final verification and records

1. Record a named passing regression for every finding and its actual completion/ownership
   observations. Include C1's early-release and leaked-lease scenarios separately. Passing manual
   lease tests or signature mocks alone cannot close ASGI/operation acceptance.
2. Run focused task/lifecycle/SOLID, registry, catalog, pipeline, authoring, email-background, and
   affected UI/security suites. Run changed-path validation again after any final code adjustment.
3. Run `make check` and `make hedron-build` on the final tree. Record counts, deselections, coverage
   with the existing 80% floor, demo tests, Ruff, formatting, BasedPyright, Hedron, Posit, and digest.
   External provider/deployment tests keep their existing qualification rules.
4. Run `tests/test_documentation.py`, validate local links in changed plan records, and run
   `git diff --check`. Remove only artifacts created by this work; preserve unrelated changes.
5. Reconcile this record, the plan index, major refactor status, and prior runtime remediation
   records. Preserve historical passes while making C1–C6 the active acceptance authority. Mark
   implemented only after named boundary regressions and final gates pass.

Definition of done: request/background/direct-operation completion is accounted for on every exit;
closed apps cannot access another owner's resources; reservation tokens cannot be stolen; all
runtime registries are sealed; same-owner reinstall cannot refresh changed facts; compatibility
checks and final repository gates pass.


## Implementation evidence

Implemented on 2026-09-16:

- C1: `RuntimeOwnershipMiddleware` now owns one operation scope around the complete downstream ASGI call. The lease is released from the ASGI finalizer on success, transport error, or cancellation; response background replacement was removed.
- C2: accepting/draining requests resolve the captured execution generation, closed apps retain a `closed` lifecycle and reject data work, and fixture compatibility is explicitly marked as `fixture` in `tests/conftest.py`.
- C3: startup reservation checks `_LEGACY_TRANSITIONING` before ownerless branches, so a second reservation cannot replace the first token.
- C4: `ExecutionRuntime` snapshots mutable connector builders into sealed generations with frozen settings; composition identity tests verify source clearing does not alter execution providers.
- C5: catalog and pipeline-authoring adapters admit before binding context or opening sessions, and nested calls borrow the same live scope.
- C6: same-owner installation compares current resource facts with the original capture and rejects changed facts without refreshing ownership state.

Validation:

- `make check`: 447 passed, 31 deselected, 84.37% coverage; Ruff, formatting, BasedPyright, Hedron, Posit, and 25 demo tests passed.
- `make hedron-build`: digest `c0123a9179acac33da4470eaec37879f5df3a884f43e67faa4d512c78670a929`.
- `tests/test_documentation.py` and `git diff --check` passed.
- An ASGI transport-failure probe confirmed the runtime lifetime returns idle after `send` raises.

This implementation record remains historical evidence. The
[retained work and explicit ownership plan](solid-runtime-retained-work-remediation.md) is the active
acceptance authority for F1–F7; the historical full gate does not cover those reproduced failures.
