# SOLID remediation completion plan

Status: earlier consumer/authoring scope implemented; runtime acceptance reopened by the
[completion and generation isolation plan](solid-runtime-completion-isolation-remediation.md).
Runtime work is recorded by the
[runtime/lifecycle plan](solid-runtime-lifecycle-remediation.md) and its
[failure handling and legacy ownership follow-on](solid-runtime-failure-remediation.md). This
document preserves the previous consumer/authoring scope and evidence and supplements the
[follow-up plan](solid-remediation-follow-up.md).

## Findings and baseline

| ID | Verified current behavior | Required result |
|---|---|---|
| C1 | Supplying both an owned session factory and registry passes the compatibility guard, but `current_session_factory()` remains global. | Every accepted background call binds one complete runtime; independent resource overrides are rejected before I/O. |
| C2 | An actual pipeline save records source IP `203.0.113.9` when its app's trust policy requires `127.0.0.1`. | Created/updated pipeline events and catalog credential events receive metadata resolved with the owning request's settings. |
| C3 | An extension registered on an owned registry outside a context receives no settings when later resolved inside that runtime. | Registration and every later factory invocation use the registry owner's settings, including under another app's context. |
| C4 | Catalog methods accept `object` and forward it through unchecked `cast(Locator, ...)`; an arbitrary string reaches a connector. | Catalog inputs/results use concrete inward value types; invalid dynamic inputs fail before credentials or connector I/O. |
| Adoption gap | Full-page rendering and save still use `CatalogFactory`; save orchestration constructs an ORM/request-bearing command in UI. | Page/preview/catalog refresh use the runner, and save uses an injected value-only authoring operation with one operation-owned session. |

The preceding completion pass added boundary regressions and ran the full
repository gates: `make check` passed with 421 tests, 31 deselections, 84.51% coverage, and 25
demo tests. `make hedron-build`, `git diff --check`, and the Posit compatibility matrix also passed.
These are historical compatibility results; they do not close the subsequent six findings.

Preserve database schema, provider IDs, settings aliases, serialized locators/policies/snapshots,
routes, form names, CSRF, redirects, OOB region IDs, connection ownership, credential secrecy,
cache lifetime, lease fencing, and existing audit/cache/save commit ordering. Keep this scoped:
full identity migration, dataset-provisioning redesign, provider transport lifetimes, and complete
presentation decomposition remain separate work.

## Delivery order

| Step | Change | Depends on | Gate |
|---|---|---|---|
| 0 | Capture current failures in regression tests | None | Tests expose C1–C4 and production adoption gaps. |
| 1 | Make runtime ownership mandatory | 0 | Override matrix, deferred work, recovery, retention, and context restoration pass. |
| 2 | Bind provider factories to registry ownership | 0 | Factory signatures and hostile/nested context cases pass with real Settings. |
| 3 | Fix persisted save audit metadata | 0 | Created/updated events pass opposing proxy-policy cases. |
| 4 | Relocate catalog and locator values inward | 0 | Identity, serialization, typing, and runtime rejection checks pass. |
| 5 | Complete runner composition and page cutover | 1, 2, 4 | Page/preview/refresh use the runner without a runtime/type alias split; save follows in step 6. |
| 6 | Implement value-only authoring and save cutover | 2–5 | Authoring behavior passes with one session and no ORM/request command in UI. |
| 7 | Run acceptance and reconcile documentation | 1–6 | All named regressions, architecture checks, repository gates, and build pass. |

Deliver small changes with their tests in this order. Steps 1–3 are independent code changes after
their regressions exist; they are not permission checkpoints. Do not stop after these localized
fixes: steps 4–7 are required for completion when this plan is implemented.

## Step 0: establish regressions and test seams

Files: `tests/test_pipeline_tasks.py`, `tests/test_connector_registry.py`,
`tests/test_user_catalog.py`, `tests/test_pipelines.py`, `tests/test_solid_boundaries.py`,
`tests/test_ui_route_boundaries.py`; add a shared isolation helper where useful.

1. Create two explicit Settings instances with different SQLite databases, registries, writer flags,
   proxy policies, and credential keys. For lifecycle acceptance, migrate each database through
   Alembic using the supplied URL. Avoid the global rebinding fixtures in isolation tests.
2. Add an override matrix for immediate execution, pending recovery, retention, and the supervisor:
   sessions only, registry only, sessions+registry, lock only, and a runtime combined with conflicting
   explicit overrides. Assert rejection before a session opens or a factory runs. Retain complete
   runtime and no-override legacy cases as positive controls.
3. Probe explicit and implicit factories, registry, connector settings, writer policy, and lock inside
   real synchronous callbacks. Exercise worker dispatch via `asyncio.to_thread`, not only direct calls.
4. Add extension factories accepting a required keyword settings argument, an optional settings
   argument, and no arguments with `connector_settings()` lookup. Register outside any context and
   under app B while targeting app A. Resolve without a context and under B; A must remain the owner.
5. Perform real created/updated saves with stored encrypted demo bundles. Query persisted audit rows
   for opposite global/app trust policies and assert both normalized IP and request ID. Keep actual
   `api_token.used` checks; inspecting a resolver's fields alone is insufficient.
6. Add locator compatibility assertions and invalid-input spies. Invalid values must not invoke a
   credential resolver or connector. Add route-level adoption checks and fake operation ports so
   tests demonstrate that production pages and saves invoke the new interfaces.

Use events and bounded waits for blocked work. Release test threads in `finally`; no fixed sleeps
as synchronization. Keep existing global-app tests as a separate compatibility baseline.

## Step 1: close all runtime override escape paths (C1)

Files: `app/services/pipeline_tasks.py`, `app/infrastructure/runtime.py`, `app/database.py`,
`app/main.py`, `app/ui/routes/pipeline_runs.py`, and runtime tests.

1. Define canonical internal entrypoints for immediate execution, recovery, retention, and supervision
   that accept only an `ExecutionRuntime` plus operation-specific values such as run ID.
   Each synchronous operation enters `runtime.context()` in its executing thread before resolving
   sessions or providers. Use the runtime's factory explicitly for worker and lease sessions.
2. Keep public compatibility wrappers only where existing callers need them. Centralize argument
   normalization: any independent session, registry, or lock override without a complete runtime
   raises a clear `ValueError`. A supplied runtime must not be silently combined with explicit
   settings/events/resources from another owner. Remove supervisor unpacking into optional arguments.
3. Introduce a named legacy runtime adapter for settings/event-only callers. Resolve the current
   legacy factory at invocation time so rebinding fixtures continue to work; use an explicit legacy
   registry accessor and the shared legacy lock. Do not adopt resources from an unrelated active
   app context or construct a replacement database.
4. Type the database context variable and binding API around the actual session factory. Retain
   compatibility for `DatabaseRuntime` request binding, but avoid arbitrary runtime-shaped `Any`.
   All app-owned entrypoints reuse the existing runtime's database, registry, lock, and shutdown event.
5. Wire both lifespans and deferred scheduling to the canonical complete runtime. Update monkeypatch
   tests to the intended interface rather than retaining separate call branches solely for old tests.
6. Reset all context tokens on nested calls and failures. On cancellation/shutdown, signal the owned
   event and await the actual in-flight thread before disposal; cancelling a `to_thread` await does
   not stop its thread. Track/shield the in-flight operation if needed to make draining explicit.

Acceptance: every override matrix case is rejected before I/O; A's deferred job executed under B
uses A and restores B afterward; queued/expired recovery and retention target A only; cancellation
and independent app shutdown do not dispose a database still used by an owned thread.

## Step 2: make provider factory ownership deterministic (C3)

Files: `app/connectors/registry.py`, `app/bootstrap.py`, provider registry tests.

1. An owned registry's Settings reference is authoritative. Capture it when binding a candidate
   factory; never replace it with `_ACTIVE_SETTINGS` from another app. Preserve a clearly documented
   unowned registry path for legacy tests and decorators.
2. Use the owner for both the discovery call that reads capabilities and every later instantiation.
   During zero-argument factory invocation, enter the owner's registry/settings context so factories
   using `connector_settings()` resolve correctly. Restore any outer context on success or error.
3. Decide settings-argument support before executing the factory, using its callable signature or an
   explicit factory adapter. Support existing zero-argument factories and settings-aware factories.
   Do not catch a `TypeError` thrown by the factory body and retry without settings: propagate the
   original failure and avoid double construction or side effects.
4. Keep candidate writer-setting validation before registry mutation, using real Settings schema and
   boolean values. Invalid replacement or construction failure leaves the previous valid spec intact.
   Do not silently rebind an owned registry to different settings during built-in loading.
5. Revalidate the final registry before readiness/scheduling. Set readiness false before validation,
   and ensure startup failure disposes only that app's owned resources without launching its worker.

Acceptance: settings-aware and implicit-settings factories observe owner A across all registration
and lookup contexts; internal `TypeError` invokes the factory once; valid Settings subclasses work;
missing/blank/nonboolean flags and invalid replacements remain denied in demo and real modes.

## Step 3: fix save audit facts before authoring migration (C2)

Files: a shared UI request-facts helper, `app/ui/routes/pipeline_context.py`,
`app/ui/routes/pipeline_save.py`, `app/services/pipelines.py`, and audit/route tests.

1. Add one HTTP-boundary helper creating `ActorContext` from authenticated user ID, middleware
   request ID, and `client_ip(request, settings)` using injected app settings.
2. Apply it consistently to catalog callbacks and save handling before crossing a thread boundary.
   Infrastructure converts actor facts to `RequestMetadata`; no later global settings lookup may
   reinterpret those facts.
3. As an interim fix, pass safe metadata through the existing save command/service path and annotate
   the persistence service to accept `RequestMetadata` alongside its legacy Request argument.
   Preserve raw Request support only for unmigrated callers. This prevents C2 while step 6 is built.
4. Ensure both `pipeline.created` and `pipeline.updated` receive that metadata. Catalog credential
   usage during validation receives the same facts with `purpose="catalog"`. Do not put credentials
   into actor values, audit details, cache payloads, rendered content, or logs.

Acceptance: real saves and encrypted catalog reads record the owning app's IP/request ID in both
trust directions; untrusted forwarding is ignored, invalid trusted forwarding falls back to the
direct address, and global configuration changes do not affect a captured operation's facts.

## Step 4: restore concrete inward catalog values (C4)

Files: new `app/domain/locators.py` and `app/domain/catalogs.py`, `app/connectors/locators.py`,
`app/connectors/base.py`, `app/application/catalogs.py`, `app/services/catalogs.py`, and value tests.

1. Move the existing locator, write-policy, snapshot models, constants, parsers, and helper functions
   inward as one coherent module. Re-export the same symbols from `app.connectors.locators`.
   Move `RemoteNamespace`, `RemoteObject`, `CatalogPage`, `ColumnSchema`, and `ObjectSchema` inward
   and re-export them from connector base. Keep transfer protocols and transport-specific values outer.
2. Preserve class identity, field order/defaults, dataclass frozen behavior, Pydantic aliases,
   discriminators, validators, snapshot versions, and serialized output. Do not create duplicate
   protocol/value hierarchies or conversion wrappers where the existing concrete values suffice.
3. Type `CatalogAccess` with `Locator` inputs and concrete namespace/page/schema results. Restore
   concrete input types in `UserCatalog`; remove casts introduced solely to accept arbitrary objects.
   Keep the generic operation runner and callback result type.
4. Deserialize untrusted inputs at designated boundary parsers, whose input can legitimately be
   `object`. For dynamic misuse of typed catalog methods, validate a supported locator before
   credentials or connector resolution; reject malformed inputs deterministically. Do not mask a
   locator type mismatch with a cast or a catch-all metadata fallback.
5. Add positive type assertions for valid locators/callback results and a separate negative type-check
   fixture proving a string/arbitrary object cannot be supplied as a locator. Do not leave intentional
   type errors in the normal project check. Verify old/new imports are identical public classes and
   saved JSON round-trips unchanged.

Acceptance: inward modules have no connector/framework/ORM/infrastructure imports; valid existing
fixtures retain identity/equality/serialization; static checking rejects invalid catalog calls and
dynamic invalid inputs cannot trigger credential access or remote operations.

## Step 5: complete catalog composition and rendering adoption

Files: `app/bootstrap.py`, `app/main.py`, `app/infrastructure/catalog_factory.py`,
`app/ui/routes/__init__.py`, `pipeline.py`, `pipeline_context.py`, `pipeline_preview.py`,
`pipeline_datasets.py`, and route boundary tests.

1. Construct the owned catalog runner from the captured runtime in `ApplicationComposition`.
   Use `runtime.settings` as its sole settings source; remove separately supplied arbitrary settings.
2. Provide a named legacy route-composition adapter that resolves the global runtime at the lifecycle
   boundary. Inject application operation ports through the route registrars. Avoid anonymous
   settings/runtime pairing and UI fallback implementations.
3. Move `_pipeline_body_in_thread` onto the runner: construct the actor at the HTTP boundary and render
   the whole catalog-dependent body inside the operation callback. Infrastructure owns the catalog
   session for the callback's lifetime and closes it on rendering exceptions.
4. Keep preview/options/direction-swap/dataset-refresh callbacks on the same port. Use `CatalogAccess`
   annotations directly and remove the `TYPE_CHECKING`/runtime legacy `UserCatalog` split.
   Move tests patching that alias to the injected operation or named legacy implementation seam.
5. Raise a framework-neutral missing-actor failure inside operation infrastructure/application code
   and map it to the existing HTTP response at the UI boundary. Preserve active-user/owner checks.
   Retain `WithUserSession` only for unrelated unmigrated operations such as dataset provisioning;
   it must not become a fallback for migrated catalog or save calls.

Acceptance: page, preview, OOB fragments, swap behavior, and dataset refresh pass through a fake
runner and the real owned runner; sessions close on callback failure; A/B caches remain isolated;
no catalog-backed production page can fall through to `CatalogFactory`.

## Step 6: implement value-only authoring and migrate save

Files: new `app/application/pipelines/authoring.py` and infrastructure authoring adapter,
`app/bootstrap.py`, `app/main.py`, `app/ui/routes/pipeline_save.py`, persistence service annotations,
and pipeline/ownership/authoring tests.

1. Define a frozen authoring command containing all current form values: name, source/destination
   providers, schemas/objects, destination-new-name selection, write mode, conflict columns, upload
   ID, and pipeline ID. Derive branches within the operation. Do not accept ORM users, Requests,
   database sessions, settings, or a caller-authorized provider set.
2. Define an application `PipelineAuthoringOperation(actor, command) -> PipelineSummary` contract.
   Keep legacy `PipelineCommands` for unmigrated callers; the save route must stop constructing
   `SavePipelineCommand` with ORM/request values.
3. The infrastructure adapter enters the complete runtime and opens one thread-owned session.
   Load the actor, derive connected providers from persistence, and authorize existing pipeline/upload
   ownership. Gate route/writer capability before connector I/O; denial must not decrypt credentials
   or inspect a remote destination. Keep final persistence validation authoritative too.
4. Build the catalog, credential/cache adapters, and a narrow persistence port over that same session.
   A framework-neutral authoring coordinator resolves branches and upsert keys through those ports
   and invokes persistence. Do not call the public catalog runner if it opens a second session.
5. Preserve PostgreSQL create-table/upsert restrictions, primary-key defaulting, selected unique-key
   validation, Foundry connection/provisioned dataset branches, CSV upload ownership, overlapping-route
   rejection, and existing user-visible validation messages.
6. Persist through the existing authoritative service, using RequestMetadata from the actor. Convert
   the ORM result to `PipelineSummary` before closing the session. Preserve short credential/cache
   commits and final save commit ordering; invalid saves cannot mutate definitions, while existing
   audit/cache side effects retain their established semantics.
7. Reduce the route to form-to-command translation, actor creation, execution via `asyncio.to_thread`,
   error-to-HTTP mapping, and the existing redirect. Remove branch queries, secret-list queries,
   upsert-key orchestration, session helpers, and CatalogFactory from this production entrypoint.
   Remove the application CatalogFactory alias once page and save no longer need it; retain any
   necessary legacy concrete factory only in its explicitly named outer compatibility module.

Acceptance: branch-specific and CSV saves/reloads, primary and unique upsert keys, create-table
restrictions, writer denial before I/O, cross-owner rejection, event metadata, and redirect/CSRF
behavior pass. Assert catalog validation and persistence share one session and no ORM result escapes.

## Step 7: final evidence and documentation

| Area | Required evidence |
|---|---|
| Runtime | Override matrix; explicit/implicit parity; deferred context restoration; queued/expired recovery; retention isolation; cancellation/draining; independent shutdown. |
| Provider ownership | Registration and lookup under no context/A/B; required/optional/zero-argument factories; one invocation on internal TypeError; safe replacement; schema validation. |
| Audit | Persisted created/updated/credential events; both proxy-policy directions; malformed forwarding fallback; request ID preserved; no plaintext escape. |
| Values | Identical legacy re-exports; unchanged model JSON; locator runtime rejection before I/O; positive/negative static typing. |
| Adoption | Page/preview/refresh use runner; save uses value-only operation; one save session; no production catalog factory, unchecked locator cast, or legacy UserCatalog alias. |

1. Extend AST checks to cover ordinary, aliased, relative, function-local, and type-only imports; test
   the checker using synthetic snippets. New domain/catalog/authoring modules cannot import outer
   packages. Migrated UI cannot import infrastructure or construct concrete cache/credential adapters.
   Check production use of the ports, not merely whether unused protocol definitions exist.
2. Keep exceptions narrow: legacy pipeline compatibility exports and unrelated dataset/session
   adapters are separately named, not a blanket exemption for application or route packages.
3. Run affected tests after each step; once acceptance cases pass, run `make check`,
   `make hedron-build`, and `git diff --check`. Keep the 80% coverage floor and demo checks.
   Record actual counts, coverage, deselections, and named new regressions from this implementation.
4. Reconcile `docs/architecture.md`, all SOLID plans, and the plan index. Mark findings closed only
   when their behavior and adoption gates pass. Stages with missing inward types, full-page cutover,
   or authoring adoption remain incomplete regardless of general suite results.

Definition of done: all C1–C4 regressions pass, page/save adoption is complete, public formats and
HTTP behavior remain compatible, and repository gates pass. Default acceptance stays offline;
live-provider and configured Posit deployment qualification remain external checks, not substitutes
for local remediation evidence.

## Risks and rollback

Factory introspection can mishandle unusual callable signatures: isolate settings-aware versus
zero-argument adaptation and test both without swallowing factory-body exceptions. Moving public
values can change import identity or serialization: use direct re-exports and compatibility fixtures.
Authoring can alter session/commit ordering: bind all authoring ports to one existing session and
verify invalid requests leave pipeline definitions unchanged. Shutdown can leave a worker thread
running: signal and drain owned work before disposal or rollback.

Keep changes reviewable by the ordered steps. Retain legacy import paths and no-override runtime
entrypoints for compatibility, but do not roll back by restoring mixed-owned/global execution,
global reinterpretation of captured audit metadata, or untyped locator inputs. No schema migration,
deployment, or broad compatibility-facade removal is part of this plan.
