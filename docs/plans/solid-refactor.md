# Major SOLID refactor plan

Status: first and second milestone changes and runtime remediation are implemented. The
[AnyIO cancellation propagation plan](solid-runtime-anyio-cancellation-remediation.md) records prior
runtime evidence. The [caller cancellation bookkeeping plan](solid-runtime-cancellation-bookkeeping-remediation.md)
records B1 acceptance. The [full-refactor remediation](solid-full-refactor-review-remediation.md)
and [drain-failure remediation](solid-runtime-drain-failure-remediation.md) record runtime and
failed-startup acceptance evidence.
Compatibility adapters remain for the unfinished migration.
Reviewed against the local codebase on 2026-09-16. Scope: the
Data Mover application, its tests, and architecture documentation.

## Objective and scope

Make changes to pipeline policy, providers, persistence, identity, and presentation independently
reviewable and testable. Preserve the current FastAPI/Hedron/HTMX product, CLI, configuration
contract, database schema, saved definitions, and in-process execution model throughout migration.

Use an incremental modular monolith with explicit application boundaries and infrastructure
adapters. Start with pipeline save/enqueue as a vertical slice, then migrate execution, catalogs,
credentials, identity, email, and the remaining UI. A directory move alone does not complete a slice.

This refactor does not introduce microservices, a public REST API, a new queue, a DI framework,
calendar scheduling, transformation graphs, or an ETL framework dependency. Those features remain
separate work described in [data-pipelines.md](../data-pipelines.md) and the
[ETL integration note](etl-integration-note.md). Keep suitable extension points without implementing
future product behavior.

## Findings from the current code

The application contains 103 Python files and approximately 24,540 Python lines, including blanks
and comments. These are scope indicators, not measures of quality. Static inspection finds
HTTP/framework imports in 16 of 28 service files and ORM/database imports in 21; the framework count
includes type-only imports and the HedronPosit URL helper, so it is not a count of runtime violations.

| Priority | Evidence | Consequence and intended change |
|---|---|---|
| P0 | `app/application/pipelines.py` commands carry `User`, `PipelineDefinition`, `Session`, and `Request`; methods forward to service functions | The boundary does not yet isolate business behavior. Replace ORM/request inputs with IDs, values, and request metadata; application use cases own orchestration. |
| P0 | `app/database.py` constructs settings, engine, and session factory during import; `app/main.py` constructs a global app; registry uses `_DEFAULT_REGISTRY`; tasks use a module lock | Independent app instances cannot fully own their dependencies. Introduce explicit factories and a per-app runtime composition root. |
| P0 | `app/services/pipeline_runs.py` combines enqueue, queries, leases, lifecycle, events, and filesystem retention; mutations commit internally | Persistence and execution policies have multiple reasons to change. Separate queries, guarded run operations, and retention while documenting every transaction boundary. |
| P0 | `app/services/transfer_engine.py` (494 lines) takes a session and ORM run, resolves adapters, validates policy, writes counters/events, handles publish uncertainty, and builds manifests | Transfer behavior cannot be exercised independently of persistence hooks. Inject run recording and control ports; retain one clear execution coordinator. |
| P1 | `app/ui/routes/pipeline.py` (3,940 lines) combines selection/swap policy, provider catalogs, previews, forms, saved cards, page composition, and run results | Route decomposition has left the rendering/orchestration monolith intact. Extract query models, selection policy, presenters, and components. |
| P1 | `app/services/pipelines.py` branches on provider IDs and consumes catalog UI sentinels; `app/ui/params.py`, secret catalog, writer flags, and registration repeat provider knowledge | Adding a provider touches unrelated policy/UI code. Register provider specifications and authoring strategies; translate legacy form sentinels at the HTTP boundary. |
| P1 | `UserCatalog` combines credential acquisition, remote browsing, branch rules, saved datasets, cache serialization, and commits | Split owner-authorized catalog orchestration from cache and provider metadata adapters. |
| P1 | `LeaseKeeper` imports global `SessionLocal`; worker packs CSV content into a credential-shaped dictionary | Inject independent lease sessions and distinguish upload source material from provider credentials. |
| P1 | `FoundryClient`/`FoundryConnector` share an 864-line file; PostgreSQL connector is 702 lines; emulator is 967 lines | Separate transport, extraction, and destination load resources by responsibility without changing remote protocols or atomic write behavior. |
| P2 | `app/dependencies.py` resolves tokens, queries sessions, rotates refresh tokens, enforces HTTP auth/CSRF, and issues cookies; identity services accept requests | Move authentication decisions into application use cases; retain cookies, trusted-client resolution, and HTTP enforcement in adapters. |
| P2 | `app/services/mailer.py` combines FastAPI scheduling, outbox SQL, delivery claims/retries, SMTP, and HTML rendering | Separate outbox orchestration, persistence, transport, and web background scheduling. |
| P2 | `tests/conftest.py` rebinds process-wide engine/settings; application command tests primarily verify forwarding; route-boundary test prohibits one old import | Build instance-scoped fixtures and test domain outcomes, adapter contracts, and architectural dependency rules. |

Preserve the good seams: narrow connector protocols and replaceable `ConnectorRegistry` instances,
injected route/writer policies, clock/sleeper ports, `RequestMetadata`, secret catalog/validator/crypto
components, run-status presenter, and separated pipeline action registrars. Strengthen these rather
than replace them with a parallel framework. `pipeline_state.py` separates lifecycle calculations
from database calls, but still imports and mutates an ORM model; finish that separation.

## Target boundaries

```text
app/
  domain/
    pipelines/          Locators, snapshots, lifecycle values, selection/write rules
    identity/           Account/session values and identity policies
    credentials/        Provider credential specifications and validation rules
    errors.py           Business failures, independent of HTTP status codes
  application/
    pipelines/          Save, enqueue, cancel, reconcile, query, execute use cases
    identity/           Login, refresh, register, invite, reset, administer accounts
    credentials/        Save, test, delete, authorized credential acquisition
    catalogs/           Owner-authorized catalog queries and dataset provisioning
    email/              Outbox delivery/retry orchestration
    ports/              Consumer-specific persistence, provider, runtime ports
    dto/                Commands, results, query projections, request metadata
  infrastructure/
    persistence/        SQLAlchemy models, mappings, repositories, transactions
    runtime/            Lease keeper, supervisor, locks, background dispatch, retention
    security/           Encryption, token and password implementations
    email/              SMTP/console transports, external-link composition
  connectors/           Existing provider adapters, progressively split internally
  ui/
    routes/             HTTP validation, auth/CSRF, use-case calls, response mapping
    presenters/         DTO-to-view transformations
    components/         Pure Hedron component composition
  bootstrap.py          Explicit settings/database/provider/runtime composition
  main.py               create_app(...) and compatible app.main:app entrypoint
  cli.py                Existing commands using the same composition factories
```

Dependency direction: UI and infrastructure depend on application contracts; application depends on
domain values and its own ports; domain depends on ordinary Python and suitable value-validation
libraries. Domain/application must not import FastAPI, Starlette, Hedron, SQLAlchemy, global settings,
or concrete connector implementations. Composition is the only place that selects implementations.

Keep `app/connectors` as the provider adapter package. Move reusable transfer contracts and locator
values inward with temporary re-exports from existing import paths. Pydantic value models and Polars
batches are acceptable deliberate dependencies; replacing them is outside scope. Separate display
labels/icons from execution capability facts. Split ORM modules only when migrating a feature;
retain shared SQLAlchemy metadata and Alembic model discovery.

Use explicit constructors and small dataclasses for dependency bundles. Do not introduce a universal
service locator, generic CRUD repository, event bus, class for every function, or deeply nested
inheritance. Pure policy can remain a function. A protocol needs a meaningful consumer boundary or
an independently replaceable implementation.

## Applying SOLID concretely

| Principle | Refactor decision | Evidence of success |
|---|---|---|
| Single responsibility | Separate policy, use-case orchestration, persistence, runtime coordination, transport, and rendering | A policy test needs neither a request nor a database; rendering a supplied view model does not browse providers. |
| Open/closed | Register a provider specification containing capabilities, credential schema/validation, authoring strategy, adapter factories, and operator writer policy | A test-only provider can participate in save/enqueue/catalog/execute without editing central provider conditionals or form literals. Registration remains explicit and audited. |
| Liskov substitution | Define behavioral contracts for narrow connector roles and destination load resources | Fake and live adapters satisfy the same relevant paging, batch, abort, finalize, and error contracts; provider limitations remain explicit. |
| Interface segregation | Retain separate browser/schema/count/source/writer/provisioner roles; add small run/query/credential ports | A write-only destination still executes successfully; unavailable optional inspection never becomes a hidden prerequisite except where a selected policy requires it. |
| Dependency inversion | Use cases receive repository/control/policy/clock dependencies; adapters implement those contracts | App instances and use-case tests replace dependencies without monkeypatching module globals. |

Provider extensibility does not imply all providers can implement every locator or write mode. Each
registered authoring strategy builds validated domain values; the service uses capabilities and
strategy dispatch rather than a growing provider-ID switch. Keep provider-specific rules local.
Do not permit an unknown provider to inherit a permissive writer default. Preserve the current
configuration aliases and explicit operator gates for built-ins.

## Core contracts and transaction ownership

Define ports from actual use-case needs. Initial contracts should cover:

- `PipelineRepository`: owner-scoped lookup, save, and definition query projections.
- `PipelineUnitOfWork`: pipeline changes, audit rows, and enqueue rows sharing one transaction.
- `RunQueries`: persisted status, latest-run summaries, and event pagination as DTOs.
- `RunStore`: claim, renew, cancel, transition, progress, and completion using a `RunLease` value.
- `RunControl`: fresh cancellation checks and lease-loss state; no cached ORM ownership decisions.
- `CredentialAccess`: owner/purpose-authorized acquisition, with existing key-use accounting/audit.
- `UploadSourceAccess`: owner-scoped content and validated CSV inspection, separate from credentials.
- Narrow provider resolvers, `CatalogCache`, `DatasetRepository`, and email transport/outbox contracts
  as their slices are migrated. Reuse the existing callable clock/sleeper ports.

Commands accept actor IDs and normalized input values, not ORM users or web requests. Actor
permissions, ownership, connection readiness, and write policy are rechecked by the use case;
client-supplied availability or a DTO's claimed roles are not authorization. Use cases return IDs
or DTOs. Resolve trusted source IP/request ID into `RequestMetadata` in the HTTP adapter before
crossing a thread boundary. CLI calls supply explicit non-HTTP context.

There are two transaction shapes, not one transaction spanning a transfer:

1. **Short business commands:** save/enqueue and related audit/event rows commit together through the
   unit of work. Session rotation, token consumption, key-use reservation, and email enqueue must
   preserve their existing atomicity and replay protection when their slices migrate.
2. **Long execution:** `RunStore` operations use short, explicit transactions. Claim commits before
   provider access; lease renewal uses an independent session; progress commits release locks before
   remote I/O. Provider load transactions remain owned by the destination adapter, independently of
   the application database. No network call is hidden inside a database transaction abstraction.

Before moving a commit, record the current behavior and its failure window. Existing counter/event
operations may commit separately; preserve that behavior first, then make any deliberate atomic
progress/event improvement in a separate change with failure tests. Removing internal commits
mechanically would break lease renewal, SQLite lock behavior, and durable recovery.

Destination finalization and application completion cannot be committed atomically together. Keep
`publish_uncertain` and reconciliation outcomes for ambiguous remote publishes or missing local
completion acknowledgement. Never describe this refactor as providing exactly-once remote writes.

## Ordered implementation backlog

Each row is a reviewable PR or small series. PRs should migrate callers as well as introduce seams.
Dates are intentionally uncommitted; size indicates relative implementation risk, not a promise.

| Order | Work and primary files | Dependencies / size | Acceptance gate |
|---|---|---|---|
| R0 | Characterize boundaries and transactions; establish import-rules ratchet and baseline (`tests`, `docs/architecture.md`) | First / M | Record route/fragment, CLI/env, snapshot, schema, security, and DB contracts; add only missing failure/concurrency behavior coverage. New packages have zero forbidden imports. |
| R1 | App/database/runtime factories (`main.py`, `database.py`, `bootstrap.py`, registry, fixture setup) | R0 / L | Two apps with different databases/registries/settings coexist in one process. Closing one does not dispose the other's engine or clear its emulator. Preserve `app.main:app`, CLI, Posit mounts, startup schema checks, and shutdown. |
| R2 | Extract domain values and make pipeline save/enqueue real use cases (`application/pipelines.py`, `services/pipelines.py`, locators/state, pipeline save/run routes) | R1 / L | Business decisions tested without ORM/HTTP; save/enqueue transactions preserve owner-scoped idempotency and audit/events; existing v3 definitions and legacy parsing behave identically. |
| R3 | Register provider authoring/credential specifications and policies (`registry.py`, secret catalog, pipeline rules, `ui/params.py`) | R2 / M–L | Test-only provider flows through authoring with no central provider-ID edits; unsupported roles/writers fail before I/O. Legacy forms normalize to explicit draft values. |
| R4 | Split run persistence/query/retention (`pipeline_runs.py`, `pipeline_state.py`, models, runtime retention) | R2 / L | Cross-session claim/renew/event sequencing/idempotency and stale-lease rejection pass on SQLite and PostgreSQL. Run DTOs avoid detached model/lazy-load behavior. Retention preserves existing cutoffs. |
| R5 | Isolate execution and runtime (`transfer_engine.py`, `worker.py`, `pipeline_tasks.py`) | R3, R4 / XL | Inject run recording/control and source material; lease keeper has its own injected session factory. Cancel, timeout, streaming limits, abort, lost lease, shutdown, restart, and uncertain publish retain current outcomes. |
| R6 | Separate credential/catalog/provisioning orchestration (`secrets.py`, `catalogs.py`, `csv_uploads.py`, `foundry_datasets.py`) | R3, R4 / L | Owner/purpose access, encryption, atomic key-use limits, cache invalidation, branch selection, upload checksums, and non-reveal behavior preserved. View rendering never decrypts credentials. |
| R7 | Decompose pipeline presentation and remaining HTTP orchestration (`ui/routes/pipeline.py`, `pipeline_preview.py`, presenters/components, `layout.py`) | R2–R6 / XL | Thin HTTP registrars consume use-case/query DTOs. Pure selection/swap rules and pure renderers are independently tested; HTTP paths, fragments, polling, OOB targets, CSRF, and responsive shell remain compatible. |
| R8 | Migrate identity, account administration, and email (`dependencies.py`, identity services, rate limit, mailer, auth/admin/security routes) | R1, R2 pattern proven / XL | Session rotation/revocation/security versions, trusted-header handling, single-use tokens, generic failure messages, shared limits, outbox claims/retries, and link/cookie mounts pass their behavior suites. |
| R9 | Split provider adapter internals and emulator (`foundry.py`, `postgres.py`, `fake.py`) | R3, R5 contracts / L | Transport/extractor/load resources have explicit lifetimes; driver/HTTP protocol suites and fake/live parity pass. PostgreSQL staging/COPY/finalize remain one provider transaction. |
| R10 | Remove compatibility facades/globals, enforce complete boundaries, update contributor docs | All migrated slices / M | Production callers use new boundaries; no deprecated internal imports or global fallback resolution remain. Full quality/release checks pass; old saved data opens and runs. |

R7 can begin with mechanical pure-component extraction after R0, but is complete only when it renders
DTOs without catalog/database work. R8 and R9 can follow their prerequisites independently; the
highest-risk transaction/runtime changes should remain serialized for review. Avoid combining a
Hedron upgrade, new feature, database migration, or changed retry policy with these PRs.

## Tests and evidence

Keep current behavior suites as the migration safety net. Reorganize tests only when a slice is
migrated; keep adapter tests against real persistence rather than replacing concurrency coverage
with mocks.

| Contract | Existing evidence to retain | Additional evidence needed |
|---|---|---|
| Pipeline policy/commands | `test_pipelines.py`, `test_application_pipelines.py`, `test_pipeline_state.py` | Outcome-based use-case tests with small in-memory ports; move beyond command forwarding assertions. |
| Durable runs/concurrency | `test_pipeline_runs.py`, `test_pipeline_tasks.py`, `test_pipeline_metadata.py` | Same guarded operation contracts on both DB dialects; concurrent claim/expiry races; app-instance isolation and deterministic runtime stop/recovery. |
| Transfers/provider substitution | `test_transfer_engine.py`, `test_connector_runtime.py`, `test_connectors_domain.py`, `test_connection_emulation.py`, `test_connector_registry.py` | Shared role behavior tests, failed finalize/abort, resource closure, post-publish local failure, and registering an extra provider. |
| Remote protocols | `test_foundry_http.py`, `test_provider_protocol.py`, `test_postgres_connector.py`, `test_postgres_sql.py` | Keep sanitized HTTP simulators and PostgreSQL staging/rollback/ambiguous-commit evidence; live-provider tests remain opt-in. |
| Credentials/catalogs/CSV | `test_secrets.py`, `test_secret_components.py`, `test_user_catalog.py`, `test_foundry_http.py`, `test_pipelines.py` | Cross-owner port contract tests, no plaintext in DTO/cache/events/logs, key accounting, and replacement/deletion invalidation. |
| Identity/email | `test_auth_security.py`, `test_registration.py`, `test_federated.py`, `test_mailer.py`, `test_email_config.py` | Outcome/transaction tests through new use cases; preserve failure-path audit and replay protection. |
| HTML/deployment/CLI | `test_web_flows.py`, `test_ui_interactions.py`, `test_ui_shell.py`, `test_ui_route_boundaries.py`, `test_links.py`, `test_connect_native_cookies.py`, `test_integration_system.py`, `test_packaging.py` | Query/render separation checks, route/fragment contract comparison, factory entrypoint compatibility, migration discovery, mounted redirects/cookies. |

Add import rules covering ordinary, type-only, and function-local imports: domain cannot depend on
outer layers; application cannot depend on UI/framework/ORM/concrete infrastructure; UI cannot
import persistence or instantiate concrete connectors; connectors cannot import UI/application
use cases. Keep an explicit, shrinking legacy exception list during migration, with slice ownership.
Do not enforce arbitrary file-length or class-count limits as an architectural acceptance test.

For each implementation PR, run the affected behavior suites and Ruff/type checks. At slice exit,
run `make check` (including the existing 80% application coverage gate and demo checks), then
`make hedron-build` before release. PostgreSQL coverage needs available server binaries; skipped
tests are not proof of database parity. Workbench/Connect and live-provider gates require their
configured environments; default tests must remain offline. Recheck the unchanged production
security gate in `SECURITY.md` when a security slice lands.

Local planning checks on 2026-09-16:

- 35 passed: application pipeline commands, connector registry, secret components, pipeline state,
  transfer engine, and UI route boundaries.
- 78 passed: pipeline runs, saved pipelines, connector runtime, connection emulation, pipeline
  background tasks, and user catalogs.
- 3 passed: documentation tests. `git diff --check` also passed.

This is a sample of current behavior, not a full baseline, coverage measurement, PostgreSQL
qualification, or production release approval. R0 still establishes the complete implementation
baseline and records environment-dependent skips.

Implementation validation after the first milestone: `make check` passes Ruff, formatting,
basedpyright, Hedron checks, the Posit deployment matrix, **412 passed / 31 deselected** tests at
84.44% coverage, and **25 passed** demo-app tests. Hedron reports informational accessibility and
compatibility reminders only; no check failed.

## Migration, compatibility, and rollback

- Migrate one entrypoint-to-adapter slice at a time. Existing service functions/import paths may be
  temporary facades into the new implementation, with the old behavior retained until cutover.
  Keep one authoritative policy/implementation; do not create permanent old/new execution paths.
- Preserve serialized locator discriminators, snapshot versions, enum strings, table names, foreign
  keys, env aliases, CLI outputs relied upon by scripts, HTTP form names, and fragment identifiers.
  Moving ORM classes must not generate an unintended Alembic schema diff.
- Roll back an individual code cutover by reverting its PR after stopping active transfers through
  the supported shutdown process. Preserve durable runs; handle interrupted loads via existing
  lease/reconciliation rules. A code rollback cannot undo a committed destination write.
- If a discovered requirement needs schema or serialized-format changes, split it into a separately
  reviewed compatibility migration with old/new reader policy and its own rollback analysis.
- Track retired facades/imports in each PR; R10 deletes them once all callers and tests migrate.
  Do not keep globals as a silent fallback for missing dependency injection.

## Completion criteria

1. Domain/application have zero forbidden imports and no command/result exposes ORM objects,
   web requests, sessions, or plaintext credentials to presentation callers.
2. Independent app fixtures require no process-wide settings/engine/registry rebinding; every app
   owns its runtime lifecycle, locks, provider state, and database resources.
3. Save/enqueue/cancel/reconcile and identity commands have explicit authorization and transaction
   ownership; execution state operations retain fresh lease fencing and short commits.
4. Adding a registered provider changes its specification/adapters/tests and composition only,
   with no edits to central business/provider switch statements or provider form literals.
5. Pipeline selection is pure policy; presenters/components render supplied models without SQL,
   credential access, provider browsing, background scheduling, or business mutations.
6. Fake/live contracts preserve capabilities, bounded batches, atomic provider writes, resource
   cleanup, and reconciliation safety. Operator-gated writers remain authoritative.
7. Current saved definitions and DBs remain compatible; CLI, Posit deployment, UI interaction,
   auth, and security contracts pass. Architecture/contributor docs describe the implemented result.
8. Compatibility facades are retired and all required quality gates pass with skips and external
   qualification gaps explicitly recorded.

The first implementation milestone is complete: the isolated app factory, pipeline policy/use-case
boundary, provider specifications, catalog cache port, email transport port, injected lease session,
and persistence-neutral run state are present and covered by focused tests. The identity login path
now delegates through an application use case and SQLAlchemy gateway, the saved-pipeline form data
is rendered through a pure presenter, and request-bound database/connector contexts let composed app
instances use their own resources. Remaining work is deeper provider authoring-strategy extraction,
full route decomposition, and removal of the remaining compatibility service facades described in
R6–R10.

The second milestone and remediation have implemented credential/domain contracts, declared writer
settings, request policy using composed app settings, and page/save operation ports. The final D1–D5
runtime changes are recorded in the [drain/publication remediation plan](solid-runtime-drain-publication-remediation.md).
The [completion and generation isolation plan](solid-runtime-completion-isolation-remediation.md)
tracks C1–C6, the active acceptance backlog.
The [runtime/lifecycle remediation plan](solid-runtime-lifecycle-remediation.md), [completion plan](solid-remediation-completion.md),
and [follow-up plan](solid-remediation-follow-up.md) preserve earlier scope and evidence. Beyond that scoped remediation, deeper
catalog/provisioning migration, full pipeline presentation decomposition, provider transport
lifetime work, and compatibility-facade retirement remain tracked in R7–R10.
