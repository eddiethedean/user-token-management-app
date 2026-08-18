# PostgreSQL protocol notes

Status: frozen for the first real-transfer release  
Evidence: `transfer_code/mss_pg.py`, `transfer_code/pg_mss.py`, `transfer_code/pg_mcs.py`, and the application's existing psycopg 3 driver

## Driver and identifiers

- Use psycopg 3 (`psycopg`), not `psycopg2`.
- Compose identifiers with `psycopg.sql.Identifier`. Support `schema.table` without string interpolation.
- Set statement, connection, and idle-in-transaction timeouts from application settings.
- `application_name` defaults to `data-mover`.

## Health check

Connect, then:

- `SELECT 1`
- `SELECT current_database(), current_user, version()`
- Optional non-mutating privilege probe: `SELECT has_schema_privilege(current_user, 'public', 'USAGE')`

Do not create objects during a health check.

## Catalog

Read namespaces and tables from `information_schema` / `pg_catalog`. Inspect columns, nullability, and primary/unique constraints. Estimated row counts may come from `pg_class.reltuples` and must be labeled as estimates.

## Source extract

- `SELECT` with an explicit column list.
- Server-side cursor and `fetchmany()` into bounded Polars frames (default 25,000 rows / 64 MiB).
- Hold a repeatable-read snapshot for the extract so verification does not observe concurrent drift.

## Destination load

- Create a uniquely named staging table per run (`dm_stage_{short_run_id}`), never a shared `temp_upload_table`.
- COPY with an explicit column list from bounded CSV/row blocks generated from each Polars frame.
- Write modes:
  - `postgres_append`: insert every staged row.
  - `postgres_upsert`: require one or more conflict columns from a real unique/primary constraint; `action=update|ignore`.
  - `postgres_replace`: load staging completely, then swap/replace according to `schema_policy=require_compatible|recreate`.
- Create the destination schema/table only when the pipeline explicitly requests it and the credential can.
- Drop staging artifacts on success, failure, and cancellation. A janitor reaps abandoned staging tables.

## Verification

Compare committed row effects (`loaded_rows`) against extracted totals. Optional key-count checks when an upsert conflict key is present. Do not invent checksums the destination did not confirm.

## Testing

Connector tests start an ephemeral PostgreSQL with
[testing.postgresql](https://pypi.org/project/testing.postgresql/) (`tests/test_postgres_connector.py`).
They cover health, catalog inspection, mixed-type extract with nulls, append/upsert/replace, abort,
and staging janitor cleanup. The suite skips when `initdb` and `postgres` are not available. Local
trust auth uses `sslmode=disable`; production credentials still default to `sslmode=require`.
