# Databricks connector: operator and acceptance guide

**Release status:** Not released. The connector and local integration are present, but every
Databricks feature flag defaults to off. Keep it off until the acceptance ledger links an approved
provider contract and live evidence for the exact workspace, compute family, routes, and write modes
being enabled.

This guide records the current implementation contract so an operator can prepare an isolated
acceptance environment. It is not approval to connect a production workspace.

## Current scope

The code uses a workspace personal access token (PAT) with existing SQL warehouses or supported
all-purpose classic clusters. Object support is Unity Catalog base Delta tables with scalar columns.
PATs do not refresh automatically: an owner must replace an expired or revoked token in the
connection form. Use a short-lived, least-privileged token where workspace policy permits it.
The design excludes interactive user OAuth, OAuth M2M, jobs compute, compute creation/resizing/shutdown,
legacy metastore objects, views, complex columns, arbitrary SQL, incremental extraction, upsert, and
external-storage credentials. No compute or write combination is supported until the provider
contract decision in [DBX-01](../plans/databricks-epic-implementation.md#provider-decisions-that-must-precede-supported-implementation)
and its live probes are reviewed.

Manual startup is the default. `allow_start` appears as a saved policy only after an operator has
attested the startup contract; the setting is false by default. Saving or testing credentials uses
management APIs to authenticate and inspect compute without submitting SQL or starting it. With
manual policy, a stopped compute blocks a run until the owner starts it explicitly. With consented
startup, an eligible stopped compute is started through the durable attempt service; the run is
requeued without holding a worker and resumes when readiness is observed. Consent is bound to the
workspace, API token, compute kind and ID, and SQL path. Changing any of those values resets consent.
Starting compute may incur provider charges. Data Mover does not stop compute or change its settings.

## Prepare an isolated workspace

Use synthetic tables and a non-production workspace. Platform staff should provision:

- a PAT owner with the minimum management permissions to inspect the selected compute, plus start
  permission only for a separately approved startup probe;
- an existing SQL warehouse or eligible all-purpose classic cluster, with the compute ID and SQL
  HTTP path taken from that same workspace and compute;
- Unity Catalog catalogs, schemas, and Delta fixtures that cover allowed and denied visibility,
  tagged and untagged columns, and all proposed publication modes;
- a private catalog/schema reserved for Data Mover staging if writer acceptance is approved; and
- network routes, DNS, proxy, TLS/CA trust, and workspace host allowlisting for the app deployment.

Do not use a real data set or paste credentials into issue comments, screenshots, or logs. The
token owner must not receive broad workspace administration rights. The current checker
requires the SQL path, workspace, compute kind, and compute ID to agree; unsupported IDs and hosts
fail closed.

## Connection setup in an acceptance environment

Only after the operator has approved an isolated acceptance deployment and enabled
`PIPELINE_ENABLE_DATABRICKS`, open **Connections → Credentials** and provide:

1. **Workspace URL**: the HTTPS workspace origin, without a path, query, fragment, credentials, or
   nonstandard port. It must match the deployment's `PIPELINE_ALLOWED_HTTPS_HOSTS` policy.
2. **Personal access token**: create it in the approved workspace for an appropriately scoped
   identity. Paste it only into the password field; saved values are encrypted and never displayed.
3. **Compute type**: `warehouse` or `classic_cluster`.
4. **Compute ID** and that compute's SQL **HTTP path**.
5. **Startup policy**: `manual` by default. `allow_start` is available only when the operator has
   enabled and attested `PIPELINE_DATABRICKS_STARTUP_CONTRACT_VERIFIED` after isolated probes.

The credential bundle is encrypted and owner-scoped. Replacing or deleting the connection invalidates
saved reviews and active startup authorization. Saved secrets are never displayed again. Use **Test
connection** to repeat the management-only validation; it does not prove SQL permissions, metadata
completeness, or transfer readiness.

For an explicitly approved start test, use the start control on the Databricks connection. The
owner-scoped **Databricks startup history** records the requested compute, observed state, check
count, and safe outcome. **Stop readiness checks** ends polling without stopping provider compute;
the original attempt remains fenced against duplicate starts. **Resume readiness checks** continues
polling against the original deadline. A timeout does not stop compute either.

## Operator gates

All gates are deployment settings; none should be turned on by copying a sample configuration.
The exact runtime settings and safety checks are defined in `app/config.py` and the connector
registry.

| Setting | Current default | Purpose |
| --- | --- | --- |
| `PIPELINE_ENABLE_DATABRICKS` | `false` | Admit the provider and show its connection UI. |
| `PIPELINE_ENABLE_DATABRICKS_WRITER` | `false` | Admit the destination writer; cannot be true unless the connector is enabled and all attestations pass. |
| `PIPELINE_ALLOWED_HTTPS_HOSTS` | empty | Exact HTTPS host allowlist shared by remote providers. Include only approved workspace hosts. |
| `PIPELINE_DATABRICKS_STAGING_SCHEMA` | empty | Private `catalog.schema` used for run-scoped staging. Writer startup fails closed without it. |
| `PIPELINE_DATABRICKS_METADATA_VISIBILITY_VERIFIED` | `false` | Operator attestation that isolated tests proved complete metadata visibility. |
| `PIPELINE_DATABRICKS_STARTUP_CONTRACT_VERIFIED` | `false` | Operator attestation that compute/SQL startup races, manual mode, and opted-in readiness passed. |
| `PIPELINE_DATABRICKS_WRITER_LIVE_ACCEPTANCE_VERIFIED` | `false` | Separate attestation for create, append, replace, governance, conflict, and recovery probes. |
| `PIPELINE_DATABRICKS_SENSITIVE_TAG_RULES` | `[]` | Exact-case tag rules mapped to fixed safe marker IDs; do not place sensitive values here. |
| `PIPELINE_DATABRICKS_METADATA_POLICY_VERSION` | `1` | Binds reviews to the operator's marker policy revision. |

The provider gate, metadata visibility attestation, startup-contract attestation, and writer live
acceptance attestation are independent. Writes require all relevant checks and an approved private
staging schema. These environment values are guardrails, not substitutes for the linked decision and
probe evidence. Keep the writer flag off unless the exact enabled combination has passed review.

## Metadata policy and review

The connector inspects Unity Catalog table, schema, catalog, column, and configured tag metadata.
Incomplete or denied coverage blocks a transfer. An empty tag result is accepted only after complete
visibility has been demonstrated and attested. `class.*` classifications are treated as sensitive;
additional rules use exact case-sensitive keys and optional exact values, with fixed marker IDs.
Never retain raw tag values, source examples, or matched SSNs in a review record.

Source and target reviews bind the owner, workspace, connection generation, qualified object identity,
schema/sensitivity fingerprints, and current tag policy. A changed credential, object, mapping,
schema, or metadata policy requires a fresh review. Hash and Remove affect transformed output only;
they do not erase prior Delta history, staging artifacts retained for uncertain recovery, snapshots,
or backups.

## Writer behavior and recovery

The writer creates a run-scoped Delta staging table in the configured private schema, writes only
after the scan and guardrail pass, then submits create, append, or full replace. For serverless
warehouses, the exact publish statement carries a non-sensitive run UUID and target fingerprint as
query tags and a SQL comment; recovery confirms only a `FINISHED` statement with the exact run and
target query tags or the exact SQL comment from Query History. For classic warehouses and eligible
all-purpose clusters, it sets Delta's
`userMetadata` for the publish statement and confirms the exact marker in `DESCRIBE HISTORY`. These
markers contain no source values. The database stores publication intent before staging and tracks
committed, unchanged, uncertain, and cleanup-pending outcomes. A target fence prevents another Data
Mover writer from racing while the outcome is unresolved. Run review exposes the persisted
publication state.

Live probes still need to prove atomicity, governance preservation, Query History visibility and
marker retention on serverless, Delta History visibility on classic compute, interaction with
external writers, and each failure window for every claimed compute family. Serverless Query
History is limited to the API's 30-day search window; older or inaccessible entries remain uncertain
and fenced for operator review. An uncertain publication holds its target fence; do not retry or
manually drop its staging table until recovery determines the commit outcome. The recovery worker
uses the saved connection generation and a deterministic run-owned stage name.
If credentials were rotated, recovery retains a safe operator-visible unresolved state rather than
using new credentials to mutate a stage created with the old connection.

Before retrying after any uncertain outcome, inspect the destination and use the run's
**Databricks publication** section. Recording a reconciliation review does not prove an uncertain
commit; recovery must first confirm the exact compute-family publication marker. Cleanup completion
is separate from whether the destination changed.

## Local connection integration tests

Install the development dependencies with `make install`, then run `make databricks-check`.
The normal `make check` and CI suite include these tests too; no cloud credentials, Docker,
or separately running emulator are needed. Install Java 11+ and PostgreSQL server binaries
(`initdb` and `postgres`) locally; CI provisions both. The development dependency pins
[MiniLake](https://github.com/dmux/minilake) to 1.8.0.

Each pytest worker that uses MiniLake starts its own loopback server with a reserved socket,
temporary data directory, bounded startup wait, and process cleanup. Every test creates its own
warehouse. Notebook execution, Spark prewarming, persistence, and extension downloads are disabled.
The test-only management-client factory redirects an approved synthetic workspace URL to MiniLake
using the real Databricks SDK; production HTTPS/host validation remains active.

The suite checks SDK enum decoding, read-only health checks on stopped warehouses, explicit startup,
missing-compute error sanitization, saved connection health, startup authorization, and durable
worker readiness polling. It caught a real adapter bug where SDK enum values such as `RUNNING`
were interpreted as `STATE.RUNNING`. Separate tests use generated classic-cluster SDK models
to cover source, access-mode, kind, and state enums.

The MiniLake race cases replace saved credentials during health checks, successful/failed start
responses, and readiness polls, and cancel observation while a start response is in flight.
Separate database sessions make these interleavings deterministic. SQL adapter tests verify
named-column alignment, parameter binding and chunk boundaries, reject batch schema changes,
and block publication if a staged target's identity, columns, sensitivity, or existence changes.
Recovery tests exercise superseded poll leases, competing cleanup workers, stale cleanup replies,
and retrying staging cleanup after the destination commit has already been confirmed.

MiniLake accepts dummy tokens and provides the REST Statement Execution API rather than the
SQL connector's Thrift protocol. These tests do **not** validate SQL PAT acceptance, Thrift/TLS behavior, Unity
Catalog sensitivity metadata, or Delta publication guarantees. MiniLake's cluster responses also
omit metadata needed to establish Unity Catalog compatibility; that case must remain blocked.
Existing SQL adapter tests and the live acceptance probes remain necessary. Passing emulator tests
does not enable startup or writer rollout gates.

### HTTP, PAT, network faults, and PostgreSQL concurrency

`make databricks-tools` downloads WireMock 3.13.2 and Toxiproxy 2.12.0 into the ignored
`.cache/databricks-tools/` directory. Both `make databricks-check` and `make check` run this
setup automatically, verify the pinned SHA-256 checksums even for cached files, and require
the test tools and PostgreSQL rather than silently skipping these checks. The downloader supports
macOS/Linux on ARM64 and AMD64. Set `DATABRICKS_TEST_JAVA` to select a Java executable.
Prefill the cache with `make databricks-tools` before working offline; pytest never downloads tools.
Tool control calls, health probes, and streamed downloads use the existing `httpx2` dependency.
Provider requests continue to use the real Databricks SDK and its own transport.
Direct pytest invocations skip external-tool cases when their binaries are absent unless
`DATABRICKS_TEST_TOOLS_REQUIRED=1` is set.

The transport tests start bounded, loopback-only WireMock and Toxiproxy processes per pytest
worker, use isolated journals and temporary directories, and terminate the processes on teardown.
WireMock drives stopped/starting/running states, 429/503 retries, permission and missing-resource
errors, and a start request whose response is lost after the simulated state changes. The latter
checks that the application retains its durable attempt, deduplicates a second request, and
resolves the uncertainty with a read-only worker poll. Scripted HTTP tests verify the real SDK sends
the configured PAT as a bearer token, does not contact an OAuth token endpoint, and sanitizes
authentication failures. These are protocol tests against synthetic responses, not evidence that
Databricks accepts the credentials.

Toxiproxy forwards real SDK traffic to MiniLake while injecting TCP resets, truncated response
bodies, and stalled reads. The tests require retryable safe errors and a usable connection after
the fault is removed. Truncated responses reproduce a `requests` `ChunkedEncodingError` that the
adapter must classify as provider unavailability rather than an internal application failure.

Separate tests use the existing ephemeral PostgreSQL harness with real concurrent transactions:
simultaneous compute reservations converge on one attempt; pollers skip locked rows; an expired
lease holder cannot overwrite its successor; and concurrent publications fence one destination
until the winning operation releases it. The tests bound database lock/statement waits and check
that the losing publication transaction remains usable. SQL Thrift, TLS, Unity Catalog governance,
and live Delta publication acceptance remain outside these tools' coverage.

## Acceptance and release checklist

1. Complete and review [DBX-01](../plans/databricks-epic-implementation.md#provider-decisions-that-must-precede-supported-implementation)
   for the exact deployment and compute families.
2. Run the permission, transport, metadata, one-query source, and type-mapping probes with synthetic
   data; record sanitized versions and results under `artifacts/databricks/`.
3. Verify manual startup, denied permissions, ambiguous responses, polling deadline, lease recovery,
   and the stop-after-state-check race.
4. Verify each proposed publication mode separately, including governance, outside writers,
   uncertain response reconciliation, process death, private-stage cleanup, and concurrent target
   fencing.
5. Run `make check`, `make hedron-build`, and the secret-artifact verification workflow, then attach
   annotated screenshots from synthetic states.
6. Update [the acceptance ledger](../plans/databricks-acceptance.md) with exact task/test/evidence
   links and the supported matrix. Enable only combinations approved by Product, Platform, and
   Engineering. Keep flags off for every unresolved gate.

The accepted feature must also be documented in the [user guide](../user-guide.md), including its
actual support matrix and any exclusions. Until then, Data Mover does not make a Databricks support
claim.
