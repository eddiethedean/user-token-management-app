# Databricks connector: operator and acceptance guide

**Release status:** Not released. The connector and local integration are present, but every
Databricks feature flag defaults to off. Keep it off until the acceptance ledger links an approved
provider contract and live evidence for the exact workspace, compute family, routes, and write modes
being enabled.

This guide records the current implementation contract so an operator can prepare an isolated
acceptance environment. It is not approval to connect a production workspace.

## Current scope

The code is intended for an OAuth M2M service principal and existing SQL warehouses or supported
all-purpose classic clusters. Object support is Unity Catalog base Delta tables with scalar columns.
The design excludes PATs, interactive user OAuth, jobs compute, compute creation/resizing/shutdown,
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
workspace, principal, compute kind and ID, and SQL path. Changing any of those values resets consent.
Starting compute may incur provider charges. Data Mover does not stop compute or change its settings.

## Prepare an isolated workspace

Use synthetic tables and a non-production workspace. Platform staff should provision:

- an OAuth M2M service principal with the minimum management permissions to inspect the selected
  compute, plus start permission only for a separately approved startup probe;
- an existing SQL warehouse or eligible all-purpose classic cluster, with the compute ID and SQL
  HTTP path taken from that same workspace and compute;
- Unity Catalog catalogs, schemas, and Delta fixtures that cover allowed and denied visibility,
  tagged and untagged columns, and all proposed publication modes;
- a private catalog/schema reserved for Data Mover staging if writer acceptance is approved; and
- network routes, DNS, proxy, TLS/CA trust, and workspace host allowlisting for the app deployment.

Do not use a real data set or paste credentials into issue comments, screenshots, or logs. The
service principal must not receive broad workspace administration rights. The current checker
requires the SQL path, workspace, compute kind, and compute ID to agree; unsupported IDs and hosts
fail closed.

## Connection setup in an acceptance environment

Only after the operator has approved an isolated acceptance deployment and enabled
`PIPELINE_ENABLE_DATABRICKS`, open **Connections → Credentials** and provide:

1. **Workspace URL**: the HTTPS workspace origin, without a path, query, fragment, credentials, or
   nonstandard port. It must match the deployment's `PIPELINE_ALLOWED_HTTPS_HOSTS` policy.
2. **OAuth service principal client ID** and **OAuth service principal secret**.
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

The current candidate writer creates a run-scoped Delta staging table in the configured private
schema, writes only after the scan and guardrail pass, then submits create, append, or full replace.
The database stores publication intent before staging and tracks committed, unchanged, uncertain,
and cleanup-pending outcomes. A target fence prevents another Data Mover writer from racing while
the outcome is unresolved. Run review exposes the persisted publication state.

Do not treat the candidate SQL statements as a provider contract. Live probes still need to prove
atomicity, governance preservation, exact commit-marker behavior, interaction with external writers,
and each failure window for every claimed compute family. An uncertain publication holds its target
fence; do not retry or manually drop its staging table until recovery determines the commit outcome.
The recovery worker uses the saved connection generation and a deterministic run-owned stage name.
If credentials were rotated, recovery retains a safe operator-visible unresolved state rather than
using new credentials to mutate a stage created with the old connection.

Before retrying after any uncertain outcome, inspect the destination and use the run's
**Databricks publication** section. Recording a reconciliation review does not prove an uncertain
commit; recovery must first confirm the exact Delta history marker. Cleanup completion is separate
from whether the destination changed.

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
