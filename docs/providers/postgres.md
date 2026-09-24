# PostgreSQL protocol notes

Status: implementation notes for the current PostgreSQL connector

Evidence: the current [`app/connectors/postgres.py`](../../app/connectors/postgres.py) connector and
[`tests/test_postgres_connector.py`](../../tests/test_postgres_connector.py); archived transfer
snippets are historical context only.

## Driver and identifiers

- Use psycopg 3 (`psycopg`), not `psycopg2`.
- Compose identifiers with `psycopg.sql.Identifier`. Support `schema.table` without string interpolation.
- Set statement, connection, and idle-in-transaction timeouts from application settings.
- `application_name` defaults to `data-mover`.

## Health check

Connect, then:

- `SELECT 1`
- `SELECT current_database(), current_user, version()`

Do not create objects during a health check.

## Run readiness preflight

Before a run is queued, the connector checks that a PostgreSQL source table still exists and grants
`SELECT`. For a PostgreSQL destination, it checks the schema and table permissions required by the
selected write mode, validates the current schema and type conversions, and rechecks the selected
upsert key against a current unique or primary constraint. These checks are read-only; a failed
preflight returns a safe field-level diagnostic before a worker begins destination writes.

## Catalog

Read namespaces and tables from `information_schema` / `pg_catalog`. Inspect columns, nullability, and primary/unique constraints. Estimated row counts may come from `pg_class.reltuples` and must be labeled as estimates.

## Source extract

- `SELECT` with an explicit column list.
- Server-side cursor and `fetchmany()` into bounded Polars frames (default 25,000 rows / 64 MiB).
- Open a dedicated connection, set `ISOLATION LEVEL REPEATABLE READ` before declaring the named
  cursor, and keep that transaction open until all batches have been consumed. This is explicit even
  when the database default is `READ COMMITTED`.
- Generate the server-side cursor name locally; never derive it from user-supplied schema, table, or
  route values.
- The snapshot covers row extraction only. Schema inspection occurs before extraction on a separate
  connection, and destination verification uses the extracted totals and committed load manifest
  rather than issuing a second source read.

## Destination load

- Create a uniquely named staging table per run (`dm_stage_{short_run_id}`), never a shared `temp_upload_table`.
- COPY with an explicit column list from bounded CSV/row blocks generated from each Polars frame;
  `\\N` is the null marker, so an empty string remains distinct from SQL NULL.
- Write modes:
  - `postgres_append`: insert every staged row.
  - `postgres_upsert`: require one or more conflict columns from a real unique/primary constraint; `action=update|ignore`.
  - `postgres_replace`: load staging completely, then swap/replace according to `schema_policy=require_compatible|recreate`.
- Create the destination schema/table only when the pipeline explicitly requests it and the credential can.
- Keep preparation, staged COPY, and final application in one transaction on a dedicated connection.
  For recreate, the live table is dropped and staging is renamed only during finalization immediately
  before commit. Abort or connection loss rolls back staging and preserves live data.
- The maintenance helper `drop_abandoned_staging` can remove legacy `dm_stage_*` tables manually;
  current loads do not commit intermediate staging or depend on the periodic app janitor for cleanup.

## Verification

Compare committed row effects (`loaded_rows`) against extracted totals from the repeatable-read
extraction. Optional key-count checks apply when an upsert conflict key is present. Do not invent
checksums the destination did not confirm.

## Testing

Connector tests start an ephemeral PostgreSQL with
[testing.postgresql](https://pypi.org/project/testing.postgresql/) (`tests/test_postgres_connector.py`).
They cover health, catalog inspection, mixed-type extract with nulls, the active repeatable-read
isolation level, append/upsert/replace, abort, and in-process staging janitor cleanup. The suite skips
when `initdb` and `postgres` are not available. Local trust auth uses `sslmode=disable`; production
credentials still default to `sslmode=require`.
