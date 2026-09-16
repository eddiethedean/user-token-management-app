# SOLID runtime review: admission, cancellation, and validated publication

Status: earlier changes implemented; W1–W5 follow-on runtime remediation is closed in the
[worker completion, publication, and captured scheduling plan](solid-runtime-worker-publication-remediation.md).
This record follows review of the
[retained work implementation](solid-runtime-retained-work-remediation.md).
Implemented on 2026-09-16. Subsequent review reproduced terminal worker cancellation,
proposed-resource validation, publication/shutdown ordering, submission cleanup, and captured
scheduling gaps; the linked W1–W5 remediation closes them.

## Scope and review evidence

Fix the seven reproduced findings without changing routes, database schema, provider protocols,
queued-run recovery, CLI configuration, or shutdown exception precedence. Preserve the existing
dirty worktree and the earlier refactor changes. No deployment or migration is required.

The review ran 52 lifecycle/task/SOLID/connector tests successfully and passed `git diff --check`.
The probes below nevertheless reproduced seven gaps. They are now covered by permanent boundary
regressions. The final integrated gate passed 453 tests with 31 deselected at 83.95% coverage,
alongside 25 demo tests and build digest
`c0123a9179acac33da4470eaec37879f5df3a884f43e67faa4d512c78670a929`.

| ID | Priority | Verified gap | Required outcome |
|---|---|---|---|
| R1 | P1 | A blocked readiness query leaves the lifetime idle; draining completes while the query runs. | Readiness owns admission before opening its session and through cleanup; disposal waits for it. |
| R2 | P2 | A worker failing after request cancellation returns `ValueError` rather than `CancelledError`. | Cancellation remains primary after worker completion, with worker/cleanup errors retained as secondary evidence. |
| R3 | P2 | AnyIO level cancellation causes 9,581 shield calls during a 150 ms worker wait. | Cleanup waits inside an appropriate cancellation shield without retrying awaits in a busy loop. |
| R4 | P2 | A retained child cannot nest during shutdown after its parent closes. | Each scope binds its own live handle; retained children can finish nested work while draining. |
| R5 | P2 | Changing source settings to `app_env=test` selects mutable request configuration while the runtime retains the original values. | Every normal accepting request uses its runtime's execution settings; compatibility requires explicit fixture ownership or test overrides. |
| R6 | P2 | Publishing a permissive registry accepts an extension declaring `nonexistent_flag` as its writer setting. | Snapshot publication validates copied metadata against captured settings before sealing, without calling provider factories. |
| R7 | P2 | Fresh publication accepts a stopped runtime and classifies it as a live lifespan owner. | All publication paths validate admission and resource authority before changing coordinator state. |

Positive controls from review: cancelling a response with an original synchronous background task
kept its request admission active while that task ran. Preserve that behavior. Constructor-free
snapshot copying and same-owner stopped-runtime rejection also remain part of the prior contract.

## Implementation and acceptance evidence

The earlier remediation changes are implemented. `RuntimeOwnershipMiddleware` admits readiness
through the database check and rejects it during drain; regular accepting requests bind the
runtime's frozen settings. Borrowed operation scopes bind their own retained lease, and
`run_owned_sync` drains its worker under an AnyIO cancellation shield before propagating caller
cancellation. Registry snapshots validate copied metadata before sealing, and legacy publication
rejects proposals already stopped at its initial state check. The linked worker-completion and
publication remediation closes the additional W1–W5 cases.

The focused lifecycle/task/SOLID suites passed 50 tests. The final `make check` passed Ruff,
formatting, BasedPyright, Hedron and Posit checks, 453 application tests (31 deselected), the 80%
coverage requirement at 83.95%, and 25 demo tests. `make hedron-build`, documentation tests,
local-link validation, and `git diff --check` also passed.

## Delivery order

| Step | Deliverable | Findings | Dependencies |
|---|---|---|---|
| 0 | Permanent failing regressions and coordinator/app teardown fixtures | R1–R7 | None |
| 1 | Correct scope binding and synchronized child retention | R4 | 0 |
| 2 | Cancellation-safe worker outcome collection under asyncio and AnyIO | R2, R3 | 0, 1 |
| 3 | Admission-owned readiness checks for both app types and every supported mount | R1 | 0–2 |
| 4 | Captured request configuration with explicit test compatibility | R5 | 0, 2–3 |
| 5 | Validated metadata publication without constructors | R6 | 0, 4 |
| 6 | Validated fresh and same-owner publication | R7 | 0–1, 5 |
| 7 | Integrated compatibility gates and reconciled acceptance records | R1–R7 | 1–6 |

The P1 readiness fix follows the scope/cancellation primitives it needs. Keep each boundary's code,
tests, and evidence together so failures can be traced to the responsible contract.

## Step 0: preserve the failures as boundary regressions

Use `tests/test_pipeline_lifecycle.py`, `tests/test_pipeline_tasks.py`,
`tests/test_solid_boundaries.py`, connector registry/runtime tests, and a dedicated request-runtime
test module if full-stack cases would otherwise obscure unit-level admission checks.

Use worker-entry, session-entry, cleanup-entry, and release events. Bound observer waits and always
release workers in teardown, even when assertions fail. Await original handles before disposing
test databases. Restore every coordinator field, app state, dependency override, and settings
mutation. Diagnostic routes must be registered only on isolated apps.

| Regression | Boundary assertions |
|---|---|
| `test_readiness_drain_waits_for_session_cleanup` | Block the actual `/ready` query or session close through the full stack; lifetime is non-idle and lifespan disposal waits until cleanup completes. Parameterize global/composed apps. |
| `test_cancelled_owned_worker_failure_preserves_cancellation` | Cancel after worker entry, then fail the worker or session cleanup; await the original handle and receive cancellation with the secondary failure attached. |
| `test_anyio_cancelled_owned_worker_wait_does_not_spin` | Cancel an enclosing AnyIO scope while a worker is blocked; verify bounded drain-await attempts and normal progress of an unrelated heartbeat until release. Avoid wall-time-only CPU assertions. |
| `test_retained_child_can_nest_after_parent_closes` | Capture the child's context, close the parent, begin shutdown, and nest from the child; new unrelated work is denied, but retained child work succeeds. |
| `test_source_test_env_cannot_switch_request_settings_authority` | Publish a development runtime, mutate source `app_env` and settings, and make a normal request; middleware, dependency, and worker observe the original captured values. |
| `test_snapshot_validates_metadata_without_factory_calls` | Register invalid metadata in a permissive registry, then snapshot into owned settings; reject without additional factory calls or source mutation. Include valid extension positive controls. |
| `test_fresh_stopped_runtime_publication_is_rejected` | Propose an uninstalled stopped runtime using real compatibility facts; reject without modifying reservation, owner kind, generation, supervisor, or captured facts. |

Run each regression on the current tree first and record its failure. Use real lifespan, session,
registry, and coordinator resources where authority is part of the finding. Signature-only mocks
must not substitute for resource-ownership assertions.

## Step 1: bind the retained child as the active scope (R4)

Files: `app/infrastructure/runtime.py` and scope/lifecycle tests.

1. In the borrowing branch, retain the parent's lease and create the child, then set
   `_ACTIVE_OPERATION_SCOPE` to that child for the entire context-manager body. Reset the token
   and close the child in `finally`, matching the root-scope branch.
2. Preserve atomic released-handle checks and retention under the existing lifetime condition.
   Audit `RuntimeOperationScope.active` and lease release so concurrent close/retain cannot grant
   a released handle or decrement admission twice. Remove obsolete ownership flags if every
   scope now owns a separately releasable lease.
3. Closing a parent invalidates that parent handle only. Already-retained children retain their
   own handles, can create grandchildren during draining, and keep the same lifetime non-idle.
   Stale copied parent contexts cannot borrow its released lease.
4. A different runtime must obtain its own admission. Do not let a child handle authorize work
   against another generation or reopen a stopped runtime for unrelated callers.
5. Verify context restoration on success, exceptions, worker context cleanup, and nested scopes.

Acceptance: parent closure cannot break live child nesting; final child cleanup releases exactly
once; stale handles do not retain; mismatched and new draining-runtime work is rejected.

## Step 2: collect worker completion without losing cancellation or spinning (R2, R3)

Files: `app/infrastructure/runtime.py`, `app/ui/routes/pipeline_context.py`, and request-worker tests.

1. Keep the child admission reserved before scheduling and retain the original task handle.
   Move task creation inside the protected cleanup boundary so scheduling failure also releases
   the reservation. Bind the child's runtime, registry, and captured settings in the worker.
2. Separate three outcomes explicitly: caller cancellation, worker result/failure, and cleanup
   failure. The drain path must collect any `BaseException` raised while waiting for the worker;
   a sibling `except` on the initial await cannot catch failures inside a cancellation handler.
3. Shield the completion wait from an enclosing AnyIO cancellation scope, as well as shielding
   the original asyncio worker task. Use a cancellation-shielded drain phase appropriate to the
   existing asyncio/AnyIO stack; keep standalone asyncio callers supported. Do not repeatedly
   await inside an already-cancelled AnyIO scope.
4. Handle repeated direct `Task.cancel()` without cancelling or abandoning the original worker.
   Distinguish caller cancellation from an independently cancelled worker handle and define the
   completion outcome deterministically when worker termination and caller cancellation race.
5. After owned cleanup, propagate caller cancellation as primary. Chain or attach worker and
   cleanup failures using the existing secondary-error convention. With no caller cancellation,
   propagate the worker failure normally. Never call `begin_shutdown()` for one request's cancel.
6. Keep admission held through worker/session/context cleanup. Confirm the worker completion
   handle represents all of that cleanup before the request releases its retained child.
7. Test successful results, worker failure, session-close failure, cancellation-before-thread
   entry, repeated cancellation, cancellation plus failure, and AnyIO level cancellation. Preserve
   existing send-failure, disconnect, original background-task, and full-response behavior.

Acceptance: cancelled callers receive cancellation after cleanup; secondary failures remain
inspectable; the app stays accepting; blocked cleanup does not spin or permit early disposal.

## Step 3: make readiness database work admission-owned (R1)

Files: `app/main.py`, readiness session wiring, and lifecycle/Posit tests.

1. Separate data-free health classification from readiness classification using the normalized
   route path. `/health` stays available without admission or database access in every lifecycle.
2. Permit a readiness database probe only for a ready, accepting owner. In not-started, starting,
   draining, and closed states, return 503 before session creation. Fixture readiness, if needed,
   must be explicit and must not infer production ownership from `APP_ENV`.
3. For accepting readiness, obtain admission before opening the session and retain it through
   query, session close, and context cleanup. Prefer a shared async readiness adapter using the
   corrected owned-worker helper, or an equivalent full-response admission boundary. Avoid an
   untracked synchronous endpoint thread.
4. Resolve sessions from the captured request execution runtime in both app types. Remove the
   global readiness handler's late `SessionLocal` fallback and composed readiness's reliance on
   late mutable app database selection for admitted work.
5. Account for shutdown starting after readiness passes its initial state check. Admission either
   wins before shutdown and drains normally, or admission loses and readiness returns 503 before
   I/O. Lifecycle flags alone do not make this check atomic.
6. Parameterize root, Connect, Workbench, and external-base mounts. Block query and session-close
   phases separately; verify disposal waits. Test readiness cancellation and database failures,
   while preserving health responses and security headers.

Acceptance: readiness cannot report lifetime idle while using owned resources; shutdown waits for
admitted readiness cleanup; all non-accepting readiness requests perform zero database I/O.

## Step 4: remove the mutable environment shortcut from request settings (R5)

Files: `app/main.py`, `app/dependencies.py`, `tests/conftest.py`, and settings-consuming tests.

1. For normal accepting requests in both app types, always bind
   `execution.execution_settings`. Remove the `state_settings.app_env == 'test'` shortcut.
   A changed source environment must never select a different request configuration authority.
2. Keep explicit fixture mode isolated and make its settings/runtime authority consistent. Do not
   use one settings object in dependencies and another in worker context. Define fixture helpers
   that configure the intended generation before requests begin.
3. Update tests that currently mutate `get_settings()` after lifespan startup. Supply their rate
   limits, authentication mode, and writer flags before generation capture, create a fresh runtime
   for a new configuration, or use deliberate dependency overrides where the test targets only
   presentation. Do not preserve those assumptions with a production-routing bypass.
4. Remove the obsolete composed-only `get_settings` override if the shared request dependency
   makes it unused. Keep CLI/bootstrap `get_settings()` and deliberate test override support.
5. Verify dependency, authentication, CSRF, cookie rotation, exception rendering, provider policy,
   runtime context, and deferred scheduling consume the same captured generation. Runtime-aware
   scheduling should forward the runtime without re-resolving mutable settings identities.
6. Test original test-environment apps and source mutation from development to test, settings-cache
   replacement, app-state replacement, global/composed lifespans, and independent A/B apps.

Acceptance: configuration changes require an explicit fresh generation or deliberate test override;
normal dependencies and workers cannot disagree about settings after publication.

## Step 5: validate copied connector metadata before sealing (R6)

Files: `app/connectors/registry.py`, `app/infrastructure/runtime.py`, and connector tests.

1. Preserve the constructor-free snapshot path. Capture the specification tuple and settings,
   copy immutable capability metadata, and create bound factory wrappers without calling them.
2. Before sealing or returning an owned snapshot, call the existing metadata validator against
   the captured settings. This is required even when the source registry was permissive or its
   original settings were different. Constructor-free copying must not skip validation.
3. Validate missing, empty, non-boolean, and incorrectly typed writer-setting declarations.
   Preserve supported declarations and intentional `writer_setting=None` behavior.
4. On validation failure, discard the unpublished destination and leave source settings,
   specifications, revision, ownership, and factory facts untouched. Do not publish partial state.
5. Verify sealed-registry reuse and fresh runtime creation also enforce matching settings authority
   and validated metadata. Keep reload rejection atomic and factory calls limited to explicit
   registration or actual provider resolution.

Acceptance: invalid extensions reject at publication without constructor/network effects; valid
extensions, fake/live providers, source independence, and legacy factory identity facts still work.

## Step 6: validate every coordinator publication before mutation (R7)

Files: `app/services/pipeline_tasks.py`, synchronized runtime admission queries, and coordinator tests.

1. Apply the accepting-state check to fresh proposals as well as identical installed owners.
   Check both closed lifetime admission and a set stop event. Reject before assigning the owner,
   kind, supervisor, facts, or completing a reservation.
2. Validate transition-token ownership and existing runtime identity before resource inspection.
   Delay ownerless-state normalization until publication is known to be valid; even rejected
   proposals must preserve the coordinator's complete prior state.
3. Validate fresh proposals against real compatibility database, session, registry, and settings
   facts. For same-owner no-ops, require the original fact capture to exist and match; missing
   capture is an error, not permission to refresh or promote ownership.
4. Define a consistent coordinator/lifetime lock order and publication linearization point. A
   concurrent `begin_shutdown()` cannot allow an already-stopped proposal to become falsely live.
   Hold no lock across awaits, provider construction, or session operations.
5. Preserve true no-op installation for accepting identical owners and coordinated matching-token
   fresh startup. Do not promote temporary owners through incidental reinstall.
6. Parameterize event-only stopping, closed lifetime, fresh/same owners, valid/invalid tokens,
   live supervisors, changed/missing facts, and shutdown/publication races. Snapshot all coordinator
   fields before rejected attempts and assert equality afterward; then prove valid retirement and
   fresh startup still work.

Acceptance: stopped proposals never publish; rejection preserves reservations and owner facts;
temporary retirement succeeds; live lifespan retirement remains protected; valid startup publishes
one accepting verified generation.

## Step 7: final compatibility gates and acceptance records

1. Run every named boundary regression after its fix and again on the integrated final tree.
   Record observed completion/admission/disposal ordering, not merely HTTP status codes.
2. Run focused lifecycle/task/SOLID, connector, readiness/Posit, authoring/catalog/pipeline,
   authentication/rate-limit/federated, email-background, and response-cleanup coverage.
3. Run `make check` and `make hedron-build`. Record final passed/deselected counts, coverage against
   the existing 80% floor, demo results, Ruff/formatting, BasedPyright, Hedron/Posit results, and build
   digest. Re-run affected gates when subsequent code changes invalidate their evidence.
4. Run documentation tests, local-link validation for changed records, and `git diff --check`.
   Preserve pre-existing artifacts and remove only new test/probe artifacts attributable to this work.
5. Update this plan, the plan index, major SOLID status, and prior retained-work record together.
   Mark R1–R7 closed only after their permanent regressions and the final gates pass. Keep earlier
   results historical and leave the broader unfinished SOLID migration explicitly unfinished.

Definition of done: readiness cleanup participates in admission; request cancellation remains primary
without an AnyIO busy loop; live children nest under their own contexts; normal requests and workers
share captured settings; connector metadata is validated without constructors; every publication
rejects stopped proposals atomically; all seven permanent regression targets and final gates pass.
