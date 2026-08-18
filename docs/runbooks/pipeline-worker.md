# Pipeline worker runbook

Run the web process and the pipeline worker as separate processes. The browser never decrypts
provider credentials or executes transfers.

## Start

```bash
python -m app serve
python -m app pipeline-worker
python -m app pipeline-janitor   # retention; schedule daily
```

Makefile targets: `make serve`, `make pipeline-worker`, `make pipeline-janitor`.

## Real mode requirements

- `DATA_MOVER_MODE=real`
- PostgreSQL application database (`postgresql+psycopg://…`)
- `PIPELINE_SPOOL_ROOT` writable
- `PIPELINE_ALLOWED_HTTPS_HOSTS` listing every Foundry hostname
- Foundry writers remain off until `PIPELINE_ENABLE_MSS_WRITER` / `PIPELINE_ENABLE_MCSCOP_WRITER`

Production refuses `DATA_MOVER_MODE=demo`.

## Crash and lease recovery

Workers heartbeat while a run is claimed. An expired lease before destination writes requeues the
run. An expired lease during load/verify marks `failed_needs_reconciliation` so operators can
inspect the destination instead of blindly retrying.

Timed-out Foundry uploads are `publish_uncertain` and are not auto-retried.

## Logs

Never log tokens, passwords, DSNs, or cell values. Connector errors use the stable taxonomy in
`app/connectors/errors.py`. Use `python -m app pipeline-janitor` to drop expired events, terminal
runs, catalog cache rows, and old spool files.
