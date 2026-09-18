## Description

An ordinary PostgreSQL COPY conversion error sends raw source cell contents into the application's exception logs. The run UI receives a generic error, but the worker logs the underlying database exception and its COPY context.

Verified on commit `1865af308509d2b50cf52c3eda64a1654331a4d5`.

## Labels

bug, high-priority, security. Applied existing repository label: bug; other labels are recommendations.

## Affected Components

- [app/worker.py:180](https://github.com/eddiethedean/user-token-management-app/blob/1865af308509d2b50cf52c3eda64a1654331a4d5/app/worker.py#L180) — process_one generic exception handler
- [app/connectors/postgres.py:471](https://github.com/eddiethedean/user-token-management-app/blob/1865af308509d2b50cf52c3eda64a1654331a4d5/app/connectors/postgres.py#L471) — PostgresConnector.write_batch
- [app/logging_config.py:18](https://github.com/eddiethedean/user-token-management-app/blob/1865af308509d2b50cf52c3eda64a1654331a4d5/app/logging_config.py#L18) — configure_logging

## Steps to Reproduce

Run a transfer with the synthetic source string AUDIT_SYNTHETIC_PRIVATE_CELL into an existing integer destination column. The reproduction uses the real worker, transfer engine and disposable PostgreSQL destination, with a fake source reader and injected disposable credentials. Capture the app.worker error log.

1. Check out the commit above and install the project's development dependencies.
2. Save the code below as `/tmp/test_bug.py` (outside `tests/`, because it explicitly loads the shared fixtures).
3. From the repository root run `.venv/bin/python -m pytest /tmp/test_bug.py -c pyproject.toml -s`.

These are **characterization assertions**: passing means the defect was reproduced. Convert them to expected-behavior assertions for regression coverage. PostgreSQL fixtures create disposable local databases and need `postgres`/`initdb`; no production database or live Foundry service is used.

<details>
<summary>Executable reproduction</summary>

```python
"""Characterization tests: pass only when the audited defect is present."""
import hashlib
import json
import uuid
from datetime import datetime, timezone
from unittest.mock import Mock

import polars as pl
import psycopg
import pytest

from app.config import Settings
from app.connectors.base import ColumnSchema, ObjectSchema, TransferBatch
from app.connectors.locators import (
    CsvUploadLocator, FoundryDatasetFilesLocator, PostgresAppendPolicy,
    PostgresUpsertPolicy, postgres_table,
)
from app.connectors.postgres import PostgresConnector, connect, drop_abandoned_staging
from tests.postgres_support import connector_settings

pytest_plugins = ['tests.conftest']


def load(creds, table, frame, policy=None, schema=None):
    connector = PostgresConnector(connector_settings())
    locator = postgres_table('public', table)
    schema = schema or ObjectSchema(locator=locator, columns=tuple(
        ColumnSchema(name=name, data_type=str(dtype)) for name, dtype in frame.schema.items()
    ))
    session = connector.prepare_destination(creds, locator, schema,
        policy or PostgresAppendPolicy(), run_id=str(uuid.uuid4()))
    try:
        connector.write_batch(session, TransferBatch(frame=frame, row_count=frame.height,
            byte_count=frame.estimated_size(), sequence=1))
        return connector.finalize(session)
    finally:
        connector.abort(session)


def test_copy_error_leaks_source_cell_to_worker_log(access_app, postgres_credentials, monkeypatch, caplog):
    from sqlalchemy import select
    from app import worker
    from app.config import get_settings
    from app.database import SessionLocal
    from app.models import User, PipelineRun
    from app.connectors.locators import DefinitionSnapshot
    from tests.test_transfer_engine import _Source
    marker = 'AUDIT_SYNTHETIC_PRIVATE_CELL'
    with connect(postgres_credentials, connector_settings()) as conn:
        conn.execute('CREATE TABLE log_dest (id integer)')
    class Source(_Source):
        def inspect_object(self, credentials, locator):
            return ObjectSchema(locator=locator, columns=(ColumnSchema(name='id', data_type='String'),))
        def extract(self, *args, **kwargs):
            frame = pl.DataFrame({'id': [marker]})
            yield TransferBatch(frame=frame, row_count=1, byte_count=frame.estimated_size(), sequence=1)
    monkeypatch.setattr(worker, 'source_reader_for', lambda _: Source())
    monkeypatch.setattr(worker, 'destination_writer_for', lambda _: PostgresConnector(connector_settings()))
    snapshot = DefinitionSnapshot(name='audit log', source_provider='mss', destination_provider='postgres',
        source=FoundryDatasetFilesLocator(dataset_rid='ri.foundry.main.dataset.audit', branch='master', file_paths=['test.parquet']),
        destination=postgres_table('public', 'log_dest'), write_policy=PostgresAppendPolicy())
    with SessionLocal() as db:
        user = db.scalar(select(User))
        run = PipelineRun(user_id=user.id, status='queued', definition_snapshot_json=snapshot.model_dump_json())
        db.add(run); db.commit()
        assert worker.process_one(db, get_settings(), run_id=run.id,
            credential_resolver=lambda *args, **kwargs: postgres_credentials)
        db.refresh(run)
        assert run.status == 'failed'
    print('source cell is present in worker exception log:', marker in caplog.text)
    assert marker in caplog.text
```

</details>

## Expected Behavior

Record the run ID, safe error code, SQLSTATE and sanitized diagnostic context without logging source cell contents.

## Actual Behavior

The worker log includes `invalid input syntax for type integer: "AUDIT_SYNTHETIC_PRIVATE_CELL"` and a COPY context containing the same marker. The persisted run correctly becomes failed with a generic summary; this does not prevent the log disclosure.

## Root Cause Analysis

PostgresConnector.write_batch lets psycopg errors containing row data escape. process_one calls log.exception before replacing the UI error with a generic summary. The configured log formatter prints the exception chain, and RequestIdFilter only adds a request ID. Run-event redaction does not sanitize exception logs.

## Impact

User/data: confidential source values may be copied into logs with broader readers and longer retention. Production: routine bad-data or schema-mismatch failures can disclose PII or other restricted records. Only synthetic data was used to verify this finding.

## Suggested Fix

Translate database errors at the connector boundary into safe summaries and structured SQLSTATE metadata. Avoid logging raw exception messages, COPY context, and chained causes containing source data; apply a safe exception-logging policy at the worker boundary.

## Test Coverage Gaps

Existing failure tests assert safe user-facing messages but do not inspect formatted log records from a real database conversion failure. Add COPY, constraint violation and parser-error cases with synthetic confidential markers.

Baseline: **481 passed, 31 deselected**. The separate audit suite has **15 passing characterization checks**. Environment: Python 3.12.13, PostgreSQL 16.13, Polars 1.44.2, psycopg 3.3.5.

## Severity

High

## Confidence

High — executable reproduction observed locally. Where remote responses or source data are simulated, the boundary is identified above.

## Existing Issue Matches

All targeted issue/open-PR searches were empty. The runtime runbook explicitly says never to log cell values; this is not an accepted limitation.

Reviewed all open issues (0 at audit start), all 20 closed issues (including the 16 recent bug reports), and all open PRs (0). Searched all-state issues and open PRs separately with each of:

- `"process_one" "logging"`
- `"COPY" "sensitive"`
- `"source" "cell" "logs"`

Read the current README, contributor guidance, user guide, provider protocol notes, runtime runbook, and relevant security/limitation sections. No expected limitation explaining this behavior was found.

