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

## Real mode requirements

- `DATA_MOVER_MODE=real`
- PostgreSQL application database (`postgresql+psycopg://…`) for production or multiple app replicas;
  SQLite is supported for a single-operator, session-scoped Workbench deployment
- `PIPELINE_SPOOL_ROOT` writable
- `PIPELINE_ALLOWED_HTTPS_HOSTS` listing every Foundry hostname
- Foundry writers remain off until `PIPELINE_ENABLE_MSS_WRITER` / `PIPELINE_ENABLE_MCSCOP_WRITER`

The route allowlist is fixed to MSS → PostgreSQL, PostgreSQL → MSS/MCS-COP, and CSV →
PostgreSQL/MSS/MCS-COP. Provider role capability alone does not authorize another pairing. The
worker rechecks this allowlist from the frozen snapshot before opening either connector.

Production refuses `DATA_MOVER_MODE=demo` and refuses SQLite. For Workbench SQLite/live mode, run
one app process; its in-process background runtime serializes SQLite transfer and retention work.

The spool directory must exist before startup and be writable by the app process. It is a local
staging area, not a shared data store. Use the same application database and encryption key ring
across app restarts.

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
This records an operator event for auditability; it intentionally does not clear the safety state or
authorize an automatic retry. A new run should be started only after the operator has confirmed the
destination state and chosen a safe write policy.

## Logs

Never log tokens, passwords, DSNs, or cell values. Connector errors use the stable taxonomy in
`app/connectors/errors.py`. The in-process janitor drops expired events, terminal runs, catalog cache
rows, and old spool files. Restart the app after correcting a runtime failure so the lifecycle
supervisor can resume recovery and cleanup.
