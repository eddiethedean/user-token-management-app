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

Production refuses `DATA_MOVER_MODE=demo` and refuses SQLite. For Workbench SQLite/live mode, run
one app process; its in-process background runtime serializes SQLite transfer and retention work.

The spool directory must exist before startup and be writable by the app process. It is a local
staging area, not a shared data store. Use the same application database and encryption key ring
across app restarts.

## Crash and lease recovery

Background tasks heartbeat while a run is claimed. An expired lease before destination writes
requeues the run. An expired lease during load/verify marks `failed_needs_reconciliation` so operators can
inspect the destination instead of blindly retrying.

Timed-out Foundry uploads are `publish_uncertain` and are not auto-retried.

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
