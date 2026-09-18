## Description

A normal parameterized Polars Datetime column is created as TEXT in a new PostgreSQL destination. This is the dtype form produced by a Parquet source and used when the transfer engine derives a schema from its first batch.

Verified on commit `1865af308509d2b50cf52c3eda64a1654331a4d5`.

## Labels

bug. Applied existing repository label: bug; other labels are recommendations.

## Affected Components

- [app/connectors/postgres.py:608](https://github.com/eddiethedean/user-token-management-app/blob/1865af308509d2b50cf52c3eda64a1654331a4d5/app/connectors/postgres.py#L608) — _pg_type
- [app/connectors/postgres.py:354](https://github.com/eddiethedean/user-token-management-app/blob/1865af308509d2b50cf52c3eda64a1654331a4d5/app/connectors/postgres.py#L354) — PostgresConnector.prepare_destination
- [app/services/transfer_engine.py:293](https://github.com/eddiethedean/user-token-management-app/blob/1865af308509d2b50cf52c3eda64a1654331a4d5/app/services/transfer_engine.py#L293) — first-batch schema derivation

## Steps to Reproduce

Construct a Polars frame containing a Python datetime, which yields `Datetime(time_unit='us', time_zone=None)`. Pass the frame schema through ColumnSchema as the engine does for Foundry/Parquet sources, load a new PostgreSQL table, and inspect information_schema.columns.

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


def test_parameterized_datetime_becomes_text(postgres_credentials):
    frame = pl.DataFrame({'occurred': [datetime(2026, 9, 17, 12, 34, 56)]})
    load(postgres_credentials, 'dates', frame)
    with connect(postgres_credentials, connector_settings()) as conn:
        result = conn.execute("SELECT data_type FROM information_schema.columns WHERE table_name='dates'").fetchone()
    print('Polars dtype:', frame.schema, 'destination:', result)
    assert result == ('text',)
```

</details>

## Expected Behavior

A timezone-naive datetime is created as TIMESTAMP, with the supported unit/precision preserved or explicitly validated.

## Actual Behavior

The load succeeds but the PostgreSQL destination column type is `text`.

## Root Cause Analysis

`_pg_type()` compares the complete parameterized dtype string against `str(pl.Datetime)`, which is just Datetime. None of the later timestamp/time tests matches a string starting with Datetime, so it falls through to TEXT.

## Impact

User/data: values that were temporal become strings. Production: timestamp filters, date arithmetic, indexing, and destination schema expectations fail or require manual casts. Existing correctly typed timestamp tables are a different path; this reproduction covers new destination creation.

## Suggested Fix

Handle parameterized Polars Datetime explicitly (prefer typed dtype conversion to string heuristics), distinguishing timezone-aware and naive variants and defining precision handling.

## Test Coverage Gaps

Current PostgreSQL load tests use Date, not parameterized Datetime. Add a Parquet/first-batch-schema-to-PostgreSQL integration test and assert the actual database column type.

Baseline: **481 passed, 31 deselected**. The separate audit suite has **15 passing characterization checks**. Environment: Python 3.12.13, PostgreSQL 16.13, Polars 1.44.2, psycopg 3.3.5.

## Severity

Medium

## Confidence

High — executable reproduction observed locally. Where remote responses or source data are simulated, the boundary is identified above.

## Existing Issue Matches

All targeted issue and open-PR searches were empty. This differs from PostgreSQL timestamptz loss: it affects naive Parquet/Polars datetime schema creation and falls through to TEXT.

Reviewed all open issues (0 at audit start), all 20 closed issues (including the 16 recent bug reports), and all open PRs (0). Searched all-state issues and open PRs separately with each of:

- `"_pg_type" "Datetime"`
- `"datetime" "TEXT"`
- `"Parquet" "timestamp"`

Read the current README, contributor guidance, user guide, provider protocol notes, runtime runbook, and relevant security/limitation sections. No expected limitation explaining this behavior was found.

