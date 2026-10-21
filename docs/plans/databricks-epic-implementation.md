# Databricks epic implementation plan

**October 21, 2026 default update:** Provider visibility, browsing, and compute controls default to
enabled. Metadata visibility verification stays false until explicitly attested; the unspecified
writer follows all prerequisites. The default-off rollout described below records the original
plan. This configuration change does not add live acceptance evidence;
see the [operator guide](../providers/databricks.md).

**Status:** Local connector implementation underway; live acceptance and release remain blocked.\
**Prepared:** October 15, 2026.\
**Baseline:** `c5a52f4` on `main`; `git pull --ff-only` reported already up to date.\
**Scope authority:** [Databricks epic](../stories/databricks-connection-epic.md),
[57 tasks across eight stories](../stories/databricks-connection-tasks.md), and
[issue CSV](../../artifacts/jira/data-mover-databricks-connection-issues.csv).

This plan turns the existing backlog into ordered, reviewable implementation slices. The local
connector, encrypted connection flow, review bindings, rollout gates, durable compute attempts,
and publication recovery have now been implemented in this working tree. Tasks remain open until
their specified evidence is recorded; this plan does not assign external tracker IDs or claim live
Databricks acceptance.
Existing local pipeline-remediation planning changes are independent work and remain intact.

## Delivery outcome

An owner can save an encrypted PAT-authenticated workspace connection, select an existing SQL warehouse or
Unity Catalog-compatible all-purpose classic cluster, understand compute readiness, explicitly
authorize startup, and transfer reviewed batches to or from supported Unity Catalog base Delta
tables. Source and destination sensitivity policy must pass before staging. Completed runs explain
what was scanned, transformed, and committed; interrupted publication distinguishes unchanged,
committed, and uncertain outcomes.

Deliver the full first-release epic: both compute families, discovery, source reads, destination
creation/append/full replace, metadata guardrails, SSN scanning, Hash/Remove, recovery, UI, operator
documentation, screenshots, and release evidence. A provider combination or write mode may be
excluded only through the DBX-01 decision record with the reason and release effect recorded.
Delivering source support alone is an intermediate milestone, not completion of this plan.

Keep the epic's exclusions: interactive user OAuth and OAuth M2M, jobs compute, compute creation/resizing/shutdown,
legacy metastore, views and non-base/non-Delta objects, arbitrary SQL, nested/complex types,
CDC/incremental extraction, upsert/MERGE, external-storage credentials, new content detectors,
governance editing, and physical data purge.

## Repository findings and implementation boundaries

| Existing behavior | Evidence in the baseline | Required change |
| --- | --- | --- |
| App-owned connector registry, typed role protocols, runtime settings snapshots | `app/connectors/base.py`, `app/connectors/registry.py`, `app/bootstrap.py`, `app/infrastructure/runtime.py` | Add Databricks through the captured composition; introduce narrow compute/metadata/recovery ports where existing roles lack the needed contract. Avoid global clients or a parallel worker runtime. |
| Encrypted owner/provider credential bundle with validation and cache invalidation | `app/models.py::UserSecret`, `app/services/secrets.py`, `app/services/secret_catalog.py`, `app/services/secret_validation.py` | Add PAT and compute fields, explicit connection/principal generations, management-only validation, transport validation, and startup consent invalidation. Preserve one saved connection per owner/provider in this release. |
| Typed locators, policies, definition snapshots | `app/domain/locators.py`, `app/connectors/locators.py`, `app/services/pipelines.py` | Add qualified Delta identities and distinct proven write policies, update serialization and re-exports, and keep credentials out of definitions. |
| Value-free schema sensitivity markers | `app/domain/catalogs.py::ObjectSchema`, `app/services/catalogs.py` | Represent detector provenance, stable identity, metadata coverage, policy revision, and fingerprints explicitly; verify all required source and target scopes. |
| Existing SSN detector and action projection | `app/domain/pipelines/guardrails.py`, `app/services/guardrail_schema.py`, `app/services/transfer_engine.py` | Add `databricks_metadata`; generalize Foundry-specific findings/labels without changing legacy detector meaning. Bind Databricks decisions to the full connection/object/mapping context. |
| Source review key hashes provider, namespace and object (or CSV upload) | `guardrail_source_key` and its UI callers | Add a versioned Databricks review context including owner, workspace, connection/principal generation and stable table identity; include target identity and mapping for target findings. Names alone are insufficient. |
| Complete scan, bounded encrypted temporary spool, then transformed replay | `_EncryptedBatchSpool`, `execute_transfer` | Preserve this order and single-query consistency; add inspection-boundary refresh, destination policy enforcement, and interrupted resource cleanup. The in-memory spool key is not a durable resume contract. |
| Leased runs and conservative reconciliation status | `app/services/pipeline_runs.py`, `app/services/pipeline_state.py`, `app/worker.py` | Add durable startup, publication intent, provider operation evidence, target coordination and cleanup records. A review timestamp or a process-local load session is not proof of a provider commit. |
| Owner-scoped catalogs, feedback and diagnostics | `app/services/catalogs.py`, `app/infrastructure/persistence/catalog_cache.py`, `app/ui/routes`, `app/application/feedback.py` | Extend cache scope, safe error vocabulary, source/target review and compute UX; enforce flags outside UI as well. |

The existing guardrail acceptance audit records the prerequisite's repository implementation in
[open-guardrail-issue-acceptance.md](open-guardrail-issue-acceptance.md). Reuse and regress that work;
do not reopen the original epic as an unrelated refactor. Archived Advana examples and simulator
REST shapes are reference fixtures only; they do not supply a product connector or SQL contract.
The proposed Remediation tab is not a dependency: Databricks recovery must work with the existing
run review and reconciliation surfaces.

## Provider decisions that must precede supported implementation

DBX-01 is the critical first gate. Produce `docs/decisions/databricks-provider-contract.md` and a
sanitized probe record under `artifacts/databricks/`. Resolve these questions in an isolated,
authorized workspace with synthetic tables:

| Decision | Required evidence and release effect | Accountable role |
| --- | --- | --- |
| Deployments and transport | Exact allowed cloud/workspace patterns, private endpoints, DNS/network paths, CA profiles, redirects and credential destination checks. Do not infer government/Advana support from commercial AWS documentation. | Platform / Product |
| SQL/SDK compatibility | Tested connector/SDK versions and Python dependency compatibility; runtime/access modes, HTTP-path binding, scalar type mapping, bounded fetch strategy and query cancellation for each compute family. Record a conservative throughput/size envelope. | Engineering |
| Metadata completeness | Positive visibility/permission evidence for catalog, schema, table and complete columns/tag relations. Prove verified-empty versus permission-filtered/partial results; if that distinction cannot be established, the combination is blocked. | Engineering / Platform |
| Startup policy | Management validation sends no SQL/start request. Test driver-triggered startup and the stop-after-state-check race. Manual SQL use requires a proven no-start path or a query-only principal without startup rights; otherwise require explicit current startup consent or exclude that combination. | Engineering / Platform |
| Source consistency | One result across all batches while source data/schema change; provider filters/masks remain active. Record only safe query/version identifiers and demonstrate cancellation and fetch failure. | Engineering |
| Delta publication | Prove atomic absent-target create, append and full replace separately; exact output, governance preservation including tags, external writer/schema conflicts, private staging and lost-response reconciliation. No mode is enabled by analogy to PostgreSQL transactions. | Engineering / Platform |

Documentation checked October 15, 2026 supports the proposed connector direction: the
[Python SQL Connector](https://docs.databricks.com/aws/en/dev-tools/python-sql-connector) supports
all-purpose compute and warehouses, native parameters in 3.0+, and M2M through the SDK; jobs compute
is excluded. Pin the tested versions after the probes rather than assuming the minimum is sufficient.
Decide Arrow/CloudFetch and network behavior explicitly; fetch links must not enter retained evidence.

[COLUMN_TAGS](https://docs.databricks.com/aws/en/sql/language-manual/information-schema/column_tags)
is a provider contract to verify under the saved principal, not proof that empty results mean complete
coverage. The [CREATE TABLE reference](https://docs.databricks.com/aws/en/sql/language-manual/sql-ref-syntax-ddl-create-table-using)
documents REPLACE preservation of privileges, history, row filters and masks; this does not establish
every tag/schema/removal case or outside-writer guarantee needed here. Treat create-as-select,
insert-select and replace-as-select as probe candidates, not settled publication implementations.

External inputs are an isolated workspace, PAT identities with both positive and denied permission
profiles, eligible warehouse/classic compute, synthetic governed tables, private staging, network/CA
configuration and an approved startup test budget. Platform owns provision/access; Product owns
support exclusions and marker policy; Engineering owns probes and decision evidence. Their
availability has not been established in this planning pass. Missing live evidence leaves the
affected acceptance gate open. Offline fixtures may be prepared while waiting; do not mark DBX-01
complete or advertise provider support based on them.

## Target design and persisted contracts

Keep provider SDK/SQL details inside `app/connectors/databricks.py` until the provider contract is
settled; split it into auth, transport, compute, catalog/metadata, scalar mapping, source and
destination modules when the acceptance-backed boundaries are clear. The current implementation
is a single connector module and does not yet meet that modularity target.
Inject management and SQL clients plus clock/sleep/jitter for deterministic tests. Keep ownership,
authorization, durable coordination and outcome transitions in application/domain services with
SQLAlchemy adapters under infrastructure. UI handlers use those operations; they do not call the
SDK directly. Preserve compatibility exports and the SOLID/runtime boundaries.

Add the following typed contracts and additive Alembic migrations in the slices that need them.
Choose migration revision IDs at implementation time against the current head.

| Contract | Required data and invariants |
| --- | --- |
| Connection and consent generation | Reuse encrypted `UserSecret` storage; add non-secret connection/principal revisions and a binding for workspace, compute kind/ID, and startup policy. Secret replacement/deletion invalidates reviews, caches, tokens and outstanding authorization. Default manual; a new workspace/principal/compute clears consent. In-flight operations re-check revision before a new side effect. |
| Delta locator and review context | Qualified catalog/schema/table plus safely resolved workspace/stable object identity. An absent target binds to its qualified name and parent identity until atomic creation. Version the review context and snapshots; preserve existing definitions/actions unchanged. Reject replaced objects, stale contexts, exact self-copy and ambiguous identifiers. |
| Metadata coverage and policy | Explicit verified/denied/incomplete/unsupported result with safe reason, detector, schema/sensitivity/policy fingerprints and timestamp. Store fixed marker IDs, never arbitrary provider tag keys/values. Source/target findings carry exact column/mapping identity. Empty tags are accepted only with verified coverage. |
| Startup coordination and audit | Durable attempt ID, normalized workspace/compute key, initiating owner, authorization revision, original deadline, next poll, lease/fence, safe state/outcome. Separate cross-owner compute deduplication from private owner audit visibility; every caller must independently be authorized. |
| Target writer coordination | Database-backed target key and fenced lease across processes/instances, including an absent target. Lost ownership prevents new staging/publication. Reconcile any submitted operation before another conflicting Data Mover writer proceeds. A local mutex alone is insufficient. |
| Publication and cleanup ledger | Run/attempt/target identities, expected target identity/version, staging reference, safe operation ID, reviewed schema/fingerprints and phase: prepared, submitting, confirmed, unchanged or uncertain. Persist intent before remote mutation; recover stage ownership and cleanup even if the process dies immediately after a remote create. Keep cleanup state separate from commit outcome. |

Use transactions, unique constraints and compare-and-swap/fencing compatible with SQLite and
PostgreSQL. Do not hold a DB transaction or request session open during provider waits. Migrations
must preserve existing users, encrypted secrets, saved routes, actions and historical run outcomes.
Backfill versioned context conservatively; historical Databricks-like data must never silently
become an approved connection/review.

Retain the encrypted spool's ephemeral replay model; worker death before publication may require a
fresh full run, not partial replay or persisted plaintext keys. Verify temporary-file behavior on
supported hosts and recover any named leftovers using run ownership and leases. Persist remote
staging references and cleanup work. Cleanup/reconciliation SQL follows the same startup policy;
defer it with an operator-visible state if it would require unauthorized startup. Retain uncertain
commit evidence and required staging until reconciliation/retention rules allow cleanup.

## Ordered implementation slices

Each slice is a reviewable PR or a small sequence of PRs. Complete its tests and evidence before
closing the referenced tasks. Numeric task ranges below are inclusive. Task dependencies in the
backlog still apply; early scaffolding does not satisfy a dependent task's completion evidence.

| Slice | Task coverage | Deliverable and main touchpoints | Exit evidence |
| --- | --- | --- | --- |
| 1. Provider contract and probes | DBX-01-T01–T07 | Decision record, permission/support matrix, isolated probe harness and sanitized results; select SDK/SQL versions and publication/reconciliation strategy. | All six decision areas above resolved for every proposed supported combination, reviewed DBX-01-T07, explicit exclusions and live evidence links. |
| 2. Domain, connection and gated composition | DBX-02-T01–T07; scaffold DBX-08-T03 | Add package/dependencies (`pyproject.toml`, `requirements.txt`, `uv.lock`), Delta locators/policies/detector, credential catalog/validation, SDK PAT authentication, transport/compute-path binding, connection revisions and safe connection UI. Add default-off connector/writer flags in `app/config.py` and registration/save/enqueue/execution checks immediately. | Round trips and legacy migrations, owner isolation/CSRF, rotation/revocation, endpoint/redirect/TLS rejection, SDK redaction and management-only validation; disabled provider absent from authoring and denied through direct entry points. |
| 3. Compute lifecycle and durable readiness | DBX-03-T01–T08; DBX-08-T02 | Compute adapter, startup coordination/audit migrations and repository, bounded background observer composed through `ExecutionRuntime`, state/Start/progress/cancel controls, source and destination readiness gates. | Every epic state, separate warehouse API, start permission denial, duplicate/lost response, refresh/restart, rate limit, deadline, lease loss, cross-owner coalescing and stop-before-SQL races; synthetic live evidence per compute family. |
| 4. Unity Catalog discovery and identity | DBX-04-T01–T06 | Catalog/schema/table navigation, qualified Delta locator resolution, exact names/type inspection, metadata coverage, revised owner/workspace/generation cache scope in catalog service/persistence, blocked/empty/unsupported UI. | Complete bounded pages, cursor loops/limits, parent visibility, verified-empty/denied/partial results, cross-workspace names, object replacement, invalid names/views/types and stale caches; blocked metadata causes zero row reads. |
| 5. Metadata policy and bound review | DBX-05-T01–T07 | Versioned exact-case key/key-value policy, four-scope tag reads, source/target gates, safe marker mapping, fingerprints and inspection refresh; shared detector labels and bound action forms in preview/save/UI. | Unknown `class.*`, case-distinct keys, source/target/parent denial, mapping drift, object/schema/policy changes and stale form attacks; no sample-table queries/raw tags retained; zero blocked-source reads or blocked-target staging. |
| 6. Consistent extraction and transformed replay | DBX-06-T01–T07 | Single-query bounded source batches, explicit scalar/Polars type mapping, encrypted full-scan spool, saved action resolution, target Hash/Remove validation and projected schema; integrate with `transfer_engine`, catalog scans and authoring. | Multi-batch SSNs and tags, full scan limits/cancellation, type/null fidelity, saved/stale/missing-column policies, Remove precedence/all-column removal, no second extraction, no staging before approval, and transformed-only writes across the planned route matrix. |
| 7. Private staging and atomic Delta publication | DBX-07-T01–T05 | Approved private schema/ACL validation, bound value inserts, durable run staging ownership, create/append/replace modes proven in slice 1, target fencing and outside-writer policy; integrate writer prepare/write/finalize/abort contracts. | Atomic mode-specific output and row-count evidence, create race, append compatibility, removal/governance preservation, exact self-copy rejection, distinct-table copy, multi-process writer/lease loss and outside schema/write conflicts. |
| 8. Commit reconciliation and cleanup recovery | DBX-07-T06–T08 | Persisted publication intent and safe operation correlation, provider-evidence reconciler, startup-safe recovery scheduling, idempotent staging/spool/session cleanup and outcome integration with `pipeline_runs` and worker janitor. | Kill/drop response/cancel before and after submit/commit, missed local status persistence, restart recovery, cleanup denial/failure and retention. Confirmed commits survive cleanup errors; uncertain append cannot be replayed automatically. |
| 9. Complete owner/operator surfaces and rollout | DBX-08-T01; DBX-08-T03–T06 | Value-free run review, selected/applied actions and original/transformed schema, final flag enforcement, diagnostics dictionary/feedback, provider/runbook/FAQ/support matrix docs and annotated synthetic screenshots. | Cross-owner UI/API isolation, secret/value/tag/fetch-link sentinels across all outputs and error/debug paths, flag-off/source-only/writer combinations, accessible HTMX interactions and reproducible operator walkthrough. |
| 10. Release acceptance | DBX-08-T07 | Task-by-task acceptance record, all regressions and isolated live acceptance, operator setup/rollback procedure and reviewed enabled support matrix. | Evidence for all 57 tasks and epic release checklist; no enabled combination with missing live proof, no unsupported route advertised, both compute families and every accepted write mode exercised. |

Scaffold rollout enforcement in slice 2 so incomplete code remains inaccessible; close DBX-08-T03
only in slice 9 after its prerequisite verification tasks pass. Slice 6 can exercise writer
contracts against deterministic test doubles before the real writer in slice 7; repeat its route
acceptance against the real adapter in slices 7–10. This preserves the backlog's DBX-06-T07 →
DBX-07-T01 dependency without claiming that simulated writes prove live publication.

The critical path is provider proof → connection → compute → identity/coverage → metadata →
full scan/actions → staging/publication → reconciliation → release. Documentation, fault fixtures
and UI scaffolding can progress alongside their owning slice, but do not bypass dependency gates.

## Execution and recovery sequence

1. At save/enqueue and worker admission, enforce provider/writer flags, owner access, current
   connection revisions, supported route/policy and serialized context. Do not trust hidden UI fields.
2. Resolve credentials only within owned runtime operations. Check both selected computes with
   management APIs. Manual stopped compute produces a terminal blocked run with a retry instruction;
   opted-in eligible compute uses the durable bounded startup workflow. Never wait for a user inside
   a request or hold a transfer worker indefinitely. Query-backed counts, discovery, preview,
   readiness probes, extraction, writes, verification, cleanup and reconciliation all use the same
   approved SQL/startup gate.
3. Inspect source identity/schema plus catalog/schema/table/column coverage and policy; inspect
   existing destination or absent-target parent policy and mapping. Block table/parent markers,
   incomplete metadata and unsupported types before row extraction or destination work. Do not use a
   full `COUNT(*)` as management validation or scan completion evidence.
4. Extract one consistent query result, scan every bounded batch, and spool it encrypted. Enforce
   row/byte/time/spool limits and cancellation. After extraction, refresh source identity/schema and
   sensitivity/policy fingerprints and target review context; changed context requires renewed review.
5. Resolve existing and new decisions. Preserve applicable saved actions even when no longer flagged;
   reject missing guarded columns, stale/mismatched reviews, conflicting mappings and all-column
   removal. Remove wins; Hash uses the existing user/pipeline HMAC policy and supported string output.
   Validate append/removal/type/governance constraints against the transformed schema.
6. Acquire fenced target ownership, re-check current context and startup authorization, persist
   staging intent, and create private run-owned staging. Replay only the scanned spool through the
   reviewed transform/cast projection. Removed columns/values never enter staging; arbitrary tags,
   source values and credentials never enter SQL text/comments or diagnostics.
7. Re-check target identity/governance/schema or absent-target parents/absence immediately before
   atomic publication. Persist submission intent and operation identity before the provider call.
   Apply the proven outside-writer conflict strategy; metadata checks alone do not close that race.
8. Reconcile the operation using provider evidence. Record applied actions and committed destination
   output only after confirmation. Preserve `failed_needs_reconciliation`/uncertain impact when proof
   is insufficient; an operator acknowledgment alone cannot authorize a duplicate append. A DBX
   unresolved operation blocks conflicting target writes and unsafe new attempts.
9. Close/cancel provider resources, release leases and clean run-owned staging/spools. Pre-submission
   cancellation leaves the live target unchanged. After submission, cancellation does not prove
   rollback. Persist deferred cleanup independently and never stop shared compute.

Full replace must omit removed columns/current values while preserving accepted governance. Append
must block if a removed column remains in the existing target, and hash affects incoming rows only.
Review and docs disclose that Delta history/retained files can preserve prior values; operator
purge/retention work stays outside the epic. Refresh boundaries do not promise continuous tag
monitoring throughout commit.

## Verification and evidence plan

Extend `tests/simulators/` with Databricks management fixtures and an injected SQL cursor/session
harness; HTTP Statement Execution fixtures alone do not test the selected Python SQL path. Keep
simulators deterministic with fake time, cancellation, failures at every remote/local boundary,
and request/query recording to assert forbidden operations are absent.

Proposed focused suites are `test_databricks_contract.py`, `test_databricks_compute.py`,
`test_databricks_catalog.py`, `test_databricks_guardrails.py`, `test_databricks_source.py`,
`test_databricks_destination.py`, and `test_databricks_recovery.py`. Extend existing secret, domain,
user-catalog, transfer, run, UI, diagnostic/redaction, migration, SOLID and runtime tests rather than
duplicating their behavior. Exercise actual process death and separate DB sessions for durable
coordination; ordinary exceptions and a process-local fake lock are insufficient.

| Acceptance group | Required cases |
| --- | --- |
| Connection and transport | Ownership, encrypted storage/rotation/delete, refresh/revocation, endpoint/path mismatch, DNS/private routing policy, redirect credential leakage, CA/TLS failure and zero SQL/start during validation. |
| Compute | Every cluster state; warehouse-specific routing; manual/opt-in and stale consent; denied lifecycle permissions; simultaneous owners; ambiguous start; 429/backoff; deadline retained on restart; cancellation/lease loss; shutdown drain; stop-after-check race. |
| Metadata and review | Four scopes, exact-case rules, every `class.*`, verified empty versus filtered/malformed/partial results, new target, source/target/schema/policy/mapping drift, stable identity replacement, caches and forged/stale review payloads. |
| Scan and actions | Tagged and untagged SSNs after the first batch, null/decimal/numeric/date/timestamp/binary/string fidelity per accepted type matrix, provider filters/masks, one-query consistency, oversize rows, full limits, saved actions, remove/hash projection, incompatible target output and cancellation. |
| Publication and recovery | Empty and multi-batch outputs in each mode, staging privacy, native binding/quoting, concurrent create, external writers, fenced lease loss, drop response before/after commit, process death at intent/stage/submit/confirm/cleanup, accurate counts/outcomes and append duplicate prevention. |
| Product and diagnostics | Disabled/direct calls, source-only/writer capability consistency, safe precise feedback, no retained secrets/values/raw tags/fetch links, selected versus applied actions, owner-only audit/review, CSRF, keyboard/focus behavior and annotated screenshots of real synthetic states. |

Final route acceptance includes CSV, PostgreSQL, MSS and MCS-COP → Databricks;
Databricks → PostgreSQL, MSS and MCS-COP where destination capabilities permit; and distinct-table
Databricks → Databricks. CSV remains source-only. Test every enabled write mode with both compute
families, or record the narrower accepted combination explicitly in DBX-01 and the release matrix.

During implementation run the owning focused suites, then the affected shared suites. Before
release run `make check` (including the current coverage floor, static checks, Hedron/Posit checks,
full tests and demo tests), `make hedron-build`, and the existing secret-artifact verification
workflow. Add an explicit opt-in `live_databricks` marker/harness for isolated provider acceptance;
ordinary CI must not start compute or consume workspace credentials. Run live cases only with the
operator's configured test environment and startup authorization.

Maintain `docs/plans/databricks-acceptance.md` during implementation with one row per DBX task:
status, code/decision links, exact named tests, sanitized live evidence where required, supported
combination, unresolved gate/owner and release effect. Every probe record names versions, compute
kind/runtime/access mode, permission profile, operation/mode and result without credentials or
source values. Do not replace the existing task/CSV hierarchy or mark work complete based on a
proposed test filename.

## Rollout and definition of complete

Proposed settings are `pipeline_enable_databricks` and
`pipeline_enable_databricks_writer`, both false by default. Operator configuration also supplies
approved workspace/network/CA policy, private staging, marker policy version and resource limits.
Reject writer enablement without connector enablement. Freeze settings with the existing runtime
generation so one app instance cannot affect another.

1. Land migrations and inactive code; prove legacy providers and flag-off behavior unchanged.
2. Enable the connector only in the isolated acceptance environment; finish connection, compute,
   discovery, source and guardrail proof. Keep destination capability disabled until writer evidence.
3. Enable accepted writer modes in that environment; complete publication/reconciliation, outside
   writer, process-death and cleanup probes for every claimed combination.
4. Product/Engineering/Platform review the completed task evidence, supported matrix, operator
   setup, cost/retention disclosures and rollout checklist before deployment enablement.
5. Roll back by stopping new Databricks admission and disabling provider/writer flags. Keep durable
   evidence and authorized recovery/cleanup available; disabling new traffic does not erase an
   uncertain commit or make remote staging safe to discard. Do not automatically downgrade schema
   containing unresolved publication records or modify provider compute/governance.

The epic is complete only when all eight stories and 57 tasks have their specified evidence, every
enabled compute/route/write combination has isolated live acceptance, existing providers pass
regressions, docs/screenshots match behavior, and the epic release checklist is satisfied. A closed
flag, simulator success, or an unproven provider assumption is not completion evidence.

The current implementation record is [databricks-acceptance.md](databricks-acceptance.md). Local
code and focused tests are present, while DBX-01 provider decisions, broad regression checks,
operator guidance/screenshots, and live acceptance remain open. Keep both Databricks settings
disabled until every claimed combination has evidence and review.
