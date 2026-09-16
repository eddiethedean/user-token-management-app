# SOLID milestone two: review remediation plan

Status: superseded; runtime acceptance reopened by the
[completion and generation isolation plan](solid-runtime-completion-isolation-remediation.md).
Historical runtime work is recorded by the runtime and
[failure handling and legacy ownership plans](solid-runtime-failure-remediation.md). This record
retains the milestone-two scope and historical implementation evidence.
This document remains the milestone-two scope record.

## Outcomes and evidence

| Finding | Observed behavior | Required result |
|---|---|---|
| P1: recovery registry isolation | Recovery used the composed database but the global connector registry. | Immediate execution, queued-run recovery, expired-run recovery, and retention use the same app-owned resources. |
| P2: implicit writer settings | With an app registry bound, implicit PostgreSQL policy returned `True` while its explicit settings returned `False`. | Implicit policy within that app agrees with explicit policy, including inside worker threads. |
| P2: missing writer flag | A provider declaring a nonexistent setting returned `True` when its capability default was enabled. | Invalid declarations fail composition; runtime policy never enables a writer through a missing or non-boolean declared flag. |
| P2: UI chooses implementations | Three pipeline UI modules instantiate SQLAlchemy cache and credential adapters. | Composition constructs these adapters; UI receives application contracts and returns the same pages, fragments, and save results. |

Preserve schema, locator/snapshot formats, provider IDs, environment aliases, routes, form names,
CSRF behavior, fragment IDs, default writer flags, and demo-mode policy. Keep the existing in-process
execution model. This remediation does not complete unrelated identity, provider transport, or full
pipeline presentation work from R7–R10 of [the refactor plan](solid-refactor.md).

## Target ownership and contracts

The composition root owns one typed execution dependency bundle containing settings, a SQLAlchemy
session factory, connector registry, and pipeline execution lock. The concrete bundle belongs under
`app/infrastructure/runtime`, not in the framework-neutral application layer. Its resources are
references to the app's existing resources; constructing it must not create a second database or
reload provider emulator state.

Requests bind database, registry, and settings together. Scheduled jobs capture the bundle when
scheduled and receive it explicitly. The supervisor receives the same bundle at startup. A job must
not depend on whatever request context happens to exist when it eventually runs.

For catalog consumers, introduce an application `CatalogAccess` protocol covering the methods already
used by the pipeline UI: namespace/object discovery, schema inspection, row counts, default branch,
and branch for a namespace. Introduce a generic `CatalogOperationRunner` protocol that accepts an
`ActorContext` and a callback receiving `CatalogAccess`, opens the correct thread-owned session,
and closes it after the callback. The interface must not expose `Session`, `User`, `Request`, cache
implementations, credential bundles, or concrete connectors.

Where these contracts need locator/catalog values, move the existing framework-neutral locator
definitions from `app/connectors/locators.py` inward and re-export them from their existing path.
Likewise relocate the required namespace, object, page, column, and schema values from connector
contracts into domain modules. Preserve class identity through re-exports, serialized discriminators,
Pydantic aliases, validators, and snapshot versions. Application contracts must not import the outer
connector package, including through type-only imports.

Adapter classes implement these interfaces in infrastructure. `UserCatalog` may remain the legacy
implementation behind the adapter for this scoped fix; its ORM dependency must not leak into the new
application contracts or route annotations. This is not a claim that all catalog orchestration has
already moved into application use cases.

## Phase 1: capture the regressions

Primary files: `tests/test_solid_boundaries.py`, `tests/test_pipeline_tasks.py`,
`tests/test_connector_registry.py`, and `tests/test_ui_route_boundaries.py`.

1. Add an isolated, non-test lifecycle scenario with an app-owned SQLite database and registry that
   is intentionally different from the default registry. Verify the actual recovery worker sees
   both owned resources. Do not let global registry initialization make the scenario accidentally
   pass. Use temporary databases and a synchronized event for bounded startup/shutdown checks.
2. Add a writer-policy test using real `Settings`, rather than a `SimpleNamespace` cast to `Settings`.
   Exercise the same binding mechanism as request middleware. A disabled PostgreSQL writer must
   remain disabled for implicit and explicit lookups, including through `asyncio.to_thread`.
3. Add missing-setting and non-boolean-setting cases with `writer_enabled=True` as the provider's
   capability default. These must fail safely rather than return enabled.
4. Add an import rule for the three migrated UI modules that rejects imports of
   `app.infrastructure`, including ordinary, type-only, relative, and function-local forms. Add
   corresponding framework/ORM/infrastructure exclusions for the new application catalog package.

Gate: these tests expose the current defects before production code changes. Keep existing
behavior-based pipeline, catalog, security, and provider tests as the compatibility baseline.

## Phase 2: bind registry and settings as one context

Primary files: `app/connectors/registry.py`, `app/database.py`, `app/main.py`, and `app/bootstrap.py`.

1. Add a context helper that pairs the registry with its settings and resets both tokens in a
   `finally` block. Reuse `registry_context` semantics where appropriate; do not introduce a second
   inconsistent settings resolution rule.
2. Change request middleware to bind the app's registry and active settings together, alongside its
   database. Put context cleanup around the entire downstream request and response processing so
   success, handler errors, cookie/header errors, and cancellation all reset the tokens.
3. Bind the actual app settings for the legacy global app too. Preserve startup connector loading
   and the compatible `app.main:app` entrypoint.
4. Keep explicit settings arguments authoritative. Inside a composed context, implicit policy uses
   its settings. Retain global settings lookup only in a named legacy entrypoint path; an isolated
   production execution bundle must never silently fill missing resources from globals.
5. Add nested-context and error-path tests proving that app A's bindings are restored after app B
   or a failed operation exits. Confirm independent requests and `asyncio.to_thread` inherit the
   correct values without mutating process-wide settings.

Gate: implicit and explicit writer results agree for two apps with opposing flags, and bindings are
restored after exceptions.

## Phase 3: carry owned dependencies through all runtime paths

Primary files: `app/infrastructure/runtime/*`, `app/bootstrap.py`, `app/main.py`,
`app/services/pipeline_tasks.py`, and `app/ui/routes/pipeline_runs.py`.

1. Construct the execution bundle in composition and expose a narrow dispatch interface to routes.
   Initialize its per-app lock and shutdown event once per lifecycle. Keep the SQLite file lock for
   coordination between processes using the same database.
2. Pass the bundle into `run_background_runtime`. Bind its registry/settings for the supervisor's
   lifetime and explicitly pass its session factory into recovery and retention.
3. Make immediate response jobs capture the same bundle at enqueue time. Execute each job inside
   its owned context, even after the scheduling request has finished. Do not rely on incidental
   `BackgroundTasks` context inheritance to choose resources.
4. Ensure worker credential and transfer operations resolve providers under that context. Continue
   passing the same factory into `LeaseKeeper`; a new `threading.Thread` does not automatically
   inherit context variables. Keep lease sessions independent from the worker's session.
5. Use the same wiring for the global app. If old task function signatures remain for compatibility,
   translate them once in a legacy adapter; do not mix a passed session factory with an unrelated
   global registry. Update internal tests asserting old task argument tuples to assert captured
   ownership and behavior instead.
6. On shutdown, signal the owned event, await its supervisor, and dispose its database only after
   owned work stops. Closing one app must not reset another app's registry, emulator, event, or lock.

Gate: an actual queued transfer recovers through the composed lifecycle, uses that app's fake remote
state, and persists success in its database. Also test a deferred immediate job after request context
reset, expired-lease recovery, retained lease fencing, retention targeting, and independent shutdown.
Keep existing cancellation, uncertain-publish, retry, timeout, and abort behavior unchanged.

## Phase 4: validate declared writer settings and fail closed

Primary files: `app/connectors/base.py`, `app/connectors/registry.py`, `app/bootstrap.py`,
and built-in provider capability declarations.

1. Add registry specification validation against the actual settings object during composition.
   A nonempty `writer_setting` must name a supported boolean configuration field. Reject missing
   fields, blank names, and fields with unrelated types. Errors identify the provider and setting
   name without printing configuration contents or credentials.
2. Remove `getattr(settings, name, capabilities.writer_enabled)` for declared flags. Runtime lookup
   requires an existing boolean; malformed values are rejected or denied before connector I/O.
   Do not coerce strings such as `"false"` through `bool(...)`.
3. Keep `writer_setting=None` as an explicit capability-only policy. Preserve demo-mode use of the
   declared capability default, while still validating that declarations are structurally valid
   during composition. Keep fake/live built-in flag names identical.
4. Preserve supported extension behavior: a test provider can declare an existing boolean setting,
   or a legitimate configuration extension can define a new boolean field. A fabricated namespace
   attribute is not proof that the production configuration supports that flag.
5. Update the current custom-provider test to use production-shaped settings and verify both enabled
   and disabled results without central provider-name switches.

Gate: typo/missing/type errors fail startup before readiness or worker scheduling; disabled writers
remain disabled in save, enqueue, provisioning, preview, and execution. Existing demo tests that
exercise real-mode flags using fake connectors continue to pass.

## Phase 5: compose catalog adapters outside the UI

Primary files: new application catalog contracts, new infrastructure catalog/session adapters,
`app/bootstrap.py`, `app/main.py`, `app/ui/routes/__init__.py`, `pipeline_context.py`,
`pipeline.py`, `pipeline_save.py`, and `pipeline_preview.py`.

1. Compose the SQLAlchemy cache, credential resolver, provisioned-dataset queries, and provider
   resolvers together in infrastructure. Construct each cache/resolver inside the operation's own
   session; do not share session-bound instances between threads or requests.
2. Inject a typed pipeline route dependency bundle from `main.py` through `register_routes` and
   `register_pipeline_routes`. Both app entrypoints use this path. Missing dependencies fail at
   registration/startup rather than causing routes to instantiate fallback implementations.
3. Replace the concrete `UserCatalog` annotations in the affected pipeline render/preview helpers
   with `CatalogAccess`. Replace UI infrastructure imports and construction with the injected
   operation runner. Preserve operation result values and existing catalog error messages.
4. In `_pipeline_body_in_thread`, run catalog-backed rendering within the runner's callback so the
   session remains open for catalog operations and closes on rendering failure. Broader removal of
   ORM pipeline models from presentation remains a separately tracked R7 task.
5. In `save_pipeline_in_thread`, move the session/catalog-dependent branch resolution and PostgreSQL
   upsert-key checks into a composed authoring operation. Expose a value-only save command and return
   a pipeline summary/ID to the route. Preserve the existing authoritative persistence operation,
   owner checks, audit behavior, and transaction boundaries rather than committing through a second
   independent catalog session. Keep legacy command APIs outside this migrated route compatible.
6. Convert the HTTP request into `ActorContext`/`RequestMetadata` at the UI boundary, using the app's
   trusted-client settings. The audit layer already supports `RequestMetadata`; update the affected
   credential service/adapter annotations to accept it without carrying a web request inward.
7. Keep the `CredentialResolver` owner check and `purpose="catalog"` attribution. Test cross-owner
   denial before decryption, correct branch selection, cache isolation/invalidation, and absence of
   plaintext in results, HTML, cache, events, and logs. Do not turn an audit purpose string into a
   claim of additional access authorization unless a corresponding policy is actually implemented.
8. Confine remaining legacy `UserCatalog` default resolution to an explicitly documented legacy
   adapter/facade. New production composition must always provide its cache and credential resolver.

Gate: the three identified UI modules have zero infrastructure imports, production callers use the
new contracts, and page/preview/save/upsert/catalog/security behavior suites pass.

## Verification and delivery sequence

Deliver in this order: regression coverage; paired context plus execution wiring; strict writer
validation; catalog composition cutover; documentation and release checks. Each production change
lands with its own affected tests. Do not leave a new port unused by production callers.

| Check | Evidence required |
|---|---|
| App/runtime isolation | Distinct databases, settings, registries, and fake remote contents; recovery and deferred dispatch target the correct app; closing A leaves B usable. |
| Context cleanup | Nested bindings, concurrent requests, exceptions, and cancellation restore the prior context. |
| Writer gating | Implicit/explicit parity; malformed declarations rejected; no provider I/O after denial; demo/live built-in policy preserved. |
| Catalog ownership | Cross-owner attempts denied; owner/purpose audit facts preserved; sessions close; no plaintext returned or cached. |
| Presentation compatibility | `/pipeline`, preview fragments/OOB targets, saved-pipeline reload, Foundry branches, CSV sources, and PostgreSQL upsert/save outcomes preserved. |
| Architecture | AST rules cover ordinary, relative, type-only, and function-local imports for migrated files; legacy exceptions are named and do not grow. |

Run affected suites during each phase. At completion run `make check`, `make hedron-build`, and
`git diff --check`. Retain the existing 80% coverage requirement and demo-app checks. Record the actual
pass counts, coverage, and environment-dependent omissions; default tests remain offline. Schema
validation in lifecycle integration tests should use genuinely migrated temporary databases rather
than replacing the schema check as the final acceptance evidence. Live-provider and configured
Workbench/Connect deployment qualification remain their existing external gates.

Update `docs/architecture.md`, the main SOLID plan, and the plan index to distinguish implemented
behavior from remaining R7–R10 work. Correct the current production-complete claim only when all
four findings have outcome-based acceptance evidence.

## Risks and rollback

The highest risks are losing context during dispatch, sharing ORM sessions across threads, and
changing save/cache/audit commit ordering. Keep sessions operation-scoped, capture execution
dependencies explicitly, and preserve existing short commits and independent heartbeat sessions.
Strict writer validation intentionally changes malformed configuration from permissive fallback to
startup failure; error messages must make the correction clear.

No schema or serialized-format change is planned. Revert a failed code cutover after stopping owned
workers through the supported shutdown path; preserve queued runs and let the retained lease and
reconciliation rules handle interruption. Do not re-enable permissive writer fallback as a rollback
shortcut. A code rollback cannot undo a committed remote destination write.
