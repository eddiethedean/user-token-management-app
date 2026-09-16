# SOLID remediation: follow-up review fix plan

Status: superseded; earlier follow-up changes are implemented. Runtime acceptance reopened by the
[completion and generation isolation plan](solid-runtime-completion-isolation-remediation.md). The runtime
and failure-handling plans retain historical evidence; this document remains the earlier scope
reference for the migration.
This document remains the earlier scope reference for the
[milestone two remediation plan](solid-milestone-two-remediation.md).

## Scope and observed evidence

| Finding | Reproduced evidence | Required outcome |
|---|---|---|
| F1: catalog audit attribution | An actual encrypted-credential access recorded forwarded IP `203.0.113.9` although the owning app's trust policy required direct IP `127.0.0.1`. | Catalog and save audit facts use request metadata resolved with the owning app's settings. |
| F2: background context ownership | Bundled recovery used the owned explicit database but the global implicit session factory. Session-factory-only recovery also used the global registry and settings. | Immediate work, recovery, and retention bind the same complete runtime; partial dependencies cannot silently select globals. |
| F3: extension writer validation | A provider added before startup declared a nonexistent writer setting, yet `/ready` returned 200 and demo writer policy returned enabled. | Every final provider declaration is validated before readiness or scheduling; invalid registration cannot enter an owned registry. |
| F4: incomplete catalog application boundary | `CatalogFactory` exposes sessions, settings, ORM users, and HTTP requests as `Any`; UI still owns catalog-dependent save orchestration. | Typed domain catalog values, an actor-based operation runner, and a value-only authoring operation are used by production routes. |

The review ran 18 focused tests successfully. Those tests do not contain the new two-app,
proxy-attribution, partial-runtime, extension-startup, or infrastructure-import regressions required
by the previous plan. Add those tests as implementation work, rather than counting the existing
green suites as new acceptance evidence.

Preserve database schema, snapshot/locator serialization, provider IDs, configuration aliases,
routes, CSRF, form fields, fragment IDs, redirect behavior, cache lifetime, credential ownership,
lease fencing, and current transaction boundaries. This does not expand into the complete R7–R10
backlog: full identity migration, full pipeline presentation decomposition, and provider transport
lifetime work remain separate.

## Implementation sequence

| Stage | Work | Dependencies | Completion gate |
|---|---|---|---|
| 0 | Establish targeted regression tests | None | Current failures reproduce without relying on globally initialized providers. |
| 1 | Bind complete runtime and isolate compatibility adapters | Stage 0 | All runtime entrypoints use identical explicit and implicit owned dependencies. |
| 2 | Validate extension declarations through startup and registration | Stage 0; run independently of Stage 1 | Invalid providers cannot reach readiness or worker execution. |
| 3 | Define typed catalog values and actor-based contracts | Stage 0 | Contracts import only inward values and standard typing utilities. |
| 4 | Resolve request metadata and compose catalog operations | Stages 1 and 3 | Real credential-access audit attribution uses each app's proxy policy. |
| 5 | Cut over value-only authoring and pipeline consumers | Stages 2–4 | Browsing and preview consumers use the new operation runner; the save-authoring adapter still needs migration. |
| 6 | Complete acceptance evidence and documentation | All stages | Repository gates plus all new regression and boundary tests pass. |

Stages are reviewable units, not permission checkpoints. Deliver each change with its affected
tests; keep the existing composed request binding and passing provider behavior intact.

## Stage 0: establish meaningful regression coverage

Primary files: `tests/test_pipeline_tasks.py`, `tests/test_solid_boundaries.py`,
`tests/test_connector_registry.py`, `tests/test_user_catalog.py`, `tests/test_pipelines.py`, and
`tests/test_ui_route_boundaries.py`.

1. Add a reusable fixture creating two independent applications with different settings, databases,
   registries, and fake remote state. Migrate each temporary database through Alembic using its URL;
   do not rebind `app.database` globals or mock schema validation in final lifecycle acceptance tests.
2. During an actual recovery worker call, inspect both its explicit factory and
   `current_session_factory()`, plus `current_registry()`, connector settings, and implicit writer
   policy. Verify recovery and janitor callbacks are each inside the complete owned context.
3. Schedule an immediate job under app A, reset the request context, bind app B, then execute A's
   deferred job. Assert A owns every dependency and that B's context is restored afterward.
4. Exercise the retained session-factory-only API. Its required outcome is a clear error before any
   session opens or provider resolves, unless a complete runtime is supplied through the supported
   compatibility adapter. Do not permit accidental success through the default registry.
5. Store a reserved demo credential bundle and perform a real catalog credential access through the
   resolver. Set opposite app/global proxy policies and assert the persisted `api_token.used` event
   contains the owning app's normalized source IP and request ID. Test both directions of trust.
6. Register missing, blank, and nonboolean writer declarations after app construction but before
   lifespan startup. Test demo and real configurations. Assert failure before readiness and before
   dispatch; a capability default of `True` must not conceal an invalid declaration.
7. Add boundary checks for the migrated UI modules and new catalog/authoring application modules.
   Walk the full AST and resolve relative imports; ordinary, aliased, type-only, and function-local
   imports must all be checked. Add small synthetic import snippets to verify the checker itself.

Use events and bounded waits to synchronize background tests. Release blocked test workers in
`finally` blocks so an assertion failure cannot hang shutdown. Existing global-app fixtures remain
the compatibility baseline; the new isolation fixture must not use them.

## Stage 1: bind complete runtime dependencies

Primary files: `app/infrastructure/runtime.py`, `app/database.py`,
`app/services/pipeline_tasks.py`, `app/bootstrap.py`, `app/main.py`, and
`app/ui/routes/pipeline_runs.py`.

1. Give `ExecutionRuntime.sessions` a concrete session-factory type and give database context
   binding a typed factory/context API. Remove `Any` used solely to accept arbitrary runtime-shaped
   objects. Reuse the existing app-owned engine and factory; never construct a replacement database.
2. Introduce canonical runtime-only internal functions for immediate execution, pending-run
   recovery, and retention. Each synchronous function enters `runtime.context()` in the thread
   doing the work, before resolving sessions or providers. Keep the explicit worker and lease-keeper
   factory equal to the factory bound by that context.
3. Have the supervisor pass the runtime object to those functions instead of unpacking it into
   independent optional arguments. Bind the supervisor context for its lifetime as well, but retain
   per-operation binding so direct synchronous callers remain correct.
4. Keep the per-app execution lock and SQLite file lock. Immediate, recovery, and retention paths
   must use that same lock; independent apps must not acquire the legacy module lock.
5. Confine compatibility handling to named adapters. A call with no overridden resources may resolve
   one complete legacy runtime from legacy settings, sessions, registry, lock, and event. A caller
   supplying an owned session factory must also supply a complete owned runtime; reject missing
   registry/settings/ownership rather than filling them from process globals. Reject conflicting
   runtime and explicit overrides before opening a database session.
6. Update global-app and composed-app lifecycle calls, deferred scheduling, and tests to use the
   canonical interfaces. Preserve `app.main:app` and existing CLI behavior. Avoid separate signature
   branches introduced only to accommodate outdated monkeypatched tests.
7. Restore every context token on success, worker failure, nested operation, and cancellation. The
   lease thread keeps an independent session and explicit factory; it does not inherit context vars.
   Signal shutdown and wait for owned execution to finish before disposing the database. Account for
   a cancelled `asyncio.to_thread` await without assuming its underlying thread has stopped.

Gate: two apps show parity between all explicit and implicit dependencies; deferred jobs, recovery,
retention, error cleanup, and independent shutdown pass. Partial-resource calls fail before I/O.

## Stage 2: validate the complete provider registry

Primary files: `app/connectors/registry.py`, `app/bootstrap.py`, and `app/main.py`.

1. Associate an owned registry with its actual `Settings` model, including supported settings
   subclasses. Validate a candidate provider specification before committing registration or
   replacement into an owned registry. Keep raw legacy/test registry construction available as an
   explicitly unowned compatibility path.
2. At lifespan startup, validate the final registry again after application construction and all
   pre-start extension registration, before setting `ready=True` or starting the supervisor. Apply
   this order to both app entrypoints. Early built-in validation remains useful but is insufficient.
3. A declared writer flag must be a nonblank supported configuration field with a boolean schema
   annotation and boolean resolved value. Do not accept fabricated `model_fields` dictionaries or
   namespace attributes as proof of configuration support. Test a legitimate `Settings` subclass
   defining a new boolean field as well as an extension reusing an existing built-in boolean field.
4. Preserve `writer_setting=None` as capability-only policy and preserve valid demo capability
   defaults. Structural validation applies in demo too. Malformed runtime values remain denied;
   no string coercion or missing-field fallback may enable a writer.
5. An invalid replacement must leave the prior valid specification intact. Registration errors
   identify provider and setting names without printing configuration values or credentials.
   Registration on an owned registry remains validated even after startup; no runtime plugin-reload
   feature or emulator reset is introduced by this change.
6. On startup failure, leave readiness false, do not launch workers, and dispose only the failed
   app's owned resources. App B must remain usable when app A fails validation.

Gate: invalid extensions fail registration or final startup validation in both modes; valid custom
boolean fields work with enabled and disabled values; built-in demo/live behavior remains unchanged.

## Stage 3: replace erased catalog contracts with typed values

Primary files: `app/application/catalogs.py`, new domain catalog/locator modules,
`app/connectors/base.py`, and `app/connectors/locators.py`.

1. Move framework-neutral locator/write-policy/snapshot definitions inward and re-export from the
   existing connector path. Move `RemoteNamespace`, `RemoteObject`, `CatalogPage`, `ColumnSchema`, and
   `ObjectSchema` into a domain catalog module and re-export them from `app.connectors.base`.
2. Preserve class identity through re-exports, discriminators, aliases, validation rules, serialized
   fields, and snapshot versions. Do not introduce wrappers or parallel DTO classes that break
   `isinstance`, equality, fixture imports, or saved-definition parsing.
3. Give every `CatalogAccess` method its actual locator and result types. Eliminate `Any` from this
   new contract; application contracts must not import connector, framework, ORM, or infrastructure
   packages, including via function-local or type-only imports.
4. Replace `CatalogFactory(db, settings, user, request)` with a generic `CatalogOperationRunner`:
   it accepts `ActorContext` and a callback taking `CatalogAccess`, and returns the callback's result
   type. Sessions, ORM users, HTTP requests, and configuration objects are not arguments to the port.
5. Define a separate value-only authoring contract and command for the save operation. Carry all
   existing selection fields, upload ID, pipeline ID, and conflict-key information as typed values.
   Return `PipelineSummary` or another frozen value containing the saved ID, never an ORM object.

Gate: type checks detect invalid catalog inputs/results, import rules pass, and serialized locator
and schema compatibility tests prove the moved definitions remain the same public classes.

## Stage 4: resolve app-scoped request facts and compose catalog sessions

Primary files: `app/ui/routes/pipeline_context.py`, new UI request-metadata helper,
`app/infrastructure/catalog_factory.py`, `app/infrastructure/security/credentials.py`,
`app/services/catalogs.py`, `app/services/secrets.py`, and `app/bootstrap.py`.

1. At the HTTP boundary, construct `ActorContext` from the authenticated user ID, middleware request
   ID, and `app.security.client.client_ip(request, settings)`. Use that request's injected app settings.
   Keep the request and settings out of application callback arguments.
2. Inside infrastructure, convert those safe facts to `RequestMetadata` and supply it to catalog
   credentials and audit/persistence operations. Update affected legacy service annotations to accept
   `RequestMetadata` alongside `Request` where legacy compatibility still requires it. New production
   catalog and authoring paths must never pass a web request inward.
3. Implement the operation runner using the captured app runtime. Open a session in the executing
   thread, load the actor's user, construct cache/credential/provider adapters inside that session,
   execute the callback, and close the session in `finally`. Bind the complete runtime for the operation.
4. Resolve missing actors through an application/domain error mapped to the existing HTTP response
   at the UI boundary. Preserve existing active-user and owner checks; do not rely on an ORM object
   supplied by the route as authorization evidence.
5. Keep owner checks before decryption and `purpose="catalog"` audit attribution. Cache only
   credential-free metadata; do not store the runner's credential memoization across operations.
6. Compose the runner and authoring dependencies in `ApplicationComposition`. Provide a named legacy
   route composition adapter for the global app that resolves its current legacy database at the
   proper lifecycle boundary; preserve fixtures that rebind the global database. Inject typed route
   dependencies through `register_routes` and `register_pipeline_routes` with no UI fallback factories.

Gate: real encrypted catalog access records the correct app-scoped source IP/request ID, cross-owner
access fails before decryption, sessions close on rendering errors, and two apps retain cache isolation.

## Stage 5: migrate production consumers and authoring

Primary files: `app/ui/routes/pipeline.py`, `pipeline_preview.py`, `pipeline_datasets.py`,
`pipeline_save.py`, `pipeline_context.py`, and new application/infrastructure authoring modules.

1. Replace catalog-backed render and preview annotations with `CatalogAccess` directly. Use the
   injected runner around callbacks for namespace/object options, branch lookup, schema inspection,
   row counts, direction swaps, dataset refresh, and page rendering. Keep the session alive throughout
   the callback, including `_pipeline_body_in_thread` rendering.
2. Remove production reliance on the `TYPE_CHECKING`/runtime `UserCatalog` alias split. Move tests
   that patch `app.ui.routes.pipeline.UserCatalog` to the new runner/gateway or the named legacy
   implementation seam. Preserve legacy service/connector imports independently of route annotations.
3. Make `/pipeline/save` translate form data to the value-only command, create its actor context, and
   invoke the injected authoring operation. It should retain HTTP validation mapping and redirect
   behavior, with no ORM user lookup, adapter construction, branch queries, or upsert-key orchestration.
4. In the operation-scoped authoring gateway/use case, load the owner and derive connected providers
   from persistence. Do not trust a caller-supplied provider set as authorization. Resolve Foundry
   branches and current PostgreSQL primary/unique keys using the same session and catalog operation.
5. Preserve create-table restrictions, primary-key defaulting, selected unique keys, owner checks on
   existing pipelines/uploads, and authoritative writer/route validation. Maintain the established
   error messages and ensure denied writers cannot proceed to connector I/O.
6. Use the existing authoritative persistence service through an infrastructure adapter where needed.
   Legacy `PipelineCommands` can remain for unmigrated callers, but migrated routes must not construct
   its ORM/request-bearing command. Broader pipeline authoring strategy extraction remains separate.
7. Preserve current credential/cache short commits and final save commit ordering. Do not promise a
   new all-or-nothing transaction around existing helpers that commit internally. An invalid authoring
   request must not mutate a pipeline definition, while legitimate audit/cache writes keep their
   existing semantics. Do not open a second independent catalog session during save validation.

Gate: page, preview/OOB targets, dataset creation refresh, branch-specific saves, CSV reloads, upsert
keys, cross-owner denial, and save audit behavior pass through the new production contracts.

## Stage 6: acceptance evidence and documentation

| Evidence | Required checks |
|---|---|
| Runtime ownership | Actual queued/expired recovery, deferred dispatch after context reset, explicit/implicit parity, retention targeting, nested/error cleanup, independent shutdown and fencing. |
| Request attribution | Real encrypted access and save events; opposite proxy policies; invalid forwarding input; request ID preservation; no plaintext in HTML, cache, event detail, or captured logs. |
| Provider validation | Missing/blank/wrong-schema/wrong-value flags; final pre-start extension registration; safe replacement; legitimate boolean Settings extension; demo/live defaults. |
| Catalog contracts | Typed inward DTOs, old import class identity and serialization, generic callback result typing, operation-scoped sessions, actor authorization, no HTTP/ORM values in ports. |
| Production adoption | UI uses runner and authoring ports; no SQLAlchemy/credential adapter construction or infrastructure imports in migrated routes; named legacy exceptions are limited and documented. |

Run affected suites after each stage. Once all outcome tests pass, run `make check`,
`make hedron-build`, and `git diff --check`. Maintain the 80% coverage floor and demo checks; record
the actual test counts, coverage, and deselections. Do not reuse the prior 413-test result as evidence
for tests that have not been added. Default acceptance remains offline; live provider and configured
Posit deployment qualification stay external gates.

Update `docs/architecture.md`, both SOLID plans, and the index only after acceptance passes. Name
each new test supporting closure and distinguish compatibility checks from new regression evidence.
Before that point, milestone two is implemented with remediation still pending.

## Risks, constraints, and rollback

The main risks are context loss across threads, accidental dependence on rebinding fixtures,
unvalidated extension replacement, changed audit attribution, and save/cache commit ordering.
Keep each cutover behind its typed injected operation, retain compatible service paths, and test
production adoption rather than adding unused ports.

No schema migration, public route change, or saved-format change is planned. If a cutover fails,
stop and await owned work before reverting its wiring; preserve queued runs and encrypted data.
Do not roll back by restoring global fallback for partially owned execution or permissive writer
validation. Previously committed remote writes remain governed by existing reconciliation rules.
