# SOLID review: six-issue remediation plan

Status: S1–S6 scope implemented; subsequent runtime acceptance reopened by the
[completion and generation isolation plan](solid-runtime-completion-isolation-remediation.md).
Historical runtime work is recorded by the runtime/lifecycle implementation and
[failure handling and legacy ownership plan](solid-runtime-failure-remediation.md).
This document preserves the six-issue scope and evidence; all fixes
must retain their compatibility checks.

## Scope and reproduced evidence

| ID | Priority | Reproduced failure | Required result |
|---|---|---|---|
| S1 | P1 | A settings-only recovery call inside runtime B opened B's database and used B's registry/settings while receiving A's settings and locking A's database. | Every accepted call executes under one complete owner; legacy calls explicitly resolve the legacy owner or reject before I/O. |
| S2 | P1 | Cancelling lifespan shutdown caused composition disposal while the supervisor's worker thread remained active. Cancelling the supervisor alone left its stop event unset. | Signal shutdown and drain the actual database-using work before disposal; cancellation remains observable afterward. |
| S3 | P2 | A catalog runner constructed with runtime A and settings B used A's database/registry with B's cache/credential settings. | The typed runtime is the runner's sole settings source. |
| S4 | P2 | A valid locator mutated to contain an invalid table name passed validation, resolved credentials, and invoked the connector. | Catalog validation revalidates model contents and rejects malformed instances before credentials, factories, or remote operations. |
| S5 | P2 | A direct authoring command using `POSTGRES` and `UPSERT` skipped schema inspection and persisted a non-unique conflict column. | Every branch and persistence argument uses the normalized, policy-validated provider and write mode. |
| S6 | P2 | A missing or unowned pipeline ID reached destination schema inspection before the persistence service rejected it. | Effective pipeline/upload references are authorized before catalog construction, credential use, or remote validation. |

The latest review passed 67 focused tests across `test_solid_boundaries.py`, `test_user_catalog.py`,
`test_pipeline_tasks.py`, and `test_pipelines.py`. Separate probes reproduced all six failures.
The preceding implementation's 421 passing tests, 31 deselections, 84.51% coverage, 25 demo
tests, and successful build remain historical compatibility evidence, not acceptance for this plan.

Preserve database schema, provider IDs, configuration aliases, locator/policy/snapshot JSON,
legacy class import identity, routes, form names, CSRF, redirects, OOB regions, ownership messages,
cache policy, credential secrecy, and lease fencing. Retain existing valid-operation credential/cache
commits and the final save commit. Rejected ownership requests must produce no credential/catalog
side effects. Broader identity migration, provider strategy extraction, presentation decomposition,
and live-provider/deployment qualification remain outside this fix pass.

## Delivery order

| Step | Deliverable | Dependencies | Gate |
|---|---|---|---|
| 0 | Turn the six diagnostic probes into failing regressions | None | Each failure is observed through its real entrypoint, not just internal attributes. |
| 1 | Explicit legacy runtime resolution and canonical execution (S1) | 0 | Legacy/owned calls cannot inherit or mix another app's resources. |
| 2 | Cancellation-safe supervision and lifecycle draining (S2) | 1 | Disposal follows real worker and lease-session completion in both lifespans. |
| 3 | Runtime-only catalog runner construction (S3) | 0 | Mixed runtime/settings construction is unavailable and real credential/cache use stays owned. |
| 4 | Revalidate locator model contents (S4) | 0 | Invalid model instances fail before factory, credential, and connector calls. |
| 5 | Canonical authoring values throughout the operation (S5) | 0 | Direct normalized commands receive the same upsert validation as HTTP commands. |
| 6 | Early local ownership authorization (S6) | 3–5 | Unowned IDs cannot trigger branch resolution, credentials, inspection, or persistence. |
| 7 | Acceptance, repository gates, and accurate status records | 1–6 | Named regression matrix and all repository gates pass. |

Keep production fixes scoped to these defects. Tests are required to demonstrate the actual failure
modes; completing isolated code edits or rerunning the old suite is insufficient.

## Step 0: establish regression fixtures

Files: `tests/test_solid_boundaries.py`, `tests/test_pipeline_tasks.py`, `tests/test_user_catalog.py`,
`tests/test_pipelines.py`; add `tests/test_pipeline_authoring.py` and a lifecycle test module if that
keeps the tests readable.

1. Provide explicit A/B Settings, separate SQLite databases, registries, writer flags, encryption
   keys, and shutdown events. Use migrated schemas for lifecycle/persistence acceptance. Keep the
   global fixture compatibility tests separate from owned-app tests.
2. Convert each review probe into a regression before fixing its implementation. Exercise real
   compatibility wrappers, the catalog runner, the authoring adapter, and both app lifespans.
3. Record explicit and implicit session factories, session engine, registry, connector settings,
   writer policy, file-lock database URL, and pipeline lock where relevant. Test the executing thread
   through `asyncio.to_thread`, including outer-context restoration after success and failure.
4. Coordinate blocked workers with events and bounded test timeouts. Enter and exit a Hedron
   lifespan in the same async task so context-variable cleanup is valid. Always release blocked
   threads and await their completion in `finally`; do not use sleeps as synchronization.
5. Use spies for call ordering and actual persisted rows for save/audit/cache assertions. Distinguish
   credential calls, connector factory construction, remote inspection, and definition mutation.

## Step 1: explicit legacy ownership (S1)

Files: `app/services/pipeline_tasks.py`, `app/infrastructure/runtime.py`, `app/database.py`,
`app/connectors/registry.py`, `app/ui/routes/pipeline_runs.py`, and runtime tests.

1. Add explicitly named accessors for the process-global compatibility session factory and registry.
   They must return the legacy resources directly, without consulting active context variables.
   Resolve them at invocation time so existing fixture/CLI rebinding remains supported.
2. Add a named legacy runtime adapter. Settings/event-only compatibility denotes the legacy owner;
   it must not mean arbitrary Settings combined with whichever resources are currently active.
   Verify that supplied settings belong to that legacy configuration and that its database/registry
   configuration is consistent. Reject a foreign Settings owner or configuration mismatch before
   taking a lock, opening a session, or invoking a provider factory. Do not reload providers or
   construct a replacement database as a side effect of normalization.
3. Normalize each public immediate/recovery/retention/supervisor call once to an ExecutionRuntime.
   Keep the existing rejection of independent sessions/registry/lock overrides, including paired
   overrides and runtime plus settings/event/resource arguments. Use canonical runtime-only internal
   operations after normalization; remove null-context fallthrough for accepted execution.
4. Enter the resolved runtime context inside the executing thread. Use that same runtime for the
   file-lock URL, pipeline lock, explicit sessions, implicit sessions, provider settings/registry,
   stop event, and lease-renewal factory. Reset tokens on every exit path.
5. For deferred scheduling, make the runtime authoritative for the environment gate and shutdown
   event. If the compatibility scheduling signature still includes settings/event arguments alongside
   a runtime, permit only the exact matching owner facts or reject; never silently discard conflicts.
   Keep the settings-only scheduling path for actual legacy callers and preserve test-mode behavior.
6. Type session-factory binding around `Callable[[], Session]` and narrow runtime/session access
   interfaces. Preserve DatabaseRuntime request binding without an arbitrary runtime-shaped `Any`.

Acceptance matrix:

- Immediate execution, pending recovery, and retention for the legacy owner run under A, B, and
  no outer context and always use legacy resources; foreign-owner settings reject before I/O.
- Owned A jobs run under B and through deferred/thread dispatch using A only, then restore B.
- Explicit/implicit session resolution, registry/settings, writer policy, locks, and heartbeat
  sessions agree on the same owner.
- All partial/conflicting override combinations reject; actual legacy and complete-runtime positive
  controls remain supported.
- Queued/expired recovery and retention affect only the accepted owner's persisted rows.

## Step 2: cancellation-safe worker draining (S2)

Files: `app/services/pipeline_tasks.py`, `app/infrastructure/runtime.py`, `app/main.py`,
`app/worker.py` where lease-thread draining needs integration, and lifecycle/runtime tests.

1. Keep an explicit task handle for each supervisor `asyncio.to_thread` operation. Shield that handle
   while awaiting it so cancellation of the supervisor cannot discard the only completion handle.
   Track recovery and retention work; signal and account for any threaded poll wait too.
2. On supervisor cancellation or shutdown, set the owned stop event before starting drain. Stop
   scheduling new cycles, await the same in-flight operation, and restore contexts/release locks only
   after it finishes. A cancelled coroutine is not proof that its worker thread has finished.
3. Use one shared lifecycle stop-and-drain helper in the global and composed lifespans. Cancellation
   during shutdown must not propagate into and cancel the protected work handle. Record cancellation,
   continue draining, dispose resources after completion, then preserve cancellation for the caller.
   Account for repeated cancellation requests without a retry loop that loses the work handle.
4. Set readiness false when shutdown begins. Perform owned cleanup on normal termination and worker
   failure too, preserving the original exception. Remove the existing CancelledError suppression
   that proceeds directly to disposal without evidence of thread completion.
5. Include already-running deferred pipeline work in the disposal invariant. Add narrow runtime
   operation registration/draining if supervisor tracking alone cannot observe it. Prevent new
   database-using pipeline work from entering after shutdown admission closes; do not introduce a
   separate global tracker that couples independent apps.
6. Confirm lease-renewal threads have stopped before the enclosing operation is treated as drained.
   The current bounded join must not allow an active owned heartbeat session to outlive engine
   disposal. Integrate that thread into draining or wait for its actual termination on this path.
7. Keep production drain tied to real completion. A test timeout is diagnostic only; do not convert
   timeout expiry into permission to dispose an engine still in use. Preserve cooperative connector
   stop checks and existing HTTP/database timeouts.

Acceptance matrix:

- Block a real supervised operation, request shutdown, and assert disposal is absent until release.
- Cancel the supervisor directly; its stop event becomes set, its thread drains, and cancellation
  is reported after completion.
- Cancel the lifespan while it awaits drain, including repeated cancellation; no early disposal.
- Exercise both lifespans, recovery and retention, active deferred work, and a blocked heartbeat.
- Worker error, cancellation, and ordinary shutdown release locks/sessions exactly once.
- Shutting down A leaves B ready and running; no A thread uses its engine after disposal.

## Step 3: runtime-only catalog composition (S3)

Files: `app/infrastructure/catalog_factory.py`, `app/main.py`, `app/bootstrap.py` as appropriate,
and catalog/owned-app tests.

1. Change `SqlAlchemyCatalogOperationRunner` and `build_catalog_runner` to accept a typed
   ExecutionRuntime only. Remove the separate arbitrary settings parameter and stored settings copy.
   Read `runtime.settings` for UserCatalog, cache, and credential adapters inside each operation.
2. Update every composition call and test to the runtime-only constructor. The old two-argument
   constructor must reject naturally or explicitly; do not retain a permissive mixed-owner overload.
3. Keep the runner's complete context and one-session callback lifetime. Preserve actor metadata,
   callback result typing, and missing-user HTTP behavior while changing configuration ownership.
4. Leave any necessary concrete legacy catalog factory as an explicitly outer compatibility helper;
   do not reintroduce it as a fallback in page/preview/refresh/save routes.

Acceptance: under outer context B, an A runner uses A's session, registry, settings, encryption keys,
and cache TTL. Actual encrypted credential reads and cache writes prove this ownership. Sessions
close and B's context restores on callback failure. All migrated route paths retain runner adoption.

## Step 4: locator instance revalidation (S4)

Files: `app/domain/locators.py`, `app/services/catalogs.py`, compatibility re-exports only if needed,
`tests/test_user_catalog.py`, and locator/domain contract tests.

1. Treat a supported Pydantic locator instance as an input snapshot, not a validation certificate.
   For supported models, serialize its current fields to a Python mapping with aliases and run the
   discriminated locator validator against that mapping. Validate raw mapping inputs through the
   same schema; reject unsupported kinds/types deterministically.
2. Return the validated locator snapshot to the connector. This must validate identifiers, branch
   bounds, RIDs, paths, filenames, UUIDs, checksums, discriminator changes, and missing fields from
   `model_construct`. Nested list mutations must also be revalidated and copied in the snapshot.
3. Preserve public class identity, aliases, JSON, discriminators, defaults, and normal model
   mutability. Avoid globally freezing existing models solely to fix this boundary; freezing also
   does not validate `model_construct` or prevent mutation of nested lists by itself.
4. Validate before schema/row-counter resolver invocation, credential resolution, or remote calls.
   Keep capability rejection tests meaningful by supplying a valid locator when testing unsupported
   schema/count authority; malformed inputs should not instantiate a provider first.
5. Keep CatalogAccess and UserCatalog typed with Locator. Dynamic misuse tests may use an explicit
   test-only cast; normal production code must not erase the concrete contract.

Acceptance: mutated models, unchecked constructed/copied models, bad nested paths, strings, unknown
tags, and incomplete mappings cause zero resolver/factory, credential, and remote calls for both
inspect and count. Valid old/new imports retain class identity and equivalent serialization, and
valid connector calls receive a fully validated independent snapshot.

## Step 5: canonical authoring values (S5)

Files: `app/infrastructure/pipeline_authoring.py`, `app/application/pipelines/authoring.py` only if
a normalized command helper is useful, and direct authoring/pipeline tests.

1. After PipelinePolicy validation, produce one canonical frozen command using validated name,
   source provider, destination provider, and write mode. Use `dataclasses.replace` or an equivalent
   explicit value conversion so unrelated form fields are retained.
2. Use canonical values for every conditional, provider-membership lookup, branch resolution,
   capability lookup, upsert inspection, create-table restriction, and persistence argument.
   Do not mix validated fields with raw command fields later in the operation.
3. Preserve case-sensitive schemas, table names, Foundry paths/branches, IDs, and conflict-column
   names. Only normalize fields governed by the existing domain policy.
4. Retain policy checks before connector I/O and authoritative final persistence validation. Keep
   primary-key defaulting, selected unique-key validation, and existing error messages.

Acceptance: direct operation calls with upper/mixed case and surrounding whitespace behave like
their canonical equivalents. Invalid/non-unique/stale keys reject after the required schema check;
valid primary/unique keys persist; omitted columns default to the primary key. Uppercase upsert
cannot bypass create-table restrictions. HTTP literal/CSRF/redirect behavior remains compatible.

## Step 6: early ownership authorization (S6)

Files: `app/infrastructure/pipeline_authoring.py`, narrow persistence ownership helpers if useful,
and direct authoring/route tests.

1. Load the actor inside the operation-owned session and preserve active-user authorization.
   Perform local ID authorization before constructing a catalog or calling branch/inspection methods.
2. For a nonempty pipeline ID, query the definition with both ID and actor user ID. Missing and
   cross-owner IDs use the same existing message: `That saved pipeline is no longer available.`
3. For effective CSV sources, require the upload ID and query it with actor ownership. Preserve
   the existing missing-upload and no-longer-available messages. Do not reject stale upload form
   fields that are irrelevant when changing a route from CSV to a remote source.
4. Keep local ownership queries, provider derivation, policy validation, catalog access, and final
   save within the same runtime/session. Do not hold a new row lock across remote operations or add
   a second session through the public catalog runner.
5. Retain the authoritative ownership checks in save_pipeline to handle changes between early
   authorization and persistence. Early authorization must not be treated as a caller-supplied
   permission or serialized into the authoring command.
6. Complete remote branch/upsert orchestration only after local ownership and route/writer policy
   gates pass. Preserve existing successful credential/cache commit ordering and actor metadata.

Acceptance: missing/cross-owner pipeline and effective CSV upload IDs cause zero catalog builds,
branch calls, schema inspections, credential decryptions, credential audit rows, and definition
mutations. Cover Foundry branches, PostgreSQL upserts, and CSV-to-Foundry saves. Valid owner edits
and uploads still save/reload; final persistence rejects a reference removed after early checks.
Assert a single operation-owned session and a value-only PipelineSummary result.

## Step 7: acceptance and status reconciliation

1. Run the affected tests after each fix. Keep the six failure probes as named automated regressions,
   plus positive controls demonstrating that legitimate legacy and owned workflows still work.
2. Run Ruff checks/formatting and BasedPyright. Extend boundary/adoption checks to catch reintroduced
   independent runner settings, raw normalized-field branches, and active-context legacy fallthrough.
   Prefer behavioral assertions; architecture checks supplement them.
3. After all new acceptance cases pass, run `make check`, `make hedron-build`, and
   `git diff --check`. Record actual test counts, coverage, deselections, and demo results from the
   final tree. Preserve the 80% coverage floor and Posit compatibility matrix.
4. Reconcile this plan, the completion/follow-up/milestone plans, the major SOLID plan, and the index.
   Keep historical gate results labeled as historical. Mark each S1–S6 closed only against its
   named new evidence; do not claim completion based on general suite counts.

Definition of done: all S1–S6 regressions and acceptance matrices pass; accepted execution has one
owner, shutdown cannot dispose resources still used by owned work, invalid locators and unowned
references cannot reach credentials/connectors, and canonical authoring cannot skip upsert checks.
Public formats and HTTP behavior remain compatible, and all repository gates pass.

## Risks and rollback

- Legacy fixtures may depend on dynamic global rebinding. Resolve global resources at invocation
  time, keep explicit positive controls, and reject inconsistent ownership before I/O.
- Cancellation handling can conceal errors or repeat cleanup. Retain actual task/thread handles,
  preserve original cancellation/failure, and test repeated cancellation and exact cleanup ordering.
- Locator reconstruction can change aliases or nested values. Use existing validators, direct class
  re-exports, and saved-JSON fixtures; do not create duplicate public locator models.
- Early ownership checks can alter validation precedence. Keep established missing/unowned messages
  and verify effective versus irrelevant form references explicitly.

Keep the fix steps reviewable. Roll back an individual implementation if its compatibility gate
fails, while retaining regressions. Do not restore cross-owner fallthrough, early engine disposal,
independent runner settings, trusted unchecked model instances, or remote work before ownership
authorization. No schema migration, deployment, or general compatibility-facade retirement is
required by this plan.

## Historical implementation evidence; later runtime acceptance is recorded separately

- S1: background entry points gained a legacy adapter and complete-runtime context binding. Review
  found that cached settings still bypass consistency checks and matching-event dispatch can fail.
- S2: supervisor work gained shielding/draining and heartbeat joins. Review found untracked deferred
  work, a completion/cancellation race, and readiness staying true during drain.
- S3: `SqlAlchemyCatalogOperationRunner` accepts only `ExecutionRuntime`; cache and credential
  adapters read settings from that runtime.
- S4: catalog inspection and row counting revalidate locator model contents before resolver,
  credential, or connector access.
- S5: authoring branches and persistence use the policy-normalized provider and write mode.
- S6: pipeline and effective CSV upload ownership is checked in the operation-owned session before
  catalog construction, branch lookup, schema inspection, or credential use; final persistence
  checks remain authoritative.

Historical validation reported by the implementation: `make check` passed with 422 tests,
31 deselected, 84.42% coverage,
and 25 demo tests. Ruff, formatting, BasedPyright, Hedron checks, Posit matrix checks, and the
80% coverage floor passed. Focused SOLID/catalog/runtime suites passed with 68 tests, and
`git diff --check` passed. The subsequent D1–D5 review findings and passing regressions are recorded
in the [drain/publication remediation plan](solid-runtime-drain-publication-remediation.md).
