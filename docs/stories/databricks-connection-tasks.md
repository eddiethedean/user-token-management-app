# Databricks connection implementation tasks

[Parent epic](databricks-connection-epic.md) · [Issue CSV export](../../artifacts/jira/data-mover-databricks-connection-issues.csv) · Prepared October 9, 2026

This backlog contains **57 tasks across eight stories**. Local implementation has started; see the
[acceptance ledger](../plans/databricks-acceptance.md) for current code evidence and open gates.
No task is complete until its specified local and live evidence is recorded.
The original default-off rollout criteria in this backlog are historical. Current defaults and
metadata-verification prerequisites are documented in the
[Databricks operator guide](../providers/databricks.md).
IDs are local planning references, not assigned GitHub/Jira issue IDs. Owner roles are proposed
accountability, not assignments to named people. The epic remains the scope and acceptance authority.

The CSV preserves the previous epic's columns and parent-link structure. Local planning IDs are
exported as issue references; dependencies and owner roles remain in task descriptions, and
unestimated hours are blank.

Each task defines work, observable completion evidence, and prerequisite task IDs. A dependency is
satisfied only when its evidence is recorded. Provider probes use an isolated authorized workspace
and synthetic fixtures; unavailable live evidence is an external gate, not evidence of support.
Story acceptance still applies to every task under that story.

## Task index

| Story | Tasks | Backlog |
| --- | --- | --- |
| DBX-01 — Validate the provider contract | 7 | [Task details](#dbx-01-tasks) |
| DBX-02 — Save and validate a workspace connection | 7 | [Task details](#dbx-02-tasks) |
| DBX-03 — Check compute and start eligible clusters | 8 | [Task details](#dbx-03-tasks) |
| DBX-04 — Discover and identify supported tables | 6 | [Task details](#dbx-04-tasks) |
| DBX-05 — Map Databricks sensitivity metadata | 7 | [Task details](#dbx-05-tasks) |
| DBX-06 — Scan and apply Hash/Remove | 7 | [Task details](#dbx-06-tasks) |
| DBX-07 — Publish Delta destinations safely | 8 | [Task details](#dbx-07-tasks) |
| DBX-08 — Explain outcomes and deliver release evidence | 7 | [Task details](#dbx-08-tasks) |

## Completion convention

- Record the implementation or decision artifact and named checks/probe evidence when finishing a task.
- Preserve blocked/uncertain outcomes and deferred scope; do not expand a mode beyond the provider decision record.
- Complete each story only when all of its tasks and acceptance criteria have evidence.
- The final release task aggregates earlier evidence and regression checks; it does not replace the story-specific tests.

## DBX-01 tasks

**Parent story:** Validate the provider contract.

| Task and owner role | Work and completion evidence | Depends on |
| --- | --- | --- |
| **DBX-01-T01 — Select supported deployments**<br>Owner: Product / Platform | Decide supported Databricks clouds and workspace endpoint patterns, private-network routes, TLS/CA requirements, and excluded deployments. **Done when:** The support matrix names allowed deployments and network prerequisites; unsupported endpoints and redirect handling have a documented policy. | None |
| **DBX-01-T02 — Choose the SQL and compute compatibility matrix**<br>Owner: Engineering | Evaluate connector/SDK versions, SQL protocol, cluster runtime/access mode, warehouse compatibility, metadata relations, and scalar type mappings. **Done when:** A pinned dependency/protocol proposal and runtime/type matrix distinguish verified support from exclusions. | `DBX-01-T01` |
| **DBX-01-T03 — Prove metadata visibility checks**<br>Owner: Engineering / Platform | Probe catalog, schema, table, and column visibility under least-privilege principals, including verified-empty, filtered, incomplete, and denied tag results. **Done when:** Synthetic probe evidence defines when coverage is verifiable and when extraction/writes must block; the permission checklist covers every metadata scope. | `DBX-01-T02` |
| **DBX-01-T04 — Prove startup authorization behavior**<br>Owner: Engineering / Platform | Test management-only validation, explicit cluster/warehouse startup, driver-triggered startup, and compute stopping between state check and query submission. **Done when:** The decision record specifies enforceable manual and opt-in modes for each compute type, required permissions, and excluded unsafe combinations. | `DBX-01-T02` |
| **DBX-01-T05 — Prove a consistent source result**<br>Owner: Engineering | Probe one-query extraction, batch boundaries, provider masks/filters, schema changes, and source changes during a read. **Done when:** The source contract shows how batches belong to one query result, preserves provider controls, and defines safe source/query identity recording. | `DBX-01-T02` |
| **DBX-01-T06 — Prove Delta publication and reconciliation**<br>Owner: Engineering / Platform | Probe atomic create/append/replace, governance preservation, outside writer conflicts, interrupted commits, and operation identity/evidence. **Done when:** Each proposed mode has a demonstrated publication and recovery contract; unverifiable modes are excluded rather than assumed safe. | `DBX-01-T02` |
| **DBX-01-T07 — Publish the provider decision record**<br>Owner: Product / Engineering / Platform | Consolidate the support, permissions, startup, source, destination, staging, and recovery decisions into one architecture decision and release matrix. **Done when:** The reviewed record resolves all provider-contract assumptions and links synthetic live evidence; any remaining external gate has an owner role and explicit release effect. | `DBX-01-T01`, `DBX-01-T02`, `DBX-01-T03`, `DBX-01-T04`, `DBX-01-T05`, `DBX-01-T06` |

## DBX-02 tasks

**Parent story:** Save and validate a workspace connection.

| Task and owner role | Work and completion evidence | Depends on |
| --- | --- | --- |
| **DBX-02-T01 — Add Databricks domain and provider contracts**<br>Owner: Engineering | Define credential fields, typed locators/write policies, stable identities, provider capabilities, serialization, and the databricks_metadata detector without permissive provider-ID fallbacks. **Done when:** Existing definitions and Foundry/SSN actions remain valid; new values round-trip without credentials and expose only supported modes. | `DBX-01-T07` |
| **DBX-02-T02 — Persist encrypted owner-scoped credentials**<br>Owner: Engineering | Integrate workspace PATs into existing encrypted save/replace/delete flows; version credential/connection changes and invalidate related reviews and caches. **Done when:** Only the owner can use or modify the bundle; replacement/deletion revokes app access and stale startup/review authorization without revealing saved secrets. | `DBX-02-T01` |
| **DBX-02-T03 — Enforce workspace transport and compute binding**<br>Owner: Engineering | Apply the approved HTTPS endpoint, TLS/CA, redirect and network policy; derive or verify SQL HTTP paths against the configured workspace and compute ID. **Done when:** Invalid hosts, mismatched compute paths and credential-forwarding redirects fail before secret-bearing requests. | `DBX-02-T01`, `DBX-01-T07` |
| **DBX-02-T04 — Integrate PAT authentication and safe failures**<br>Owner: Engineering | Use the supported SDK PAT auth for management and SQL requests; handle token replacement, expiry/revocation, and bounded authentication errors. **Done when:** Failure paths retain safe diagnostics and never expose tokens. | `DBX-02-T02`, `DBX-02-T03` |
| **DBX-02-T05 — Implement management-only connection validation**<br>Owner: Engineering | Check authentication and selected compute visibility/state through management requests; separate authenticated, visible, stopped, stale, and SQL-ready information. **Done when:** Connection validation opens no SQL session and initiates no startup; status names the evidence and time checked rather than claiming transfer readiness. | `DBX-02-T03`, `DBX-02-T04` |
| **DBX-02-T06 — Add connection forms and policy controls**<br>Owner: Engineering | Provide Databricks credential, compute, and startup-policy forms with owner authorization, CSRF protection, safe replace/delete actions, and revalidation. **Done when:** The UI never reveals saved secrets and clears startup consent when workspace, token, or compute changes; unsupported selections have actionable feedback. | `DBX-02-T02`, `DBX-02-T05` |
| **DBX-02-T07 — Verify credential and compatibility boundaries**<br>Owner: Engineering / Quality | Add contract, persistence/migration, UI ownership, transport, secret rotation, token rotation, revoked-authentication and serialization tests. **Done when:** Named tests prove cross-owner isolation, legacy compatibility, no secrets in definitions/logs, and management-only validation without startup. | `DBX-02-T01`, `DBX-02-T02`, `DBX-02-T03`, `DBX-02-T04`, `DBX-02-T05`, `DBX-02-T06` |

## DBX-03 tasks

**Parent story:** Check compute and start eligible clusters.

| Task and owner role | Work and completion evidence | Depends on |
| --- | --- | --- |
| **DBX-03-T01 — Implement compute state and start adapters**<br>Owner: Engineering | Map every cluster state and the supported warehouse states; provide authorized state/start operations for the configured compute type. **Done when:** Adapters use the correct management API and never create, resize, restart, terminate, or change compute settings. | `DBX-02-T07` |
| **DBX-03-T02 — Enforce startup authorization**<br>Owner: Engineering | Implement explicit Start and opt-in authorization checks using current owner, principal generation, workspace/compute binding, provider permissions, and CSRF. **Done when:** A forged compute ID, stale consent, non-owner request, or missing provider permission cannot initiate startup; broader administration rights are not requested. | `DBX-03-T01` |
| **DBX-03-T03 — Persist and deduplicate startup attempts**<br>Owner: Engineering | Store attempt identity, selected compute, initiator, deadlines and outcomes; coordinate simultaneous requests and re-check ambiguous start responses. **Done when:** Refresh, retry, concurrent owners and worker restart do not duplicate an in-flight start or reset its deadline; observing shared compute does not expose another owner's private record. | `DBX-03-T02` |
| **DBX-03-T04 — Implement bounded readiness observation**<br>Owner: Engineering | Add background polling, backoff/jitter, rate-limit handling, readiness checks, lease-safe recovery, timeout, and observation cancellation. **Done when:** Polling obeys the original deadline and stops safely on terminal outcomes; cancellation/timeout never stops provider compute or waits indefinitely for user input. | `DBX-03-T03` |
| **DBX-03-T05 — Gate SQL on the approved startup policy**<br>Owner: Engineering | Apply the DBX-01 manual/opt-in contract to SQL sessions for discovery, scans, runs and reconciliation, including the stop-after-check race. **Done when:** Every SQL entry point either satisfies the proven no-autostart contract or has current explicit startup authorization; blocked combinations do not submit SQL. | `DBX-03-T02`, `DBX-03-T04` |
| **DBX-03-T06 — Build compute state and startup feedback**<br>Owner: Engineering | Show current state and checked time, selected workspace/compute, startup charge disclosure, Start, progress, safe failure/remediation, and cancel-observation behavior. **Done when:** Users can distinguish provider state from SQL readiness, understand warehouse startup side effects, and see that cancelling observation does not terminate compute. | `DBX-03-T04`, `DBX-03-T05` |
| **DBX-03-T07 — Integrate readiness with pipeline lifecycle**<br>Owner: Engineering | Check source and destination readiness using the existing background runtime; make manual stopped-compute runs terminally blocked and opted-in runs bounded. **Done when:** No worker waits indefinitely for an owner; a later retry re-checks state, retains guardrail enforcement, and never shuts down shared compute. | `DBX-03-T04`, `DBX-03-T05` |
| **DBX-03-T08 — Verify states, races and least privilege**<br>Owner: Engineering / Quality / Platform | Extend simulator fixtures for all states, duplicate/lost starts, permissions, warehouse routing, rate limits, cancellation and startup races; verify supported behavior in an isolated workspace. **Done when:** Named tests and synthetic live evidence prove readiness outcomes and absence of unauthorized lifecycle calls for each supported compute type. | `DBX-03-T01`, `DBX-03-T02`, `DBX-03-T03`, `DBX-03-T04`, `DBX-03-T05`, `DBX-03-T06`, `DBX-03-T07` |

## DBX-04 tasks

**Parent story:** Discover and identify supported tables.

| Task and owner role | Work and completion evidence | Depends on |
| --- | --- | --- |
| **DBX-04-T01 — Implement bounded Unity Catalog discovery**<br>Owner: Engineering | Browse accessible catalogs, schemas and supported base Delta tables with bounded pagination; identify unsupported object types without exposing source row examples. **Done when:** Every supported discovery page is consumed safely; views, legacy metastore, foreign/streaming objects and arbitrary SQL routes cannot be selected as supported sources. | `DBX-03-T08` |
| **DBX-04-T02 — Bind selections to stable object identity**<br>Owner: Engineering | Resolve fully qualified table identity under the saved connection; preserve exact column names and use provider-aware identifier validation/quoting. **Done when:** Cross-workspace same-name tables and replaced/aliased objects cannot inherit the wrong selection or review; unsupported names are rejected with remediation. | `DBX-04-T01` |
| **DBX-04-T03 — Verify schema and metadata coverage**<br>Owner: Engineering | Implement DBX-01 permission/visibility checks for the object, complete columns, parent scopes, supported runtime and tag paths; reject unsupported types early. **Done when:** Verified-empty results are distinguishable from denied, partial, malformed, unsupported or ambiguous coverage, all of which block extraction/writes. | `DBX-04-T02`, `DBX-01-T03` |
| **DBX-04-T04 — Scope and invalidate catalog caches**<br>Owner: Engineering | Key caches by owner, connection/workspace, principal generation and stable object identity; invalidate on credential/source/schema changes and limit lifetime. **Done when:** Cached metadata does not cross owners/workspaces, preserve stale authorization, or turn partial/failed reads into verified-empty results. | `DBX-04-T02`, `DBX-04-T03` |
| **DBX-04-T05 — Build catalog selection and blocked-state UI**<br>Owner: Engineering | Show authorized namespaces/tables, safe schema, explicit unsupported-object feedback and precise empty/permission/coverage messages. **Done when:** Unavailable selections cannot enqueue a route; UI descriptions reflect coverage evidence without displaying source values. | `DBX-04-T03`, `DBX-04-T04` |
| **DBX-04-T06 — Verify discovery, identity and visibility**<br>Owner: Engineering / Quality | Test pagination boundaries, same-name tables across workspaces/owners, object replacement, caches, permission-filtered results and unsupported objects/types. **Done when:** Named tests prove complete supported discovery, correct review isolation, and fail-closed handling without row reads for blocked metadata. | `DBX-04-T01`, `DBX-04-T02`, `DBX-04-T03`, `DBX-04-T04`, `DBX-04-T05` |

## DBX-05 tasks

**Parent story:** Map Databricks sensitivity metadata.

| Task and owner role | Work and completion evidence | Depends on |
| --- | --- | --- |
| **DBX-05-T01 — Define versioned sensitive-marker policy**<br>Owner: Product / Engineering | Add exact-key and exact-key/value configuration mapped to fixed safe marker IDs; preserve provider key case and flag every class.* column tag. **Done when:** Policy validation rejects ambiguous rules; newly encountered classifications create findings; matching does not collapse case-distinct provider keys. | `DBX-04-T06` |
| **DBX-05-T02 — Read all required tag scopes safely**<br>Owner: Engineering | Read catalog/schema/table/column tag scopes using the approved visibility contract, transient matching and bounded result handling. **Done when:** The reader never queries classification sample results or changes tags; raw keys/values are absent from returned findings, stored metadata and diagnostics. | `DBX-05-T01`, `DBX-04-T03` |
| **DBX-05-T03 — Enforce source and destination metadata blocks**<br>Owner: Engineering | Block sensitive source parents/tables before extraction and sensitive destination parents/tables before staging; map destination column findings to incoming columns. **Done when:** Denied target metadata blocks writes; new-target parent checks and concurrent creation conflicts cannot bypass sensitivity controls. | `DBX-05-T02` |
| **DBX-05-T04 — Normalize findings and fingerprints**<br>Owner: Engineering | Emit databricks_metadata findings with safe marker IDs and exact affected identities; serialize value-free schema/sensitivity/policy fingerprints. **Done when:** Findings round-trip with truthful Databricks labels and contain no arbitrary tag text, sample values or provider responses. | `DBX-05-T02`, `DBX-02-T01` |
| **DBX-05-T05 — Refresh policy at inspection boundaries**<br>Owner: Engineering | Re-check metadata at preflight and after extraction, then target/parent policy before publication; compare identity, schema, sensitivity and policy revision. **Done when:** Changes require renewed review and prevent staging/publication; cached previews cannot override current metadata or claim continuous provider monitoring. | `DBX-05-T03`, `DBX-05-T04` |
| **DBX-05-T06 — Add metadata review and action controls**<br>Owner: Engineering | Present safe source/target column findings, table blocks, verified coverage and Hash/Remove controls bound to exact identities and column mapping. **Done when:** Saved choices from another source/target/mapping are rejected; labels say No configured markers found only with verified coverage and do not imply complete PII detection. | `DBX-05-T03`, `DBX-05-T04`, `DBX-05-T05` |
| **DBX-05-T07 — Verify metadata enforcement and redaction**<br>Owner: Engineering / Quality | Test all tag scopes, exact key/value matches, case-distinct keys, unknown class tags, filtered metadata, target findings and changing fingerprints. **Done when:** Named tests prove no blocked-source row reads, no blocked-target staging, no classification sample query, and no raw tag persistence or logging. | `DBX-05-T01`, `DBX-05-T02`, `DBX-05-T03`, `DBX-05-T04`, `DBX-05-T05`, `DBX-05-T06` |

## DBX-06 tasks

**Parent story:** Scan and apply Hash/Remove.

| Task and owner role | Work and completion evidence | Depends on |
| --- | --- | --- |
| **DBX-06-T01 — Implement consistent bounded extraction**<br>Owner: Engineering | Stream one supported source query result with row/byte limits and scalar fidelity; retain safe query/source identity, close cursors and cancel reads on failure. **Done when:** All batches belong to the approved source result; masks/filters are honored and schema/type drift, limits or interruption do not trigger unreviewed re-reads. | `DBX-05-T07`, `DBX-01-T05` |
| **DBX-06-T02 — Integrate complete scan and encrypted spool**<br>Owner: Engineering | Use the existing full-source SSN scanner and bounded encrypted spool; maintain runtime leases, cancellation and recoverable spool cleanup. **Done when:** Incomplete scans, exceeded limits or cancelled work create no destination staging, and raw source batches are encrypted and removed according to cleanup policy. | `DBX-06-T01` |
| **DBX-06-T03 — Resolve source-bound saved actions**<br>Owner: Engineering | Preserve applicable saved policies, require decisions for new findings, reject missing guarded columns/invalid mappings and all-column removal; apply Remove precedence and existing HMAC scope. **Done when:** The effective policy is deterministic, rejects stale cross-source choices, and defines supported hash output types before writes. | `DBX-06-T02`, `DBX-05-T06` |
| **DBX-06-T04 — Enforce destination Hash/Remove limits**<br>Owner: Engineering | Validate transformed schema against append/full-replace targets and governance; block retained removed columns, required tag/policy removal and incompatible output types. **Done when:** Append cannot leave a removed column in the target; replace omits its current data without editing governance; review states incoming-only hash and history limits. | `DBX-06-T03`, `DBX-05-T03` |
| **DBX-06-T05 — Replay only transformed reviewed batches**<br>Owner: Engineering | Integrate policy projection, casting and transformation with the worker; publish batches from the scanned spool without fetching the source again. **Done when:** Removed values never enter destination staging; hash output is not replaced by raw casts; original and transformed schemas remain distinct and actions stay pending until confirmed commit. | `DBX-06-T03`, `DBX-06-T04` |
| **DBX-06-T06 — Build scan/action feedback and policy save**<br>Owner: Engineering | Expose preview versus full-scan scope, unresolved decisions, saved policies, projected output schema and guardrail limitations in authoring/run feedback. **Done when:** Owners understand which decisions are selected, which scans are complete and what output is promised; preview/saved forms enforce identity and permissions. | `DBX-06-T03`, `DBX-06-T04`, `DBX-06-T05` |
| **DBX-06-T07 — Verify guardrail output across supported routes**<br>Owner: Engineering / Quality | Test multi-batch tagged/untagged SSNs, type/null fidelity, stale actions, drift, limits, cancellation and transforms on CSV/PostgreSQL/Foundry-to-Databricks and supported reverse/Databricks-copy routes. **Done when:** Named tests prove exact transformed schema/rows, no staging before resolved scans, source-bound actions, and correct failure/retention disclosures; source-only providers are never advertised as reverse destinations. | `DBX-06-T01`, `DBX-06-T02`, `DBX-06-T03`, `DBX-06-T04`, `DBX-06-T05`, `DBX-06-T06` |

## DBX-07 tasks

**Parent story:** Publish Delta destinations safely.

| Task and owner role | Work and completion evidence | Depends on |
| --- | --- | --- |
| **DBX-07-T01 — Create private run-scoped staging**<br>Owner: Engineering / Platform | Validate an operator-approved private staging schema, run-scoped object names/ACLs, native parameter binding, identifier quoting and durable safe staging references. **Done when:** Only approved principals access transformed staging; unrelated users cannot read it; no source values enter SQL text/debug logs and cleanup can identify the run's objects. | `DBX-06-T07`, `DBX-01-T06` |
| **DBX-07-T02 — Implement atomic new-table publication**<br>Owner: Engineering | Publish a transformed staged result to an absent managed Delta target using the proven create operation, with parent-policy and absence re-checks. **Done when:** Schema and row output match the reviewed manifest; concurrent target creation causes a conflict rather than overwrite or a false successful no-op. | `DBX-07-T01` |
| **DBX-07-T03 — Implement atomic append publication**<br>Owner: Engineering | Validate compatible transformed columns and publish staging through the proven single atomic append operation; report truthful row-count evidence. **Done when:** Pre-publication failures leave the live table unchanged; a confirmed append writes the reviewed rows once and an uncertain append is not automatically replayed. | `DBX-07-T01` |
| **DBX-07-T04 — Implement atomic full-replace publication**<br>Owner: Engineering | Use the proven replace operation with transformed schema, target-policy checks and preservation of grants/tags/masks/filters; prohibit drop-and-create fallback. **Done when:** Current target output matches the reviewed rows/schema; unsupported governance changes or unproven preservation block safely without claiming physical erasure. | `DBX-07-T01` |
| **DBX-07-T05 — Coordinate target identity and concurrent writers**<br>Owner: Engineering | Reject exact self-copy by stable identity; serialize Data Mover target writers across instances and apply the verified outside-writer/schema conflict policy. **Done when:** Copies between distinct tables remain allowed; worker/lease loss and concurrent target changes cannot silently overwrite a different approved target/version. | `DBX-07-T02`, `DBX-07-T03`, `DBX-07-T04` |
| **DBX-07-T06 — Reconcile submitted and uncertain commits**<br>Owner: Engineering | Persist publication intent and safe operation identity before submission; reconcile lost responses/worker death using provider evidence and startup-policy-safe queries. **Done when:** Run outcomes distinguish committed, unchanged and uncertain; actions are applied only for confirmed commits and uncertain append never receives a blind retry. | `DBX-07-T05`, `DBX-03-T05` |
| **DBX-07-T07 — Recover staging and post-commit cleanup**<br>Owner: Engineering | Close sessions and clean only run-owned staging/spools; persist failed cleanup and resume it safely after interruption without deleting live targets or stopping compute. **Done when:** Cleanup recovery is idempotent, respects owner/workspace identity and preserves evidence needed by uncertain runs; cleanup failure never turns a confirmed commit into rolled_back. | `DBX-07-T06` |
| **DBX-07-T08 — Verify publication and recovery failure windows**<br>Owner: Engineering / Quality / Platform | Test/probe create/append/replace output, governance denial, outside writers, commit-time cancellation, dropped responses, worker restart and cleanup failure. **Done when:** Automated failure tests plus isolated synthetic provider evidence establish each enabled mode's outcomes, duplicate prevention and truthful capability/verification levels. | `DBX-07-T02`, `DBX-07-T03`, `DBX-07-T04`, `DBX-07-T05`, `DBX-07-T06`, `DBX-07-T07` |

## DBX-08 tasks

**Parent story:** Explain outcomes and deliver release evidence.

| Task and owner role | Work and completion evidence | Depends on |
| --- | --- | --- |
| **DBX-08-T01 — Persist and present value-free run review**<br>Owner: Engineering | Record detector, safe object/column identity, counts, actions, scan coverage, original/transformed schemas and execution/destination/blocked outcomes. **Done when:** Run review shows selected versus applied actions accurately and never stores source values, arbitrary tags, tokens or fetch links. | `DBX-06-T07`, `DBX-07-T08` |
| **DBX-08-T02 — Audit compute authorization and attempts**<br>Owner: Engineering | Record owner authorization, configured compute, attempt time/deadline, state transitions, cancellation/timeout and final outcome in owner-scoped audit views. **Done when:** An operator can explain why startup occurred; concurrent owners cannot read another owner's private attempt details and audits contain no secret/provider payloads. | `DBX-03-T08` |
| **DBX-08-T03 — Implement operator and writer rollout gates**<br>Owner: Engineering | Add default-off connector enablement and independent writer gating to registration, UI, save/enqueue and direct use-case entry points. **Done when:** Disabled connections/routes fail safely, source-only enablement exposes no writer path, and capability matrices advertise only proven modes. | `DBX-02-T07`, `DBX-07-T08` |
| **DBX-08-T04 — Verify diagnostics and log redaction end to end**<br>Owner: Engineering / Quality | Exercise safe user feedback, existing diagnostic events and SDK/connector error logging for authentication, metadata, startup, scans and publication. **Done when:** Secret, matched-value, raw-tag and fetch-link sentinels are absent from persistent records, UI and captured logs, including partial failures and debug-path checks. | `DBX-08-T01`, `DBX-08-T02`, `DBX-08-T03` |
| **DBX-08-T05 — Update user and operator documentation**<br>Owner: Product / Engineering / Platform | Update connection setup, permission profiles, supported routes, startup policy, private staging/networking, recovery, feature flags, FAQ and retention guidance. **Done when:** Guides match implemented support and include a reproducible operator walkthrough with synthetic values; no document claims physical purge or unsupported routes. | `DBX-08-T01`, `DBX-08-T02`, `DBX-08-T03` |
| **DBX-08-T06 — Capture accurate annotated screenshots**<br>Owner: Engineering / Quality | Capture synthetic fixtures for stopped compute, progress/failure, unresolved guardrail choices and committed/blocked/uncertain outcomes; label the relevant controls and record provenance. **Done when:** Captions explain the exact shown state and numbered callouts identify the discussed controls; screenshots are verified against implemented UI without real credentials/data. | `DBX-08-T01`, `DBX-08-T02`, `DBX-08-T05` |
| **DBX-08-T07 — Complete release acceptance and regression record**<br>Owner: Engineering / Quality / Platform | Run required local/CI regression checks; assemble named story/task evidence, isolated live acceptance, support matrix, operator setup, docs/screenshots and rollout checklist. **Done when:** The acceptance record links passing checks and provider evidence for every enabled combination; missing external evidence keeps the affected support/flag gate closed and no task is marked complete without evidence. | `DBX-01-T07`, `DBX-02-T07`, `DBX-03-T08`, `DBX-04-T06`, `DBX-05-T07`, `DBX-06-T07`, `DBX-07-T08`, `DBX-08-T04`, `DBX-08-T05`, `DBX-08-T06` |

## Release traceability

The epic release checklist is satisfied through DBX-01-T07 (provider decisions), each story's
verification task (automated and live evidence as specified), DBX-08-T03 (rollout gates),
DBX-08-T04 (end-to-end redaction), DBX-08-T05/T06 (docs and annotated screenshots), and
DBX-08-T07 (the consolidated acceptance record). Until that evidence exists, these tasks describe
the implementation and acceptance record; they do not claim a released Databricks connector.
