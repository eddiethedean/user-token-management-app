# Databricks connector: operator and acceptance guide

**Live release status:** Not released. Databricks connections, catalog/schema/table browsing,
and explicit or owner-consented compute startup default to enabled, including on Posit Connect.
Schema inspection, row counts, source transfers, and Delta create/append/replace require explicit
metadata visibility verification. `PIPELINE_DATABRICKS_METADATA_VISIBILITY_VERIFIED` defaults to
false; successful empty tag queries do not establish coverage. After verifying visibility for
the deployment's principals and workspaces, an operator can attest it with this setting. The
unspecified writer then enables when all prerequisites are true. These configuration controls
do not add live acceptance evidence or change the release status.
Set `PIPELINE_ENABLE_DATABRICKS=false` to hide the provider and disable its default writer.
Live writes use `main.data_mover_staging` by default; the schema must exist and the token owner
must have the required workspace permissions.
The local `make demo` launcher uses an offline emulator.

This guide records the current implementation contract so an operator can prepare an isolated
acceptance environment. It is not approval to connect a production workspace.

## Offline demo

Run `make demo` to seed MSS, MCS-COP, PostgreSQL, and Databricks with fake encrypted credentials.
Databricks is simulated at the reserved `databricks.demo.invalid` hostname, with a running SQL
warehouse, `demo.analytics` and `demo.staging` schemas, and synthetic Delta tables. Browse tables,
inspect schema, extract to another provider, or create/append/replace a Delta destination. CSV can
also feed a Delta destination. No network request, real PAT, or compute charge is involved.

The local writer uses staged, atomic in-memory publication. Table changes last for the current
process/registry generation. Built-in table identities are deterministic across restarts, so saved
routes to unchanged seeded objects remain valid; newly created tables must be recreated. Routes
saved before the identity fix need their Delta table reselected and saved once. Persisted run
history remains in the application database.

Set `PIPELINE_ENABLE_DATABRICKS=false` before `make demo` to omit the provider. Direct
`seed-demo-connections` calls also include it by default unless the provider flag is false. The reserved demo
hostname is accepted only in demo mode. Real-mode HTTPS allowlisting and explicit metadata-visibility
attestation still apply; startup and writer controls are enabled by default subject to prerequisites.
See the [user guide](../user-guide.md#local-seeded-demo) and
[current screenshots](../screenshots/README.md).

## Current live scope

The code uses a workspace personal access token (PAT) with existing SQL warehouses or supported
all-purpose classic clusters. Object support is Unity Catalog base Delta tables with scalar columns.
PATs do not refresh automatically: an owner must replace an expired or revoked token in the
connection form. Use a short-lived, least-privileged token where workspace policy permits it.
The design excludes interactive user OAuth, OAuth M2M, jobs compute, compute creation/resizing/shutdown,
legacy metastore objects, views, complex columns, arbitrary SQL, incremental extraction, upsert, and
external-storage credentials. Formal live support remains unapproved until the provider contract
decision in [DBX-01](../plans/databricks-epic-implementation.md#provider-decisions-that-must-precede-supported-implementation)
and its live probes are reviewed. Keep use within an isolated acceptance environment while that
review remains open.

Manual startup is the default. The `allow_start` policy is available by default, controlled by
`PIPELINE_DATABRICKS_STARTUP_CONTRACT_VERIFIED`. Saving or testing credentials uses
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
- the private catalog/schema `main.data_mover_staging` (the default), reserved for Data Mover
  staging, with the required permissions for the PAT owner; and
- network routes, DNS, proxy, TLS/CA trust, and workspace host allowlisting for the app deployment.

Do not use a real data set or paste credentials into issue comments, screenshots, or logs. The
token owner must not receive broad workspace administration rights. The current checker
requires the SQL path, workspace, compute kind, and compute ID to agree; unsupported IDs and hosts
fail closed.

## Connection setup in an acceptance environment

The connection form is available by default unless `PIPELINE_ENABLE_DATABRICKS=false`.
After the operator has approved an isolated acceptance deployment, open
**Connections → Credentials** and provide:

1. **Workspace URL**: the HTTPS workspace origin, without a path, query, fragment, credentials, or
   nonstandard port. It must match the deployment's `PIPELINE_ALLOWED_HTTPS_HOSTS` policy.
2. **Personal access token**: create it in the approved workspace for an appropriately scoped
   identity. Paste it only into the password field; saved values are encrypted and never displayed.
3. **Compute type**: `warehouse` or `classic_cluster`.
4. **Compute ID** and that compute's SQL **HTTP path**.
5. **Startup policy**: `manual` by default. Choose `allow_start` to authorize startup for this
   connection. It is available unless `PIPELINE_DATABRICKS_STARTUP_CONTRACT_VERIFIED=false`.

Writes use the private `main.data_mover_staging` schema by default. Set
`PIPELINE_DATABRICKS_STAGING_SCHEMA` to override it with another existing private `catalog.schema`.
The token owner must be able to create, write, inspect, and drop staging tables. Destination
permissions are also required. The application does not create the staging catalog or schema.

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

The provider, startup, and writer acceptance controls default to enabled. Metadata visibility
verification defaults to false and requires explicit deployment evidence. The unspecified writer
follows all prerequisites: a false provider or verification flag disables it without preventing
app startup. Explicit writer=true with a false prerequisite is rejected.
The defaults below are the committed settings.
The exact runtime settings and safety checks are defined in `app/config.py` and the connector
registry.

| Setting | Current default | Purpose |
| --- | --- | --- |
| `PIPELINE_ENABLE_DATABRICKS` | `true` | Admit the provider and show its connection UI; set false to hide it. |
| `PIPELINE_ENABLE_DATABRICKS_WRITER` | automatic | Enables only when the provider and all three verification prerequisites are true. Set false to opt out; explicit true rejects disabled prerequisites. |
| `PIPELINE_ALLOWED_HTTPS_HOSTS` | empty | Exact HTTPS host allowlist shared by remote providers. Include only approved workspace hosts. |
| `PIPELINE_DATABRICKS_STAGING_SCHEMA` | `main.data_mover_staging` | Existing private `catalog.schema` used for run-scoped staging. Writes fail closed if it is empty or inaccessible. |
| `PIPELINE_DATABRICKS_METADATA_VISIBILITY_VERIFIED` | `false` | Explicit operator attestation of complete metadata visibility for the deployment's principals and workspaces. Empty query results alone cannot establish coverage. |
| `PIPELINE_DATABRICKS_STARTUP_CONTRACT_VERIFIED` | `true` | Enable SQL actions and explicit or owner-consented compute startup. |
| `PIPELINE_DATABRICKS_WRITER_LIVE_ACCEPTANCE_VERIFIED` | `true` | Allow the enabled destination writer to execute create, append, and replace. |
| `PIPELINE_DATABRICKS_SENSITIVE_TAG_RULES` | `[]` | Exact-case tag rules mapped to fixed safe marker IDs; do not place sensitive values here. |
| `PIPELINE_DATABRICKS_METADATA_POLICY_VERSION` | `1` | Binds reviews to the operator's marker policy revision. |

The provider, metadata, startup, and writer controls are independent. Writes require all relevant
checks and a private staging schema. Runtime checks for permissions, metadata completeness,
sensitive-data policy, compute readiness, publication coordination, and recovery still apply.
Enabling these environment values does not create acceptance evidence.

## Metadata policy and review

The connector inspects Unity Catalog table, schema, catalog, column, and configured tag metadata.
Incomplete, denied, or unattested coverage blocks a transfer. An empty tag result is accepted only
after all scopes are read and the operator has explicitly attested complete metadata visibility.
Tag views can filter rows by permissions without returning an error; a successful query alone
does not prove visibility. Verify the deployment's principals and workspaces before setting the
attestation, and clear it when the verified scope changes. `class.*` classifications are treated as sensitive;
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

PostgreSQL source scalar types are mapped to their Delta equivalents for staging declarations and
append compatibility checks. Append still requires matching column names, order, and mapped types.
PostgreSQL numeric precision above 38 is unsupported by the Delta writer and is rejected rather
than reduced.

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
actual support matrix and any exclusions. Until then, Data Mover does not make a live Databricks support
claim; the offline demo remains available for evaluation.
