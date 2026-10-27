## Description

Appending explicit key values into an existing SERIAL primary-key column succeeds, but the associated sequence is not advanced. The next normal application insert using the default key fails with a unique-constraint violation.

Verified on commit `4d6bbeefd5a6b25f39e4f33adc59fa39919f3671` (October 22, 2026). Baseline suite: **972 passed**. Audit suite: **9 passing characterization cases across 6 defects**. Python 3.12.13, Polars 1.44.2, psycopg 3.3.5.

## Labels

`bug`, `priority: high`.

## Affected Components

- [app/connectors/postgres.py:781](https://github.com/eddiethedean/user-token-management-app/blob/4d6bbeefd5a6b25f39e4f33adc59fa39919f3671/app/connectors/postgres.py#L781) — `prepare_destination` generated-column discovery.
- [app/connectors/postgres.py:1007](https://github.com/eddiethedean/user-token-management-app/blob/4d6bbeefd5a6b25f39e4f33adc59fa39919f3671/app/connectors/postgres.py#L1007) — `finalize` advances only discovered identity sequences.
- [app/connectors/postgres.py:1269](https://github.com/eddiethedean/user-token-management-app/blob/4d6bbeefd5a6b25f39e4f33adc59fa39919f3671/app/connectors/postgres.py#L1269) — `_advance_identity_sequences`.

## Steps to Reproduce

1. Check out the audited commit and install its development dependencies. PostgreSQL cases need local `postgres`/`initdb` binaries, as in the repository test suite.
2. Save the following test outside `tests/` as `/tmp/test_bug.py`.
3. From the repository root run `.venv/bin/python -m pytest /tmp/test_bug.py -c pyproject.toml -s`.

These are **characterization assertions**: they pass when the defect is present. Invert them when adding regression tests. The code reuses test helpers from this exact commit. Databases are disposable local fixtures and all credentials are synthetic; no live Databricks or Foundry service is contacted.

<details>
<summary>Executable reproduction</summary>

```python
import uuid
from datetime import timedelta
from unittest.mock import Mock
import polars as pl
import psycopg
import pytest
from app.config import get_settings
from app.connectors.base import ObjectSchema, ColumnSchema, TransferBatch
from app.connectors.errors import ConnectorError, TransferErrorCode
from app.connectors.locators import DefinitionSnapshot, PostgresAppendPolicy, DatabricksCreatePolicy, postgres_table
from app.connectors.postgres import PostgresConnector, connect
from app.database import SessionLocal
from app.models import PipelineRun, DatabricksPublication, utcnow
from app.services.pipeline_runs import claim_run
from app.services.transfer_engine import execute_transfer
from tests.postgres_support import connector_settings
pytest_plugins = ['tests.conftest']


def transfer_frame(credentials, table, frame, schema=None):
    connector = PostgresConnector(connector_settings())
    locator = postgres_table("public", table)
    schema = schema or ObjectSchema(
        locator=locator, columns=tuple(ColumnSchema(n, str(t)) for n, t in frame.schema.items())
    )
    session = connector.prepare_destination(
        credentials, locator, schema, PostgresAppendPolicy(), run_id=str(uuid.uuid4())
    )
    try:
        connector.write_batch(
            session,
            TransferBatch(
                frame=frame, row_count=frame.height, byte_count=frame.estimated_size(), sequence=1
            ),
        )
        return connector.finalize(session)
    finally:
        connector.abort(session)


def test_serial_import_leaves_next_generated_key_colliding(postgres_credentials):
    with connect(postgres_credentials, connector_settings()) as conn:
        conn.execute("CREATE TABLE serial_target (id SERIAL PRIMARY KEY, label TEXT)")
    result = transfer_frame(
        postgres_credentials, "serial_target", pl.DataFrame({"id": [1, 2], "label": ["a", "b"]})
    )
    assert result.rows == 2
    with connect(postgres_credentials, connector_settings()) as conn:
        assert conn.execute("SELECT id FROM serial_target ORDER BY id").fetchall() == [(1,), (2,)]
        with pytest.raises(psycopg.errors.UniqueViolation):
            conn.execute("INSERT INTO serial_target(label) VALUES ('next generated')")
    print("Successful serial import causes next ordinary INSERT to violate primary key")
```

</details>

## Expected Behavior

A successful import leaves future generated keys outside the imported key range. If the connector cannot safely advance a sequence under the granted permissions or concurrency model, it should reject the operation before publication.

## Actual Behavior

A two-row import of IDs 1 and 2 reports `manifest.rows == 2` and commits both rows. `INSERT INTO serial_target(label) VALUES (...)` then fails with `psycopg.errors.UniqueViolation` because the default sequence still generates 1.

## Root Cause Analysis

The discovery query includes only `is_generated = ALWAYS` or `is_identity = YES`. PostgreSQL SERIAL is a sequence-backed DEFAULT, with neither flag. Its supplied ID never enters `supplied_identity_columns`, so finalization bypasses the sequence synchronization used for SQL IDENTITY columns.

## Impact

Users see a successful transfer that breaks subsequent writes by the destination application. Imported rows remain intact, but ordinary inserts fail until the sequence is advanced. The affected table can be a live production table.

## Suggested Fix

Discover owned sequence-backed defaults as well as SQL IDENTITY columns, for example through `pg_get_serial_sequence` and catalog dependencies. Apply the existing safe locking, cache, privilege, and direction checks when explicit sequence-backed keys are loaded; avoid blindly changing arbitrary default expressions.

## Test Coverage Gaps

Tests cover generated expressions and SQL IDENTITY keys, but do not import explicit SERIAL keys and then issue an INSERT that omits the key. Add SERIAL/BIGSERIAL append, upsert, rollback, non-default sequence start/increment, and concurrent generated-insert cases.

## Severity

High

## Confidence

High — executable reproduction passed locally. Simulated boundaries are explicitly identified above.

## Existing Issue Matches

Reviewed all open issues (0 at audit start), the 98 returned closed issues, and both open PRs (#151 and #153, documentation-only). Targeted all-state issue searches and open-PR searches were also performed. README, user guide, provider documentation, and relevant tests were checked for documented limitations. Issue searches: `serial`, `serial sequence`, `_advance_identity_sequences`; open PR search: `SERIAL`. #31 concerns COPY into generated expression columns, not stale SERIAL sequences after a successful import. No matching report or open fix found.
