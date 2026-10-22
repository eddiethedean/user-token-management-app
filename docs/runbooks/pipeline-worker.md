# Pipeline runtime runbook

The Hedron app owns transfer execution, lease recovery, and retention cleanup. The browser never
receives provider credentials. In a Connect or Workbench deployment, the app process runs the
background runtime inside the same FastAPI lifecycle as the UI.

## Start

On the web host (or in the Connect content), start the application:

```bash
python -m app serve
```

The app attaches a FastAPI background task to each newly queued run and starts a lightweight
supervisor at startup to recover queued/expired runs and run the retention janitor periodically.
There is no standalone pipeline-worker or pipeline-janitor service. Restart the Hedron app after
correcting an operational failure; its lifecycle supervisor recovers queued work and performs
retention cleanup.

## Database audit volume

PostgreSQL deployments use `LISTEN/NOTIFY` by default. After the initial recovery inspection,
empty queues have no periodic queue-check queries. Database triggers notify each process when
committed changes affect `pipeline_runs`, `databricks_compute_attempts`, or Databricks publication
recovery. Owner activation also wakes queued-run recovery. Notifications carry an empty payload
and are delivered only on commit. Ordinary row counters and run-lease heartbeats do not notify.

Before deploying this behavior, run `make migrate` to apply `0027_queue_notifications`, then
restart all Data Mover content processes. The migration adds PostgreSQL functions/triggers; it
does not modify saved runs or credentials. Each process uses one additional persistent autocommit
database connection, outside the application's SQLAlchemy pool. The database endpoint must
support session-level `LISTEN` (a direct connection or session pooling, not transaction pooling).
The normal migration role needs permission to create the function and triggers.

The listener is established before inspecting durable queue state, both on startup and reconnect.
This prevents work committed during a restart/disconnection from being missed. Known future
retries, compute observations, and lease expirations set local timers; these continue recovery
without a new notification. Heartbeat extensions are rechecked at the previously known lease
deadline. The listener checks its socket/shutdown flag locally without issuing SQL. TCP keepalives
help detect broken connections; they are network traffic rather than queue queries.

If notifications cannot connect or the connection fails, the app logs a warning and falls back to
recovery polling while reconnecting. Both initial connection failures and established-listener
disconnects enforce the fallback interval before another connection attempt, so a flapping
listener cannot trigger rapid reconnects and repeated recovery scans.
SQLite and `PIPELINE_QUEUE_NOTIFICATIONS_ENABLED=false`
also use this fallback. Its default interval is 60 seconds. Earlier releases defaulted to two
seconds, producing about 43,200 idle cycles per continuously running process per day. A 60-second
fallback delay reduces that to about 1,440, roughly 97% fewer. Multiple processes multiply totals;
actual log bytes also depend on auditing and other application activity.

For immediate mitigation on an older deployment, or to keep fallback queries infrequent, set:

```dotenv
PIPELINE_BACKGROUND_POLL_SECONDS=60
```

Restart the content processes so all pick up the setting, coordinating with active transfers.
This interval override works with earlier app versions without a migration; eliminating idle
polling requires the new code and notification migration. Change or remove any existing `=2`
override so it cannot reintroduce rapid polling during fallback.
See Posit's [content environment-variable settings](https://docs.posit.co/connect/user/content-settings/#setting-environment-variables).

Run submissions still trigger their own immediate response background task. The supervisor
recovers work when the local execution lock was busy. Active transfer heartbeats renew leases
at roughly one-third of `PIPELINE_LEASE_SECONDS` (40 seconds by default). The retention janitor
still performs maintenance at `PIPELINE_JANITOR_INTERVAL_SECONDS` (one hour by default), even
with empty queues. Browser activity and active work also produce database queries.

`LOG_LEVEL` only controls application output; it does not suppress PostgreSQL's security auditing.
Keep the database audit policy in place. After restart, check for
`Queue notifications connected; idle queue polling suspended`, and ask the administrators to
confirm queue-check statements stop while idle. A notification-unavailable warning means polling
is active. Check for extra/older content processes still polling, and verify a new run and a
Databricks startup observation. Notifications are wakeup hints; existing database claims, leases,
and destination fences remain authoritative across processes.

## Real mode requirements

- `DATA_MOVER_MODE=real`
- PostgreSQL application database (`postgresql+psycopg://…`);
- `PIPELINE_SPOOL_ROOT` writable
- `PIPELINE_ALLOWED_HTTPS_HOSTS` listing every Foundry hostname
- PostgreSQL and Foundry writers default to enabled; an operator can disable one with its
  `PIPELINE_ENABLE_*_WRITER` setting

Route compatibility is derived from registered connector capabilities: MSS, MCS-COP, and PostgreSQL
are source- and destination-capable, while CSV is source-only. Same-system source and destination
objects must not overlap, and destination writers must remain enabled. The
worker rechecks capabilities, object overlap, and writer policy from the frozen snapshot before
opening either connector.

Production refuses `DATA_MOVER_MODE=demo` and refuses SQLite.

The spool directory must exist before startup and be writable by the app process. It is a local
staging area, not a shared data store. Use the same application database and encryption key ring
across app restarts.

## Databricks and demo boundaries

`make demo` enables an offline Databricks warehouse and local Delta source/destination routes.
Emulator table contents last only for the process; built-in seed identities remain stable across
restarts. The application database retains saved routes and run history. The live panel's stage
progress and packet motion are presentation only; use persisted status, counts, and impact for
operational decisions. Motion stops outside loading and for reduced-motion preferences.

Databricks connections and compute controls default to enabled. Live transfers require explicit
metadata visibility verification; the unspecified writer follows its prerequisites. Configure the
workspace allowlist and private staging schema, and review the [provider operation controls](../providers/databricks.md).
An uncertain Delta publication retains its target fence and may retain a staging table. Inspect
**Databricks publication** in the run review and follow the provider's compute-family recovery
procedure. A reconciliation review does not prove commit or authorize dropping an unresolved stage.

## Crash and lease recovery

Each task renews its lease about every one-third of `PIPELINE_LEASE_SECONDS` from an independent
database session. Renewal is an atomic compare-and-set on the run ID, token, worker-owned status,
and unexpired lease. State and counter writes also reload the database row and reject a stale or
expired token. An expired lease before destination writes requeues the run. An expired lease during
load/verify marks `failed_needs_reconciliation` so operators can inspect the destination instead of
blindly retrying.

Timed-out Foundry uploads and lost PostgreSQL commit acknowledgements are `publish_uncertain` and
are not auto-retried. The same reconciliation
block applies when a connector has committed the destination but the app cannot persist the final
successful run state.

PostgreSQL destination preparation, staged COPY, and replace/recreate swaps remain uncommitted on a
dedicated connection until finalization. Abort or connection loss rolls the transaction back and
preserves the live table. The `drop_abandoned_staging` helper exists for manual cleanup of legacy
`dm_stage_*` tables; the current transactional path does not rely on the periodic janitor to remove
PostgreSQL staging tables.

Run-event sequences come from an atomic counter on `pipeline_runs`. Enqueue uses the database's
owner/token uniqueness constraint directly, so simultaneous submissions with one idempotency token
return the same run and create only one queued event.

## Reconciliation review

When a run is `failed_needs_reconciliation`, inspect the destination using the provider's native
tools and compare it with the run's persisted row counts, schema manifest, remote ID, and event
feed. In the Pipeline monitor, use **Record reconciliation review** only after that inspection.
This records an operator event for auditability and unlocks the explicit **Run again** control; it
does not mutate the destination or claim that an uncertain write was safe. A new run should be
started only after the operator has confirmed the destination state and chosen a safe write policy.

## Logs

Never log tokens, passwords, DSNs, or cell values. Connector errors use the stable taxonomy in
`app/connectors/errors.py`. Set `LOG_FORMAT=json` for ingestion and keep `LOG_LEVEL=INFO` during
normal operation. The run ID is the primary reference after enqueue; the enqueue request's
`reference_id` is still useful for locating the original submission.

Important events are `pipeline.run.queued`, `pipeline.run.blocked`, `pipeline.run.completed`,
`pipeline.run.failed`, `pipeline.run.cancelled`, and `pipeline.lease.heartbeat_failed`. Search by `run_id` and inspect
`stage`, `error_code`, `retryable`, and `data_impact`. A `data_impact` of `uncertain` is a safety
stop, not a suggestion to retry. Use the provider's native tools and the persisted manifests/events
before recording reconciliation review.

A `pipeline.run.blocked` event is a pre-write sensitive-data safety stop. Review the run's safe
findings and saved decisions, resolve the required actions or select an allowed source, then enqueue
a new run. Retrying the unchanged blocked run cannot resolve the finding.

Enqueue preflight does not repeat the content scan. The worker scans the complete extracted source
and resolves saved Hash/Remove choices before destination staging, including findings in later
batches. A selected action in a failed or cancelled review does not prove that the destination
committed it; inspect the separate execution and destination outcomes. Uncertain publication still
requires reconciliation through the procedure above.

Before resolving a block with Remove, confirm approval to drop that column from an existing
PostgreSQL destination: append/upsert also remove its prior values transactionally. Before rotating
the active credential-encryption key, coordinate downstream uses of hashed identifiers because new
guardrail digests change. Protect raw source temporary files and stored CSV uploads as well as the
encrypted scan spool. See [SD-27](../../SECURITY.md#sd-27--enforce-sensitive-data-decisions-before-destination-staging)
and the [production security gate](../../SECURITY.md#production-security-gate)
for detector scope, marker configuration, key scope, and deployment controls.

The in-process janitor drops expired events, terminal runs, catalog cache rows, and old spool files.
Restart the app after correcting a runtime failure so the lifecycle supervisor can resume recovery
and cleanup. See the [diagnostic event dictionary](../diagnostics-event-dictionary.md) for the
allowlisted schema and permitted traceback behavior.
