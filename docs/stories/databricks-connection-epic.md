# Proposed Epic: Databricks connections and sensitive-data guardrails

**Status:** Local implementation underway; provider acceptance and release remain open. No external epic issue has been assigned.
**Reviewed:** October 15, 2026.
**Depends on:** [Sensitive-data guardrails](CDO-4551-sensitive-data-guardrails-epic.md).

**October 21, 2026 runtime update:** This backlog records the original default-off rollout proposal.
The current provider, browsing, and compute controls default to enabled; metadata visibility
verification remains false until explicitly attested, and the writer follows its prerequisites.
See the [operator guide](../providers/databricks.md) for current behavior. Live acceptance and
release remain open.

[Issue CSV export](../../artifacts/jira/data-mover-databricks-connection-issues.csv) contains the epic,
eight stories, and 57 tasks in the previous epic's eight-column format. It uses local planning IDs
for hierarchy links; tracker keys and hour estimates have not been assigned.

## User outcome

A pipeline owner can save a Databricks workspace connection, select compute, check its readiness,
start an eligible stopped cluster, and move reviewed data to or from Unity Catalog Delta tables.
Sensitive metadata, unresolved column actions, incomplete scans, and unsupported routes block the
transfer before destination staging. Completed runs explain what was scanned, transformed, and
committed without displaying sensitive values.

## First-release scope

| Area | Included | Deferred |
| --- | --- | --- |
| Authentication | Workspace personal access token; encrypted owner-scoped bundle; replacement and revocation | Interactive user OAuth, OAuth M2M |
| Compute | Existing SQL warehouse or Unity Catalog-compatible all-purpose classic cluster | Jobs compute, notebook/job submission, compute creation, resizing, automatic shutdown |
| Objects | Unity Catalog base Delta tables; fully qualified catalog/schema/table identity | Legacy Hive metastore, views, materialized/streaming tables, foreign tables, arbitrary SQL |
| Sources | One consistent query result, bounded batches, full worker scan and encrypted spool | Unbounded extracts, incremental/CDC transfers |
| Destinations | Managed Delta table creation, append, full replace; modes enabled only after publication evidence | Upsert/MERGE, direct batch-by-batch writes to the live target, external storage credentials |
| Guardrails | Unity Catalog markers, existing SSN scan, Hash/Remove, value-free run review | New content detectors, tag editing, physical erasure/retention management |

Databricks classifies some views without applying column classification tags by default; views
therefore need a separate lineage/coverage design before support. Sources must retain provider row
filters and column masks. Data Mover reads only what its saved principal may see and never bypasses
those controls. [Classification reference][classification]

The proposed SQL data path is the supported Python SQL Connector; management APIs handle compute
state and startup. Verify its versions, Unity Catalog access modes, and publication strategy in
DBX-01. The Statement Execution API is an alternative requiring a separate compatibility decision;
its warehouse-specific route must not be assumed to work for all-purpose clusters. [Connector
reference][sql-connector]

## Compute readiness and startup

Connection validation uses management requests only. It reports authentication, compute visibility,
latest state, and the time checked without opening a SQL session. Successful authentication is not
proof that the cluster is running, catalog metadata is readable, or a transfer can succeed.

For all-purpose clusters, use this state contract:

| Provider state | Data Mover behavior |
| --- | --- |
| `RUNNING` | Check SQL readiness before discovery/extraction; do not equate state alone with a working session. |
| `TERMINATED` | Offer Start if authorized; default pipeline behavior is a terminal blocked result with a retry instruction. |
| `PENDING`, `RESTARTING` | Observe an existing startup with a deadline; do not send another start/restart request. |
| `RESIZING` | Check SQL readiness; do not resize or restart compute. |
| `TERMINATING` | Report stopping; do not race it with a start request. |
| `ERROR`, `UNKNOWN`, deleted/inaccessible | Block with a safe reason; require a fresh state check before retry. |

A Start action names the selected workspace and cluster, warns that compute may incur charges, and
requires the saved principal's start permission. Cluster start uses
`POST /api/2.1/clusters/start` for the configured terminated all-purpose cluster only. Query access
and startup permission are different: `CAN_ATTACH_TO` supports attachment, while `CAN_RESTART`
includes startup; broader `CAN_MANAGE` is not required merely to start. Never request cluster
creation or change compute settings. [Cluster API][cluster-api] · [Compute permissions][compute-acls]

Startup is bounded background work that does not hold an HTTP request or a transfer worker waiting
for a user. Proposed defaults: a 15-minute startup deadline and polling that backs off from 5 to
30 seconds with jitter and provider rate-limit handling. Persist the attempt and deadline so a page
refresh or worker restart does not issue a duplicate start or reset the timeout. Coalesce concurrent
attempts for the same workspace/compute; re-read provider state after an ambiguous start response.
Cancelling observation does not undo provider startup. Timeout or cancellation never stops shared
compute.

**Startup policy must cover SQL side effects too.** Default to manual startup. Owners may explicitly
enable startup for query-backed discovery, scans, and runs on the selected compute; changing the
workspace, principal, or compute clears that authorization. An opted-in run may start eligible
stopped compute and observe readiness within the same deadline. It must not restart, create, or
resize it. A driver may also trigger startup when SQL is submitted, so a state pre-check alone is
insufficient. DBX-01 must prove a no-autostart query path for manual mode or restrict it to a
query-only principal without startup rights; otherwise require explicit startup authorization
before SQL use. This includes the race where compute stops after the state check.

Warehouses use warehouse state/start operations, never the cluster-start API. Databricks can start
a stopped warehouse when a query or connection is submitted. The UI discloses this before enabling
SQL-backed actions; it must not promise a no-start mode that the provider cannot enforce. A denied
start produces remediation rather than a privilege or configuration change. [Warehouse
startup][warehouses] · [Driver-triggered cluster startup][driver-start]

Runs never stop compute afterward or change auto-termination. Report that the provider retains
lifecycle control.

## Sensitive-data contract

### Metadata coverage and policy

- Inspect catalog, schema, table, and column tag scopes separately. A configured sensitive
  catalog/schema/table marker blocks the source before rows are read; a column marker requires an
  action. Parent-scope blocking is Data Mover's conservative policy, not a claim that table tag
  queries automatically include inherited tags. Databricks tag inheritance has ABAC-specific
  semantics. [Tag reference][tags]
- Match provider tag keys exactly, preserving Databricks case sensitivity. Operator policy may
  match an exact key or key/value pair and maps it to a fixed safe marker ID. Every `class.*`
  column classification produces a finding, including a newly encountered classification.
  Arbitrary keys/values may be read transiently for matching but are never retained or displayed.
- Check the selected object's identity and complete schema, supported runtime, parent visibility,
  and each required metadata query. Tag relations are permission-filtered and `COLUMN_TAGS`
  requires Unity Catalog with Databricks SQL or Runtime 13.3 LTS or later. Empty tag rows alone
  cannot prove coverage: DBX-01 must define and test the permission/visibility checks that permit
  a verified-empty result. If completeness cannot be established, block. [Column tags][column-tags]
- Show “No configured markers found” only after successful coverage checks. This does not mean the
  source contains no sensitive data: classification coverage may be incomplete and the existing
  content detector recognizes SSN patterns only.
- Never query `system.data_classification.results`; its `samples` field can contain matched
  values. Do not enable classification, alter tags, or weaken provider policies. [Classification
  results schema][classification-results]

### Review and enforcement

Bind preview findings, caches, saved decisions, and run snapshots to owner, connection/workspace,
principal generation, catalog/schema/table and stable object identity. Target findings also bind to
the target identity and source-to-target column mapping. A similarly named table in another
workspace must not inherit the previous review. Changing the source invalidates preview results;
replacement of an object or incompatible schema change requires renewed review.

Refresh metadata at run preflight and again after extraction, before destination staging. A changed
schema, marker-policy version, or sensitivity fingerprint blocks for review. Use one consistent
source query result and publish the exact batches that were scanned from the bounded encrypted
spool; never re-read the source after scanning. Record a source version/query reference when safely
available. These checks define inspection boundaries; they do not promise continuous monitoring of
provider tag changes during a commit.

Apply existing saved actions to columns still present, even when a later scan does not flag them.
New findings require decisions; missing guarded columns, conflicting schema mapping, or removing
every output column blocks the run. Remove wins conflicting choices. Hash uses the existing
user/pipeline-scoped HMAC-SHA-256 policy and produces the supported output string type. Reject
unsupported nested/complex types before extraction rather than stringify them around the detector.
No destination staging occurs until the full scan and policy resolution have succeeded.

Inspect Databricks destination metadata too. A configured sensitive parent/table marker blocks
writes; tagged destination columns require reviewed handling after source-to-target mapping.
Absent metadata permissions do not become permission to write. Re-inspect existing target policy
before publication. For a new target, re-check parent scope and absence of the target before atomic
creation; a concurrent creation must cause a conflict rather than replace someone else's table.

**Remove is a logical output guarantee.** Full replace must omit the removed column and its current
values. Append is blocked when that column remains in an existing target, even if inserts would
leave it null. Hash transforms incoming rows; append does not retrospectively hash earlier target
rows. Replace must preserve existing grants, tags, row filters, and masks; if removing a column
would require unsetting a governed tag or changing a policy, block for operator remediation.
Data Mover never removes governance controls to make a write succeed.

Delta history and retained files can preserve previous values even after replacement or a
metadata-only column drop. Describe this limit in review and operations docs; physical purge
(`REORG`/`VACUUM` and retention decisions) remains provider-admin work outside this epic.
[Schema removal][schema-removal] · [Replace semantics][create-table]

## Destination publication and recovery

After guardrail approval, write transformed batches to a run-scoped staging table in an
operator-approved private schema. SQL values use native parameter binding; identifiers use
provider-aware quoting. Do not embed source rows in SQL text, query comments/tags, diagnostics, or
debug logs. Staging ACLs must prevent unrelated users from reading intermediate output; parent
policies must not silently weaken protection.

Publish using one tested atomic Delta operation for the selected mode. Do not assume SQL
`rollback()` reverses earlier committed statements or combine a drop-and-create sequence into an
atomic guarantee. Append requires compatible transformed columns; full replace must validate the
resulting schema and preservation of target governance. Reject a table-to-itself route using stable
identity, serialize Data Mover writers per target, and treat outside write/schema conflicts as
failures without silently overwriting concurrent changes. [Delta isolation][isolation]

Before publication, cancellation leaves the live target unchanged and cleanup removes only the
run's staging objects. Close/cancel provider cursors and release sessions; encrypted scan spools
must be removed on completion/failure and recovered safely after interrupted cleanup. Once commit
is submitted, a lost response, cancellation, or worker death can
leave the outcome uncertain. Preserve a safe run/operation identity, reconcile against provider
evidence, and report committed, unchanged, or uncertain accurately. Mark actions applied only with
confirmed commit evidence. Never replay an uncertain append automatically; cleanup failure must not
relabel a committed run as rolled back. Persist staging references and make interrupted cleanup
recoverable.

## Stories and acceptance evidence

DBX labels below are local planning references, not assigned tracker IDs. Each story includes its
acceptance criteria, prerequisites, and linked implementation tasks. The [task backlog](databricks-connection-tasks.md)
contains 57 proposed tasks with owner roles, dependencies, and completion evidence. Local connector
work is underway; see the [acceptance ledger](../plans/databricks-acceptance.md) for implementation
evidence and the live acceptance gates that remain.

### DBX-01 — Validate the provider contract

**As an operator**, I need a verified support matrix before the connector is advertised.

- Record an architecture decision covering supported clouds/workspace endpoints, private-network
  and CA requirements, connector/SDK versions, cluster runtime/access mode, scalar types, metadata
  completeness checks, and the manual/opt-in startup contract.
- Demonstrate source consistency and append/replace publication, governance preservation, and
  reconciliation in an isolated workspace using synthetic fixtures.
- Resolve unsupported combinations by excluding them explicitly; do not leave publication,
  metadata visibility, or no-autostart guarantees as implementation assumptions.

**Evidence:** Reviewed decision record plus provider probe results. Blocks release support claims.

**Tasks:** [Seven provider-contract tasks](databricks-connection-tasks.md#dbx-01-tasks).

### DBX-02 — Save and validate a workspace connection

**As a pipeline owner**, I can manage my own Databricks credentials and see what validation proves.

- Save workspace URL, workspace API token, compute type/ID, and startup policy with existing
  encrypted replace/delete behavior. Derive or verify the SQL HTTP path against that compute.
  Validate allowed HTTPS endpoints, TLS, and redirect behavior before sending credentials.
- Use PAT authentication through the supported SDK; reject expired/revoked credentials safely.
  Secrets are absent from pipeline definitions, URLs, logs, and diagnostics.
- Add provider capabilities, typed locators/write policies, serialization and UI support. Extend
  the guardrail detector model for `databricks_metadata`; keep existing Foundry/SSN records valid.
  Revalidation/review invalidation follows principal or connection changes.

**Prerequisite:** DBX-01. **Evidence:** Ownership, secret rotation, token refresh and migration tests.

**Tasks:** [Seven connection-lifecycle tasks](databricks-connection-tasks.md#dbx-02-tasks).

### DBX-03 — Check compute and start eligible clusters

**As a pipeline owner**, I can tell whether compute is ready and authorize its startup.

- Implement the state table, management-only validation, explicit Start and opted-in startup,
  with least-privilege enforcement and charge/lifecycle disclosure.
- Persist bounded startup attempts; deduplicate requests across refresh/retry/concurrent owners;
  handle rate limits, lost responses, timeouts, deleted compute, and readiness failures.
- A stopped cluster in manual mode blocks a run without an indefinite worker wait. Cancellation
  stops observation, not shared compute. Warehouse actions use the correct provider API.

**Prerequisites:** DBX-01, DBX-02. **Evidence:** All states and auto-start races in simulator tests;
isolated live startup/permission verification for each supported compute type.

**Tasks:** [Eight compute-readiness and startup tasks](databricks-connection-tasks.md#dbx-03-tasks).

### DBX-04 — Discover and identify supported tables

**As a pipeline owner**, I can select an authorized table without confusing it with another source.

- Browse catalog/schema/base Delta tables with bounded pagination and explicit unsupported-object
  feedback. Preserve exact column names and object identity; reject unsafe/unsupported identifiers.
- Verify schema and metadata coverage under the saved principal. Denied, incomplete, malformed, or
  stale results block rather than appear as empty catalogs/unmarked tables.
- Bind caches and review controls to the complete source identity and invalidate on changes.

**Prerequisites:** DBX-02, DBX-03. **Evidence:** Cross-owner/workspace isolation, pagination,
unsupported views/types, verified-empty and denied-metadata cases.

**Tasks:** [Six discovery and identity tasks](databricks-connection-tasks.md#dbx-04-tasks).

### DBX-05 — Map Databricks sensitivity metadata

**As a pipeline owner**, I see safe findings and cannot bypass source or target sensitivity policy.

- Map exact configured key/key-value policies and all `class.*` column tags to fixed marker IDs.
  Sensitive source parents/tables block before extraction; sensitive targets block before staging.
- Refresh and compare schema, markers, and mapping version at the defined inspection boundaries.
  Labels identify Databricks metadata rather than displaying “Foundry metadata.”
- No raw tag text or classification samples enter persisted findings, UI, diagnostics, or logs.

**Prerequisites:** DBX-02, DBX-04. **Evidence:** Parent/table/column markers, case-distinct keys,
unknown classifications, source/target permission failures, and changed-marker tests.

**Tasks:** [Seven sensitivity-metadata tasks](databricks-connection-tasks.md#dbx-05-tasks).

### DBX-06 — Scan and apply Hash/Remove

**As a pipeline owner**, I can review and transform flagged columns before data reaches a target.

- Run the existing complete-source SSN scan and bounded encrypted spool, then publish those exact
  reviewed batches. Enforce source consistency, limits, supported type mapping, and cancellation.
- Preserve source-bound saved decisions, resolve new findings, and apply Hash/Remove before
  publication. Removed columns never enter destination staging; unresolved actions create none.
- Enforce append/replace removal limits, hash output types, and governance/history disclosures on
  Databricks-source and Databricks-destination routes.

**Prerequisites:** DBX-04, DBX-05. **Evidence:** Tagged/untagged synthetic SSNs, saved/stale actions,
multi-batch scans, schema drift, spool limits, numeric/decimal/timestamp/null fidelity and failures;
representative CSV/PostgreSQL/Foundry-to-Databricks, reverse routes, and distinct-table Databricks
copies according to the supported capability matrix.

**Tasks:** [Seven scan and transformation tasks](databricks-connection-tasks.md#dbx-06-tasks).

### DBX-07 — Publish Delta destinations safely

**As a pipeline owner**, I can distinguish committed output from unchanged or uncertain output.

- Implement only the modes proven in DBX-01 through private transformed staging and atomic
  publication. Register truthful capability and row-count verification levels.
- Preserve target governance; block incompatible schema, exact self-copy, conflicts, and
  unauthorized operations. Pre-publication failure leaves the live target unchanged.
- Reconcile ambiguous commits and recover staging cleanup. Never blindly replay uncertain append
  or report Hash/Remove applied without commit evidence.

**Prerequisites:** DBX-01, DBX-06. **Evidence:** Append/create/replace output and schema checks,
concurrent writers, denied governance changes, cancellation at commit, dropped responses,
worker restart, duplicate prevention, and cleanup failure.

**Tasks:** [Eight publication and recovery tasks](databricks-connection-tasks.md#dbx-07-tasks).

### DBX-08 — Explain outcomes and deliver the release evidence

**As an owner or operator**, I can understand startup, blocking, and publication outcomes safely.

- Run review records detector, safe object/column identity, count, action, scan coverage,
  execution/destination outcome, and blocked reason; compute audit records who authorized startup,
  selected compute, attempt time and outcome. No matched values, secrets, or fetch links persist.
- Update connection/user/operator guides, FAQ, supported route matrix, and troubleshooting.
  Capture synthetic, annotated screenshots for stopped compute, startup progress/failure,
  guardrail decisions, and committed/blocked/uncertain outcomes. Captions identify relevant controls.
- Protect all connector entry points with an operator enablement flag; a separate writer switch
  controls destination capability. Disabled routes fail safely. Complete the release checklist.

**Prerequisites:** DBX-02 through DBX-07. **Evidence:** Redaction tests, UI checks, captioned screenshots,
operator setup walkthrough and release acceptance record.

**Tasks:** [Seven run-review and release tasks](databricks-connection-tasks.md#dbx-08-tasks).

## Release checklist

- [ ] DBX-01 decisions identify supported deployments, protocols, permissions and write modes.
- [ ] All story acceptance criteria have named automated evidence; existing providers pass regression checks.
- [ ] Isolated live evidence verifies authentication, tags, startup, and each enabled write mode;
      a simulator alone is insufficient.
- [ ] Negative tests prove no row reads on blocked source metadata, no staging before resolved scans,
      no secret/value logging, and no unapproved compute startup.
- [ ] Ambiguous commit, concurrent write, worker restart and staging cleanup outcomes are verified.
- [ ] Documentation and annotated screenshots match implemented behavior and disclose costs/retention.
- [ ] Operator enablement and writer flags default off until the supported matrix is accepted.

Product owns scope and guardrail policy decisions; engineering owns adapter/recovery evidence; the
deployment operator owns workspace permissions, private staging, networking, and live validation.
This checklist remains the acceptance authority; local code is present, but it is not evidence of
provider acceptance or release completion.

## Repository integration points

These repository areas now contain local Databricks integration. Their presence does not constitute
provider acceptance or released support.

- [Connector protocols](../../app/connectors/base.py) and [registry](../../app/connectors/registry.py):
  truthful capabilities, compute health, source extraction and publication/abort outcomes.
- [Typed locators and write policies](../../app/domain/locators.py): stable Databricks identity and
  supported Delta modes without credentials in definitions.
- [Guardrail model](../../app/domain/pipelines/guardrails.py) and
  [transfer engine](../../app/services/transfer_engine.py): Databricks detector, source-bound review,
  metadata refresh, encrypted scan and commit reconciliation.
- [Catalog service](../../app/services/catalogs.py) and
  [run service](../../app/services/pipeline_runs.py): permission-aware caches and durable, value-free
  findings/compute attempts with accurate publication outcomes.
- [Acceptance ledger](../plans/databricks-acceptance.md): per-task evidence, local checks, and
  unresolved live gates.

## Official references

References were checked on October 9, 2026. AWS documentation is used for the provider facts below;
DBX-01 must verify equivalent behavior for every cloud/deployment claimed as supported.

[sql-connector]: https://docs.databricks.com/aws/en/dev-tools/python-sql-connector
[cluster-api]: https://docs.databricks.com/api/clusters/v2/list-clusters
[compute-acls]: https://docs.databricks.com/aws/en/compute/use-compute
[warehouses]: https://docs.databricks.com/aws/en/compute/sql-warehouse
[driver-start]: https://kb.databricks.com/en_US/clusters/cluster-restart-fails
[classification]: https://docs.databricks.com/aws/en/data-governance/unity-catalog/data-classification
[classification-results]: https://docs.databricks.com/aws/en/admin/system-tables/data-classification
[column-tags]: https://docs.databricks.com/aws/en/sql/language-manual/information-schema/column_tags
[tags]: https://docs.databricks.com/aws/en/database-objects/tags
[schema-removal]: https://docs.databricks.com/aws/en/tables/update-schema
[create-table]: https://docs.databricks.com/aws/en/sql/language-manual/sql-ref-syntax-ddl-create-table-using
[isolation]: https://docs.databricks.com/aws/en/optimizations/isolation
