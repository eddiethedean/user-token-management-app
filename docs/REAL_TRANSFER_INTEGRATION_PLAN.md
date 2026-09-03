# Real Transfer Integration Plan

Status: implementation specification — revision 2  
Audience: Cursor or another implementation agent  
Scope: replace Data Mover's synthetic connection/catalog/run behavior with real MSS, MCS-COP,
PostgreSQL, and local CSV transfers, using Polars as the dataframe and file-processing engine.

## 0. Cursor execution contract

This document is the controlling implementation specification. Cursor should work phase by phase,
keep the application runnable after every work package, and stop at protocol or security gates rather
than inventing provider behavior.

Rules for the implementation agent:

1. Read `AGENTS.md` if one exists, then `README.md`, `docs/architecture.md`, `SECURITY.md`, the latest
   Alembic migration, and the files named by the active work package.
2. Preserve unrelated user changes and do not rewrite working authentication, session, encryption,
   or audit code to accomplish connector work.
3. Start each work package with failing tests for its acceptance criteria, then implement the minimum
   production code needed to pass them.
4. Keep the fake connector path until the corresponding real path and UI have passed their exit gate.
   A fake must implement the same connector protocol; production must reject fake mode.
5. Never place real credentials, endpoint responses, dataset RIDs, hostnames, or operational data in
   source control. Fixtures must be synthetic or sanitized.
6. Do not infer undocumented MSS/MCS-COP publish semantics. Mark the work package blocked and record
   the exact missing evidence when the protocol-verification gate cannot answer a question.
7. Run formatting, linting, type checking, focused tests, the full test suite, and migration checks at
   the end of every phase. Record commands and results in the PR description.
8. Do not delete `transfer_code/` until Phase 6. It is acceptance evidence, not the target architecture.
9. Use feature flags for incomplete real paths. Never expose a partially implemented writer in the UI.
10. One pull request should cover one work package or one tightly coupled group. Avoid a single
    repository-wide conversion PR.

The plan uses requirement keywords deliberately:

- **MUST** is required for release.
- **SHOULD** is the preferred implementation unless a documented constraint prevents it.
- **MAY** is optional and cannot delay the release.

### 0.1 Fixed decisions versus discovery decisions

Cursor may implement these without further product input:

- Polars is the canonical batch engine.
- psycopg 3 is the PostgreSQL driver.
- real transfers run in a separate durable worker, not a web request or FastAPI background task.
- MSS/MCS-COP locators use dataset/branch/file terminology.
- production cannot enable demo connectors.
- the initial provider/route matrix in section 2.1 is authoritative.
- secrets are decrypted only inside the claimed worker run.
- status, metrics, and logs shown in the UI must be persisted facts.

These require evidence from Phase 0 and must not be guessed:

- exact MSS/MCS-COP discovery, upload, commit, overwrite, and idempotency behavior;
- whether MCS-COP can act as a source;
- whether Foundry append can be represented safely;
- required NIPR trust bootstrap and approved endpoint/network allowlists;
- provider upload/download limits and pagination formats.

## 1. Objective

Integrate the working concepts in `transfer_code/` into the main Data Mover application without
copying the standalone scripts directly into request handlers. The finished application must:

- validate real user-owned connections;
- browse real remote data objects;
- save provider-accurate pipeline definitions;
- execute transfers outside the web request lifecycle;
- persist truthful run status, progress, metrics, and sanitized logs;
- support the transfer routes proven by the reference scripts;
- use Polars instead of pandas and avoid loading an entire large dataset into memory;
- retain encrypted credentials and owner isolation;
- keep deterministic fakes only behind explicit test/development adapters.

Do not treat the existing scripts as production-ready modules. They are protocol references for:

- MSS dataset files to PostgreSQL;
- PostgreSQL to MSS dataset file upload;
- PostgreSQL to MCS-COP dataset file upload.

## 2. Product decisions

### 2.1 Supported providers

The first real-transfer release has these first-class providers:

| Provider ID | UI label | Source | Destination | Remote object model |
|---|---|---:|---:|---|
| `postgres` | PostgreSQL | Yes | Yes | database → schema → table |
| `mss` | MSS | Yes | Yes | dataset RID → branch → file |
| `mcscop` | MCS-COP | No initially | Yes | dataset RID → branch/upload file |
| `csv` | CSV upload | Yes | No | owner-scoped uploaded file |

The required initial route matrix is:

- MSS → PostgreSQL;
- PostgreSQL → MSS;
- PostgreSQL → MCS-COP;
- CSV → PostgreSQL, MSS, or MCS-COP, because the local file source uses the same writer adapters.

Do not expose an unsupported route in the UI. Source capability and destination capability must
come from connector metadata, not from a rule that every provider can do both.

Advana and MongoDB are outside this release because no real transfer implementation for them exists
in `transfer_code/`. Remove them from real pipeline menus and credential setup. Preserve existing
encrypted rows during migration so a later connector can recover them; label them unsupported in an
admin/migration report rather than deleting credentials. Remove the synthetic Databricks wake flow.

### 2.2 Foundry terminology

MSS and MCS-COP are not relational schema/table catalogs. Their UI must use:

- dataset name and RID;
- branch (`master`, `main`, or a discovered/configured branch);
- source file path(s) for reads;
- destination filename for uploads;
- published/preview state when the verified API supports it.

Do not map a dataset RID into `source_schema` or call a Parquet filename a table.

### 2.3 Write-mode semantics

Make semantics explicit per destination:

| Destination | Append | Upsert | Replace |
|---|---|---|---|
| PostgreSQL | insert all rows | conflict-key update or ignore, selected by pipeline policy | transactionally replace table contents/schema according to policy |
| MSS/MCS-COP | new uniquely named file or verified append protocol | unavailable until the remote API's dataset transaction semantics are confirmed | upload/replace a named file and commit/publish using the confirmed API |

The UI must show only modes supported by the selected destination. Do not claim that the current
`preview=true` upload URL publishes data until that behavior has been verified in the target
environment.

## 3. Mandatory protocol-verification gate

Complete this gate before implementing live writes. Record results in a short provider protocol
document under `docs/providers/` and fixtures with secrets removed.

1. Confirm the MSS and MCS-COP base URLs and whether `/api/v1/datasets/.../files` and
   `/api/v2/datasets/.../files/.../upload` are the approved APIs.
2. Confirm authentication header format, token scopes, maximum upload size, pagination, rate limits,
   branch defaults, URL encoding rules, and whether redirects are expected.
3. Confirm how an uploaded preview is committed/published and how overwrite conflicts behave.
4. Capture sanitized success/error response shapes for list, download, upload, and publish actions.
5. Confirm whether dataset discovery is available. If it is not, make dataset RID a credential- or
   pipeline-supplied locator and validate it with a metadata request.
6. Confirm whether the internal `socom_ca_fix` package is required in deployment. Prefer an
   operator-configured NIPR CA bundle/SSL context over process-wide trust-store mutation.
7. Confirm PostgreSQL server versions, minimum privileges, supported data types, maximum identifier
   length, and whether creating schemas/tables is allowed.

No live writer should be enabled until its protocol contract has an integration test against an
approved non-production endpoint.

## 4. Target architecture

Use a ports-and-adapters design. Web routes orchestrate application services; they must not contain
provider HTTP or database code.

```text
Browser / HTMX
    │ save definition, start/cancel run, poll status
    ▼
Pipeline services ───────────────► application PostgreSQL
    │                                  definitions, runs, events, leases
    ▼
DB-backed pipeline worker
    │ decrypt only the two required credential bundles
    ▼
Connector registry
    ├── PostgreSQL connector (psycopg 3 + Polars)
    ├── Foundry connector for MSS (HTTP client + Polars)
    ├── Foundry connector for MCS-COP (HTTP client + Polars)
    └── CSV source connector (Polars)
         │
         ▼
bounded record batches / temporary Parquet spool
```

Add these modules, keeping responsibilities narrow:

- `app/connectors/base.py`: connector protocols, capabilities, typed locators, schema metadata,
  transfer batch/result/error types.
- `app/connectors/registry.py`: provider ID to connector factory and capability lookup.
- `app/connectors/postgres.py`: real health check, catalog discovery, batch extraction, destination
  preparation/COPY/merge/finalization.
- `app/connectors/foundry.py`: shared authenticated HTTP behavior, pagination, retries, file listing,
  download, upload, and publish contract.
- `app/connectors/mss.py` and `app/connectors/mcscop.py`: provider-specific endpoint and capability
  configuration over the shared Foundry connector.
- `app/connectors/csv_source.py`: Polars-backed read of stored CSV content.
- `app/services/catalogs.py`: orchestration/cache for live connector catalogs; remove hard-coded
  synthetic schemas and tables.
- `app/services/pipeline_runs.py`: enqueue, claim, execute, cancel, retry, status transitions, metrics,
  event persistence, and ownership checks.
- `app/services/transfer_engine.py`: provider-neutral extract/load orchestration and verification.
- `app/worker.py`: long-running worker entry point with graceful shutdown and lease recovery.

Connector interfaces should be typed and async at the orchestration boundary even if psycopg/Polars
work is executed in a bounded worker thread. Suggested operations:

- `test_connection(credentials) -> ConnectionHealth`;
- `list_namespaces(credentials) -> list[RemoteNamespace]`;
- `list_objects(credentials, namespace, cursor) -> CatalogPage`;
- `inspect_object(credentials, locator) -> ObjectSchema`;
- `extract(credentials, locator, batch_size) -> iterator[TransferBatch]`;
- `prepare_destination(credentials, locator, schema, write_policy) -> LoadSession`;
- `write_batch(load_session, batch) -> BatchWriteResult`;
- `finalize(load_session) -> DestinationManifest`;
- `abort(load_session)`.

Never pass decrypted credentials through models, route responses, logs, queue payloads, or run-event
details. Decrypt in the worker immediately before connector construction and discard references when
the run ends.

## 5. Polars data path

Add `polars` as a runtime dependency and remove pandas/pyarrow assumptions from transfer code and CSV
inspection. Polars can write Parquet itself; add PyArrow only if a confirmed interoperability case
requires it.

### 5.1 Canonical batch

Use `polars.DataFrame` as the in-process batch type and `polars.Schema` or an application DTO as the
schema contract. Default batch size should be configurable and bounded by both rows and bytes.
Start with 25,000 rows and a 64 MiB target, then tune from real telemetry.

Preserve:

- column order and original names;
- nulls separately from empty strings;
- timezone information;
- integer widths and decimal precision where supported;
- binary values;
- a stable source-row count and byte count.

Reject duplicate column names and unsupported nested/object types before opening a destination
transaction. Surface a column-by-column compatibility report in the UI.

### 5.2 PostgreSQL source

- Use psycopg 3, not `psycopg2`, to match the application's existing dependency.
- Compose identifiers with `psycopg.sql.Identifier`; support schema-qualified names without string
  interpolation.
- Use a server-side cursor and `fetchmany()` to produce bounded Polars frames.
- Read metadata from `information_schema`/`pg_catalog`, including PK/unique constraints.
- Set statement, connection, and idle transaction timeouts.
- Keep the snapshot/transaction behavior explicit so the source does not drift during verification.

### 5.3 PostgreSQL destination

- Create a uniquely named staging table per run, never a shared `temp_upload_table` name.
- Use explicit column lists for every COPY, SELECT, INSERT, and UPDATE.
- Use psycopg 3 COPY with bounded CSV or row blocks generated from each Polars frame.
- Validate/create the destination schema only when the credential has permission and the pipeline
  explicitly requests it.
- For append, COPY/insert every batch.
- For upsert, require one or more conflict columns selected from a real unique/primary constraint;
  support `update` and `ignore` policies explicitly.
- For replace, load staging completely, validate, then perform a short atomic destination operation.
- Always drop/clean staging artifacts on failure or cancellation, with a janitor for abandoned runs.

### 5.4 MSS source

- Paginate the real dataset file list and retain the resolved branch in the run snapshot.
- Ignore only confirmed provider metadata files, not every path beginning with `_` by assumption.
- Stream each CSV/Parquet response into a run-scoped spool file with maximum-size enforcement.
- Use `pl.scan_csv`/`pl.scan_parquet` where practical; collect and emit bounded slices.
- Require compatible schemas across multiple source files or apply a user-approved union policy.
- URL-encode file paths and set connect/read/write/pool timeouts.
- Retry only idempotent reads and retryable status codes with exponential backoff and jitter.

### 5.5 MSS/MCS-COP destinations

- Stream Polars batches to a run-scoped Snappy Parquet spool rather than concatenating a full frame.
- Enforce spool disk quotas and check free space before extraction.
- Upload with a streaming request body, explicit content length when required, and bounded timeouts.
- Retry only when the provider contract proves the operation is idempotent; otherwise reconcile the
  remote file before retrying.
- Publish/commit only after the upload checksum/size is verified.
- Persist the returned remote transaction/file identifier in the run manifest.

### 5.6 CSV source

- Replace manual whole-file CSV parsing with Polars schema inference and validation.
- Keep filename, checksum, row count, and column profile behavior.
- Move uploaded bytes out of the application database for production: use a configured protected
  spool/object store and retain only an opaque storage key in `pipeline_uploads`.
- Keep a small in-database backend for tests and local development.
- Raise the current 5 MiB limit only after streaming upload storage and quotas are implemented.

## 6. Persistence and migrations

Create forward-only Alembic migrations after `0009_csv_sources`.

### 6.1 Pipeline definitions

Add provider-accurate, versioned locator and policy fields:

- `definition_version` integer;
- `source_locator_json` text/JSON;
- `destination_locator_json` text/JSON;
- `write_policy_json` text/JSON;
- optional `source_schema_snapshot_json`;
- optional `destination_schema_snapshot_json`.

Locator examples:

```json
{"kind":"postgres_table","schema":"public","table":"readiness_events"}
```

```json
{"kind":"foundry_dataset","dataset_rid":"ri.foundry.main.dataset...","branch":"master","file_paths":["part-000.parquet"]}
```

```json
{"kind":"foundry_upload","dataset_rid":"ri.foundry.main.dataset...","branch":"master","file_name":"readiness.snappy.parquet"}
```

Backfill only definitions that can be mapped unambiguously. Mark synthetic Advana/MongoDB and fake
catalog definitions as `legacy_unsupported`; do not silently convert fake schema/table names into
real object locators. Keep legacy columns for one compatibility release, stop writing them, then
remove them in a later migration.

### 6.2 Durable run tables

Add:

- `pipeline_runs`: ID, pipeline/version snapshot, owner, status, attempt, timestamps, heartbeat,
  cancel request, stage, source/destination manifests, row/byte counters, checksum, error category,
  sanitized error summary, and worker lease fields.
- `pipeline_run_events`: monotonic sequence, run ID, timestamp, level, stage, message, and sanitized
  structured detail.
- `pipeline_run_batches`: optional/coalesced batch sequence, row/byte counts, duration, status, and
  checksum. Retain only what is needed for observability.

Statuses must have guarded transitions:

`queued → validating → extracting → loading → verifying → succeeded`

Terminal alternatives are `failed` and `cancelled`. A stale leased run returns to `queued` only when
its operation is safe to retry; otherwise mark it `failed_needs_reconciliation`.

Store an immutable snapshot of the pipeline definition used for each run. Editing a saved pipeline
must never change an active or historical run.

### 6.3 Catalog cache

Add a short-lived, owner/provider-scoped catalog cache only if live discovery latency requires it.
Store object locators and display metadata, never credentials. Include `fetched_at`, expiration, and
an explicit Refresh action. A stale cache may render but must be revalidated when saving/running.

### 6.4 Concrete run schema contract

Use the following as the minimum schema. Names may follow repository conventions, but no capability
may be dropped without updating this plan.

`pipeline_runs`:

| Field | Type/constraint | Purpose |
|---|---|---|
| `id` | UUID/string PK | public run identifier |
| `pipeline_definition_id` | nullable FK, `SET NULL` | link to saved definition |
| `user_id` | FK, indexed | ownership boundary |
| `definition_snapshot_json` | non-null JSON/text | immutable versioned route and policy |
| `status` | indexed string | guarded state-machine value |
| `stage` | string | current user-visible stage |
| `attempt` | positive integer | retry attempt |
| `queued_at`, `started_at`, `finished_at` | timestamps | lifecycle |
| `cancel_requested_at` | nullable timestamp | cooperative cancellation request |
| `worker_id`, `lease_token` | nullable strings | exclusive claim identity |
| `lease_expires_at`, `heartbeat_at` | nullable timestamps, indexed | crash recovery |
| `source_rows`, `source_bytes` | non-negative bigint | confirmed extracted totals |
| `loaded_rows`, `loaded_bytes` | non-negative bigint | destination-confirmed totals |
| `source_manifest_json` | nullable JSON/text | frozen source evidence |
| `destination_manifest_json` | nullable JSON/text | committed destination evidence |
| `verification_json` | nullable JSON/text | provider-aware result |
| `error_code` | nullable stable string | machine-actionable failure category |
| `error_summary` | nullable bounded text | sanitized user-facing explanation |
| `retryable` | boolean | computed and persisted retry decision |
| `created_at`, `updated_at` | timestamps | auditing/UI ordering |

`pipeline_run_events` MUST have a unique `(run_id, sequence)` constraint. Event detail must be size
bounded and pass the central redactor before persistence. `pipeline_run_batches` MAY be omitted in
the first slice if batch metrics are represented as coalesced events, but the UI contract must not
depend on one row per batch.

Use optimistic/conditional updates for transitions, for example “update where current status is
`queued` and lease is absent.” A route must never set worker-owned execution states directly.

### 6.5 Definition JSON contracts

Validate JSON through versioned Pydantic discriminated unions before storage and again when a worker
loads a snapshot. Unknown versions or kinds fail closed.

Minimum locator kinds:

- `postgres_table`: `schema`, `table`;
- `foundry_dataset_files`: `dataset_rid`, `branch`, `file_paths` or an explicit all-supported-files
  selector frozen to concrete paths at run start;
- `foundry_upload`: `dataset_rid`, `branch`, `file_name`, and confirmed publication policy;
- `csv_upload`: owner-scoped `upload_id` and immutable checksum.

Minimum write policies:

- `postgres_append`;
- `postgres_upsert` with non-empty `conflict_columns` and `action=update|ignore`;
- `postgres_replace` with an explicit schema policy (`require_compatible` or `recreate`);
- provider-specific Foundry policy defined only after Phase 0.

Do not accept generic arbitrary JSON from form submissions. Routes construct typed locators from
individual validated fields and connector metadata.

## 7. Credentials, TLS, and outbound security

Update `SECRET_PROVIDERS` to reflect actual fields:

- MSS: HTTPS base endpoint, bearer token, optional default dataset RID/branch, TLS CA profile.
- MCS-COP: HTTPS base endpoint, bearer token, optional default dataset RID/branch, TLS CA profile.
- PostgreSQL: host, port, database, username, password, SSL mode, optional CA certificate reference,
  optional connect timeout, and application name.

Do not store arbitrary CA file paths supplied by users. Operators configure named CA profiles and
allowed endpoint suffixes/IP ranges. Provider credentials reference a profile name.

Replace `_set_simulated_connection_health()` with connector calls:

- PostgreSQL: connect, `SELECT 1`, server version, current database/user, and permission probes that
  do not mutate data.
- MSS/MCS-COP: authenticated metadata request and optional default-RID access check.

Persist accurate status (`connected`, `failed`, `untested`, `stale`), checked time, sanitized error,
and latency. Saving credentials should store them as `untested`; testing is a distinct action unless
the UI explicitly says Save and test.

Outbound protections:

- HTTPS-only Foundry endpoints in production;
- operator allowlist for internal domains/networks;
- no credentials in URLs;
- redirects disabled unless the protocol gate approves exact hosts;
- bounded DNS/connect/read/write/pool timeouts;
- response and download size limits;
- log redaction for bearer tokens, passwords, URLs with query strings, and database DSNs;
- least-privileged destination accounts;
- no global TLS verification disable switch.

Use an injectable TLS/bootstrap service. Unit tests mock that boundary instead of importing a fake
top-level `socom_ca_fix` module. If deployment mandates `socom_ca_fix`, wrap it in one adapter called
once during worker startup and mock the adapter in tests. Prefer a configured `ssl.SSLContext` with
the approved NIPR CA bundle.

## 8. Worker and operational model

Do not execute a real transfer inside `/pipeline/run` or a FastAPI background task. Add a separate
worker process and a CLI command such as `python -m app pipeline-worker`.

Worker requirements:

- claim runs atomically from the application PostgreSQL database using row locking/leases;
- limit global concurrency and per-user/per-provider concurrency;
- heartbeat leases and recover from worker death;
- honor cancellation between batches and before irreversible finalization;
- use a unique run-scoped temporary directory with restrictive permissions;
- enforce maximum source bytes, rows, duration, spool bytes, and log/event counts;
- clean temporary data on success/failure and sweep stale directories at startup;
- emit sanitized structured logs correlated by run ID and request ID;
- shut down gracefully without claiming new work;
- make retry policy provider- and stage-aware.

SQLite may remain supported for unit tests, the demo UI, and a session-scoped single-operator
Workbench live mode. Real multi-process or production execution requires application PostgreSQL;
validate that condition at worker startup.

Add deployment configuration for worker replicas, concurrency, batch sizes, quotas, CA bundle,
endpoint allowlists, HTTP timeouts, retry limits, catalog TTL, and retention periods. Update Docker,
Posit Connect guidance, Make targets, `.env.example`, and operations documentation.

### 8.1 Configuration contract

Add typed settings with safe bounds. Suggested names and initial defaults:

| Setting | Initial value | Production behavior |
|---|---:|---|
| `DATA_MOVER_MODE` | `demo` in development/test | MUST be `real` |
| `PIPELINE_WORKER_ID` | generated hostname/process ID | unique per worker |
| `PIPELINE_WORKER_CONCURRENCY` | `2` | tune after load test |
| `PIPELINE_LEASE_SECONDS` | `120` | heartbeat before half-life |
| `PIPELINE_BATCH_ROWS` | `25000` | bounded 1,000–250,000 |
| `PIPELINE_BATCH_TARGET_BYTES` | `67108864` | hard upper bound per in-memory batch |
| `PIPELINE_MAX_RUN_SECONDS` | `14400` | terminate cooperatively, then fail |
| `PIPELINE_MAX_SOURCE_BYTES` | operator-selected | required in production |
| `PIPELINE_MAX_SPOOL_BYTES` | operator-selected | less than provisioned free space |
| `PIPELINE_SPOOL_ROOT` | empty/dev temp root | required protected worker volume |
| `PIPELINE_HTTP_CONNECT_SECONDS` | `10` | bounded |
| `PIPELINE_HTTP_READ_SECONDS` | `120` | bounded |
| `PIPELINE_HTTP_WRITE_SECONDS` | `120` | bounded |
| `PIPELINE_HTTP_RETRY_ATTEMPTS` | `3` | idempotent operations only |
| `PIPELINE_CATALOG_TTL_SECONDS` | `300` | allow manual refresh |
| `PIPELINE_CONNECTION_MAX_AGE_SECONDS` | `900` | stale checks block enqueue |
| `PIPELINE_RUN_RETENTION_DAYS` | `90` | policy-controlled |
| `PIPELINE_EVENT_RETENTION_DAYS` | `30` | preserve terminal summary |
| `PIPELINE_ALLOWED_HTTPS_HOSTS` | empty | required allowlist in real mode |
| `PIPELINE_CA_BUNDLE` | empty | required when system trust is insufficient |

Settings validation MUST reject production real mode when the application database is SQLite. For
session-scoped Workbench live mode, SQLite is allowed but the spool root must be writable, the
endpoint allowlist must be non-empty, limits must be consistent, and production TLS policy remains
enforced whenever `APP_ENV=production`. Secrets such as bearer tokens remain per-user encrypted
credentials, not global settings.

### 8.2 Claiming and idempotency

- Generate a run ID before enqueue and use it as the root correlation/idempotency key.
- A start request for the same pipeline may include a one-time idempotency token so browser retries do
  not enqueue duplicate runs.
- PostgreSQL staging names derive from a validated short run-ID suffix and are safely quoted.
- Foundry destination filenames and transaction IDs must support reconciliation after a timeout. If
  the API has no idempotency facility, a timed-out publish is not automatically retryable.
- Lease recovery must distinguish pre-write, staging-only, upload-uncertain, and committed states.
- At most one worker may own a lease token; every heartbeat/final transition checks that token.

### 8.3 Failure taxonomy

Persist stable error codes rather than exposing raw exceptions:

- `credentials_missing`, `credentials_stale`, `authentication_failed`, `permission_denied`;
- `endpoint_blocked`, `tls_failed`, `connection_timeout`, `provider_unavailable`, `rate_limited`;
- `source_not_found`, `destination_not_found`, `schema_drift`, `unsupported_type`;
- `source_limit_exceeded`, `spool_limit_exceeded`, `run_timeout`;
- `destination_conflict`, `partial_write`, `publish_uncertain`, `verification_failed`;
- `cancelled_by_user`, `worker_lost`, `internal_error`.

Each connector maps provider/driver exceptions to this taxonomy and separately determines retryability.
The raw exception may go to protected server logs after redaction; UI and audit rows receive only the
stable code, sanitized summary, provider request/correlation ID, and operator-safe guidance.

## 9. UI and interaction changes

Keep Hedron/HTMX and progressively replace simulation-specific markup and JavaScript.

### 9.1 Connections

- Show MSS, MCS-COP, and PostgreSQL cards with the real credential fields.
- Replace synthetic “handshake succeeded” text with the last real check result and timestamp.
- Add Test connection and Refresh status actions.
- Remove Wake cluster and fabricated region/latency values.
- Explain that testing and running contact the configured external system.
- Never redisplay stored secret fields; allow replacement only.

### 9.2 Pipeline builder

- Populate providers from connector capabilities.
- Load namespaces/objects through owner-scoped HTMX catalog endpoints.
- For PostgreSQL show database context, schema, table, columns, types, estimated rows, and PK/unique
  constraints.
- For MSS/MCS-COP show dataset RID/name, branch, files, formats, sizes, and last-modified values.
- For Foundry destinations collect dataset RID, branch, destination filename, and publish behavior.
- Show an explicit Refresh catalog control and loading/error/empty states.
- Add conflict-key and upsert-action controls for PostgreSQL upserts.
- Show a schema compatibility preview before saving or running.
- Disable impossible routes and unsupported write modes based on connector metadata.
- Replace decorative hard-coded transforms (“Normalize timestamps”, “Drop 3 empty fields”) with
  actual planned conversions or remove them.

Suggested HTMX endpoints:

- `POST /connections/{provider}/test` (existing route adapted to real checks);
- `GET /pipeline/catalog/{provider}/namespaces`;
- `GET /pipeline/catalog/{provider}/objects?namespace=...`;
- `POST /pipeline/object/inspect`;
- `POST /pipeline/save` with typed provider locator fields;
- `POST /pipeline/runs` with a saved `pipeline_id` form field;
- `POST /pipeline/runs/{run_id}/cancel`;
- `GET /pipeline/runs/{run_id}/status` returning the run-monitor fragment;
- `GET /pipeline/runs/{run_id}/events?after_sequence=...` returning new log fragments.

All routes must enforce authentication, CSRF for mutations, ownership, provider capability, locator
validation, and no-store caching where credentials/run state are involved.

### 9.3 Route behavior contract

The application remains HTML/HTMX-first; these are not public JSON APIs.

- Catalog GETs return bounded fragments and accept opaque pagination cursors. They never accept a
  credential ID belonging to another user; provider is resolved against the authenticated user's slot.
- Object inspection is a mutation-like remote action and SHOULD use POST + CSRF to avoid sensitive
  locators in query logs when a RID/path is user-entered.
- Run creation accepts a saved `pipeline_id` and an idempotency token. The server reloads and validates
  the definition; it never trusts hidden locator JSON from the browser.
- Successful enqueue returns `202` for an HTMX fragment or `303` to the canonical run URL. It does not
  return success merely because a database row was created; the UI label must say Queued.
- Status fragments include the run ID and last event sequence. Poll requests pass the sequence so the
  server returns only new event lines while always returning the authoritative summary.
- Cancellation sets `cancel_requested_at`; it does not claim the run is cancelled until the worker
  reaches a safe boundary and persists the terminal state.
- Retry creates a new run linked to the prior run and copies its immutable definition snapshot only
  after current credentials/locators are revalidated.
- Terminal run pages remain reloadable and must not depend on JavaScript memory.

Expected status codes:

| Condition | Status |
|---|---:|
| fragment/page success | `200` |
| run accepted | `202` or mount-safe `303` |
| malformed/unsupported locator or route | `422` |
| missing ownership-scoped object | `404` |
| stale definition/catalog/connection conflict | `409` |
| provider throttled/unavailable during interactive discovery | `503` with sanitized retry guidance |

Preserve existing mount-aware URL helpers, declared Hedron fragment regions, CSRF enforcement, and
`Cache-Control: no-store` conventions.

### 9.4 Run monitor

Delete the timer-driven synthetic run in `app/static/app.js`. The Run transfer button should enqueue
a server run and begin HTMX polling. Render only persisted facts:

- current status/stage and percent when calculable;
- rows/bytes extracted and loaded;
- actual throughput and elapsed time;
- batch counts derived from run events;
- sanitized worker log events;
- final source/destination manifests and verification result;
- actionable failure category and retry button when safe;
- cancel control for non-terminal runs.

Use indeterminate progress when the source total is unknown. Never invent record counts, file sizes,
throughput, TLS version, regions, checksums, or completion. Poll more slowly for inactive tabs and
stop at a terminal state.

Rename “Saved pipelines” history appropriately and add a real recent-runs section. Saved pipeline
cards should show last run status/time and whether their locator or credentials require revalidation.

## 10. Validation and transfer correctness

Before enqueue:

- confirm the user owns the definition and upload;
- confirm both required credentials exist and have a recent successful check;
- validate route and write-mode capabilities;
- revalidate locators against the remote system;
- validate conflict keys and destination privileges;
- produce a schema compatibility plan.

During the run:

- freeze source metadata/branch and definition version;
- count rows and bytes per batch;
- compute a streaming source checksum over a documented canonical representation or over source
  files, depending on connector guarantees;
- track destination acknowledgements;
- never report loaded rows until the destination confirms them.

Verification must be provider-aware. PostgreSQL can compare committed row effects and optional key
checks. Foundry should compare uploaded byte count/checksum and published file metadata. Do not claim
row-level reconciliation when only file-level verification is available.

## 11. Testing strategy

Keep unit tests offline and deterministic, but mock connector ports rather than third-party modules.

### 11.1 Unit tests

- provider capability/route matrix;
- typed locator validation, including malicious identifiers and encoded file paths;
- credential validation and redaction;
- Polars type mapping and multi-file schema compatibility;
- write-mode planning and PostgreSQL SQL composition;
- state-machine transition guards, lease recovery, cancellation, and retry decisions;
- bounded batching and quota failures;
- run-event sequencing and ownership;
- UI fragments for loading, empty, success, failure, indeterminate, cancelled, and stale states;
- `socom_ca_fix`/TLS bootstrap mocked through the internal adapter.

### 11.2 Contract tests

Use recorded, sanitized HTTP fixtures for MSS/MCS-COP error and pagination shapes. Validate request
method, path encoding, headers, timeouts, redirect policy, and response parsing. Never record tokens.

### 11.3 Integration tests

- Ephemeral PostgreSQL source/destination with mixed types, nulls, quoted identifiers, composite
  keys, schema drift, and all write modes.
- Semblance Foundry simulator (`tests/simulators/foundry.py`) that supports listing, streaming
  downloads, preview upload, upload failures, and Bearer auth against sanitized fixtures.
- Worker crash/restart during extraction, upload, and finalization.
- End-to-end browser flow: configure → test → browse → save → run → poll → verify result.
- Approved non-production MSS and MCS-COP smoke tests behind opt-in markers and environment secrets.

No test may depend on a developer's `cred.py`, mutate the host CA store, or make an accidental live
request. Live tests require an explicit marker and deny-by-default environment flag.

## 12. Documentation and dependency updates

Update:

- `pyproject.toml` and `requirements.txt`: add Polars and any confirmed HTTP/worker dependencies;
  standardize on psycopg 3 and remove standalone `psycopg2`, pandas, and unnecessary PyArrow usage.
- `.env.example`: worker, quota, TLS, allowlist, catalog, timeout, retry, and retention settings.
- `README.md`: stop calling real-transfer mode a simulation; retain a clearly separated offline demo.
- `docs/architecture.md`: worker trust boundary, connectors, spooling, and run persistence.
- `docs/user-guide.md`: real credential risks, provider-specific object selection, run/cancel/retry.
- `docs/deploy.md`: web + worker deployment, CA bundle, network egress, disk sizing, DB requirements.
- `SECURITY.md`: outbound SSRF controls, decrypted credential lifetime, worker isolation, log redaction,
  spool handling, and provider token rotation.
- `CONTRIBUTING.md`: fake connector and opt-in live-test workflow.

Retain an explicit `DATA_MOVER_MODE=demo|real` setting during migration. Production must refuse
`demo`; tests default to fake connectors. The UI must visibly label demo mode and never mix demo
catalog entries with real credentials.

### 12.1 File-level change map

Cursor should use this as the default repository map. New filenames may be adjusted to match local
conventions, but responsibilities should not be collapsed into the pipeline route.

| Area | Existing files to change | New files expected |
|---|---|---|
| Dependencies/config | `pyproject.toml`, `requirements.txt`, `.env.example`, `app/config.py` | none |
| Models/migrations | `app/models.py`, `app/schema.py`, migration chain | `migrations/versions/0010_*.py` and later staged migrations |
| Credential providers | `app/services/secrets.py`, `app/services/demo.py`, `app/ui/routes/security.py`, security UI partials | connector/TLS error-redaction helpers |
| Connector domain | replace synthetic duties in `app/services/catalogs.py` | `app/connectors/{base,registry,postgres,foundry,mss,mcscop,csv_source}.py` |
| Pipeline definitions | `app/services/pipelines.py`, `app/ui/params.py` | locator/policy DTO module |
| Run execution | `app/cli.py`, `app/__main__.py` | `app/services/{pipeline_runs,transfer_engine}.py`, `app/worker.py` |
| UI | `app/ui/routes/pipeline.py`, `app/static/app.js`, `app/static/theme.css`, region declarations | run/catalog fragments if splitting improves reviewability |
| CSV | `app/services/csv_uploads.py`, `app/models.py` | storage backend abstraction |
| Tests | `tests/conftest.py`, existing pipeline/secret/UI tests | connector, worker, state-machine, contract, and integration test modules |
| Operations/docs | `Makefile`, Docker/Connect config, README and security/operator docs | provider protocol notes and sanitized fixtures |

Do not add pandas-based compatibility utilities. Do not import from `transfer_code/` in production.
Port verified behavior into connectors and prove equivalence with tests.

### 12.2 Work-package backlog

Use these IDs in commits/PR descriptions and complete them in dependency order.

| ID | Deliverable | Depends on | Acceptance evidence |
|---|---|---|---|
| `RT-00` | MSS/MCS-COP protocol and CA evidence | none | approved notes + sanitized fixtures |
| `RT-01` | connector protocols, DTOs, capability registry | `RT-00` decisions | unit tests/type checks |
| `RT-02` | settings, TLS policy, redactor, error taxonomy | `RT-00` | security/config tests |
| `RT-03` | definition-v2 and run/event migrations | `RT-01` | SQLite + PostgreSQL migration tests |
| `RT-04` | durable run state machine, leases, cancellation | `RT-03` | concurrency/crash unit tests |
| `RT-05` | fake connectors adapted to new ports | `RT-01` | existing demo tests remain green |
| `RT-06` | PostgreSQL health/catalog/inspection | `RT-01`,`RT-02` | ephemeral PostgreSQL tests |
| `RT-07` | MSS/MCS-COP health/catalog/inspection | `RT-00`–`RT-02` | contract + opt-in smoke tests |
| `RT-08` | real connection/catalog UI | `RT-05`–`RT-07` | HTMX/browser tests |
| `RT-09` | Polars CSV source/storage abstraction | `RT-01`,`RT-03` | inference/streaming/quota tests |
| `RT-10` | PostgreSQL bounded source | `RT-06` | memory and mixed-type integration tests |
| `RT-11` | MSS bounded source | `RT-07` | paginated multi-file contract tests |
| `RT-12` | PostgreSQL destination modes | `RT-06`,`RT-10` | append/upsert/replace/failure tests |
| `RT-13` | worker CLI and transfer orchestration | `RT-04`,`RT-09`–`RT-12` | end-to-end MSS/CSV → PostgreSQL |
| `RT-14` | Parquet spool and Foundry destinations | `RT-07`,`RT-13` | publish/reconcile/quota tests |
| `RT-15` | enqueue/cancel/poll/retry UI | `RT-13`,`RT-14` | reloadable browser E2E tests |
| `RT-16` | retention, janitor, metrics, operations | `RT-13`–`RT-15` | soak/crash/runbook evidence |
| `RT-17` | remove simulation/legacy execution paths | all prior | repository search + full suite |

Every package must include tests and documentation relevant to its behavior; do not defer all tests
or documentation to `RT-16`.

## 13. Implementation sequence for Cursor

Implement in small, reviewable slices. Do not start the next phase until the exit criteria pass.

### Phase 0 — protocol evidence and decisions

1. Complete the mandatory protocol gate.
2. Record actual MSS/MCS-COP API contracts and destination commit behavior.
3. Confirm the route/write-mode matrix and CA strategy.

Exit: approved protocol notes and sanitized fixtures exist; no unresolved question can cause data to
be published incorrectly.

### Phase 1 — domain contracts and schema

1. Add connector DTOs/protocols and capability registry.
2. Add versioned locator/policy models and validators.
3. Add definition migration/backfill and durable run/event tables.
4. Implement the guarded run state machine without real I/O.

Exit: migrations upgrade/downgrade in SQLite test and PostgreSQL; old definitions are preserved or
explicitly marked unsupported; state-machine tests pass.

### Phase 2 — real read-only connectivity

1. Implement TLS/outbound policy and sanitized connector errors.
2. Implement PostgreSQL, MSS, and MCS-COP health checks.
3. Implement real catalog discovery and inspection with cache/refresh.
4. Update Connections and builder UI to show real objects.

Exit: users can test connections and browse approved non-production objects; no transfer writes occur.

### Phase 3 — Polars sources and PostgreSQL destination

1. Convert CSV inspection/read to Polars.
2. Implement PostgreSQL and MSS bounded extraction.
3. Implement PostgreSQL staging, append/upsert/replace, cleanup, and verification.
4. Deliver CSV → PostgreSQL and MSS → PostgreSQL end to end through the worker.

Exit: route integration tests pass with bounded memory and truthful persisted metrics.

### Phase 4 — Foundry destinations

1. Implement Parquet spooling and quotas.
2. Implement MSS/MCS-COP upload, confirmed publish semantics, reconciliation, and verification.
3. Deliver PostgreSQL/CSV → MSS/MCS-COP.

Exit: approved non-production smoke tests prove upload visibility and failure recovery without
duplicate or falsely successful publishes.

### Phase 5 — real run UI

1. Add enqueue/cancel/status/event endpoints.
2. Replace the JavaScript simulation with HTMX polling.
3. Add recent runs, manifests, retry eligibility, and accurate error states.
4. Remove all fabricated metrics/catalog code and simulation copy from real mode.

Exit: every displayed metric comes from persisted worker state; refresh/resume works during a run.

### Phase 6 — hardening and cleanup

1. Add load, soak, crash-recovery, security, and quota tests.
2. Add retention/janitor jobs and operational alerts.
3. Update all operator/user/security documentation.
4. Archive or remove `transfer_code/` only after every reference behavior is covered by connector
   tests; never ship both scripts and production paths as competing implementations.

Exit: all definition-of-done items below pass and production deployment has an explicit rollback.

### 13.1 Release and rollback strategy

Roll out by capability, not by replacing every mock at once:

1. Deploy schema additions and connector framework with real execution disabled.
2. Enable real read-only health/catalog functions for an internal allowlisted cohort.
3. Enable CSV/MSS → PostgreSQL in a non-production environment with destination accounts restricted
   to dedicated test schemas.
4. Enable PostgreSQL → MSS/MCS-COP only after publish/reconciliation evidence passes.
5. Enable persisted real run UI, then remove synthetic run controls.
6. Expand users/endpoints/concurrency gradually while monitoring failures, lease recovery, spool use,
   provider throttling, and reconciliation alerts.

Feature flags should be server-side and deny by default, for example connector enablement and writer
enablement per provider. They are rollout controls, not permanent substitutes for capability metadata.

Rollback rules:

- Database migrations in a release must be additive until the new path has survived the observation
  window. Do not drop legacy definition columns in the same release that stops writing them.
- Disabling a writer prevents new claims but does not abandon active runs; workers drain or cancel at
  a safe boundary according to the operator runbook.
- A rollback must not relabel uncertain remote publishes as failed/retryable. Preserve them as
  `publish_uncertain` for reconciliation.
- Keep old web code able to read new nullable columns during the compatibility window.
- Credential ciphertext format remains backward compatible; never require users to re-enter secrets
  solely because application code rolled back.

Before production enablement, operators must have runbooks for stuck leases, partial PostgreSQL
staging tables, uncertain Foundry uploads, spool exhaustion, credential revocation, CA rotation,
worker shutdown, and audit export.

### 13.2 Measurable release gates

Record actual benchmark results; the values below are minimum correctness gates, not promised product
throughput:

- Peak resident memory remains within configured batch/spool overhead and does not grow linearly with
  source row count during a dataset at least ten times larger than the batch size.
- No test or production transfer creates more than the configured concurrent source/destination
  connections per worker.
- Cancellation is observed by the worker within one completed batch or one bounded HTTP operation.
- A killed worker's expired pre-write/staging run is reclaimed within two lease periods.
- Duplicate enqueue with the same idempotency token creates exactly one run.
- Logs/events contain none of the seeded sentinel tokens, passwords, DSNs, sensitive query strings,
  or raw data-cell values.
- A browser reload during queued/running/terminal states renders the same authoritative status.
- Source/destination row and byte metrics never decrease and loaded totals never exceed confirmed
  extracted totals unless the documented destination representation explains the difference.
- Migration upgrade from a copy of the current schema preserves every user secret, upload, and
  pipeline definition, including definitions marked legacy unsupported.

## 14. Definition of done

- No production catalog, connection check, transfer, progress value, or run log is synthetic.
- The supported route matrix exactly matches connector capabilities shown in the UI.
- MSS/MCS-COP use dataset/branch/file terminology and validated RIDs.
- All dataframe/file processing uses Polars; transfers operate in bounded memory.
- Web requests only enqueue/control runs; a durable worker executes them.
- Credentials are decrypted only inside the worker execution boundary and never persisted in runs.
- SQL identifiers are safely composed and every load uses explicit columns.
- HTTP calls have TLS verification, allowlists, size limits, bounded timeouts, safe retries, and
  sanitized errors.
- Every run has an immutable definition snapshot, valid state history, real metrics, and a provider-
  appropriate verification manifest.
- Cancellation, worker crash, stale lease, partial upload, schema drift, quota exhaustion, and
  destination conflict are tested.
- Unit/contract/integration/UI tests pass; live tests remain explicit and opt-in.
- Demo mode remains isolated and unmistakably labeled; production cannot enable it.
- Documentation describes actual behavior and operational requirements.

## 15. Explicit non-goals for this release

- Advana/Databricks or MongoDB transfers;
- a general transformation language or arbitrary user code;
- scheduling/cron beyond manual enqueue and safe retry;
- distributed processing beyond bounded single-run Polars execution;
- public REST API exposure;
- claiming row-level Foundry verification when only file-level evidence exists.
