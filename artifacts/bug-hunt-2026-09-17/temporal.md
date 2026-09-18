## Description

A PostgreSQL timestamptz source is converted to a timezone-naive Polars datetime and a newly created PostgreSQL destination uses TIMESTAMP WITHOUT TIME ZONE. The transfer therefore loses the instant-preserving semantics of the source.

Verified on commit `1865af308509d2b50cf52c3eda64a1654331a4d5`.

## Labels

bug, high-priority. Applied existing repository label: bug; other labels are recommendations.

## Affected Components

- [app/connectors/postgres.py:316](https://github.com/eddiethedean/user-token-management-app/blob/1865af308509d2b50cf52c3eda64a1654331a4d5/app/connectors/postgres.py#L316) — PostgresConnector.extract
- [app/connectors/postgres.py:608](https://github.com/eddiethedean/user-token-management-app/blob/1865af308509d2b50cf52c3eda64a1654331a4d5/app/connectors/postgres.py#L608) — _pg_type
- [app/connectors/postgres.py:639](https://github.com/eddiethedean/user-token-management-app/blob/1865af308509d2b50cf52c3eda64a1654331a4d5/app/connectors/postgres.py#L639) — _polars_type

## Steps to Reproduce

Create a source timestamptz column containing `2026-09-17 08:00:00-04`. Extract it through PostgresConnector and load a new destination using the inspected source schema. Inspect the destination type, then read/cast it under America/New_York.

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


def test_timezone_source_loses_timezone(postgres_credentials):
    with connect(postgres_credentials, connector_settings()) as conn:
        conn.execute('CREATE TABLE timezone_source (occurred timestamptz)')
        conn.execute("INSERT INTO timezone_source VALUES ('2026-09-17 08:00:00-04')")
    connector = PostgresConnector(connector_settings())
    locator = postgres_table('public', 'timezone_source')
    schema = connector.inspect_object(postgres_credentials, locator)
    batch = next(connector.extract(postgres_credentials, locator, batch_rows=100, batch_bytes=10000))
    load(postgres_credentials, 'timezone_dest', batch.frame, schema=schema)
    with connect(postgres_credentials, connector_settings()) as conn:
        result = conn.execute("SELECT data_type FROM information_schema.columns WHERE table_name='timezone_dest'").fetchone()
        conn.execute("SET TIME ZONE 'America/New_York'")
        delta = conn.execute('SELECT EXTRACT(EPOCH FROM d.occurred::timestamptz) - EXTRACT(EPOCH FROM s.occurred) FROM timezone_dest d, timezone_source s').fetchone()
    print('timezone dtype:', batch.frame.schema, 'destination:', result, 'epoch drift seconds:', delta)
    assert result == ('timestamp without time zone',)
    assert delta[0] == 14400
```

</details>

## Expected Behavior

The new destination retains TIMESTAMP WITH TIME ZONE and represents the same instant in every session timezone.

## Actual Behavior

The frame is `Datetime(time_unit='us', time_zone=None)` and the destination is `timestamp without time zone`. In the executable reproduction, interpreting the destination under America/New_York differs from the original instant by 14,400 seconds.

## Root Cause Analysis

`_polars_type()` maps every timestamp type to `pl.Datetime('us')` without timezone metadata. `_pg_type()` maps every PostgreSQL type beginning with timestamp to TIMESTAMP, including timestamp with time zone.

## Impact

User/data: a completed transfer changes temporal semantics without reporting schema drift. Production: date boundaries, ordering, time-based joins, and downstream timestamps can be wrong when consumers use a non-UTC session timezone.

## Suggested Fix

Preserve timezone-aware source types through extraction and use TIMESTAMPTZ for timezone-aware PostgreSQL destinations. Normalize aware values to UTC while retaining awareness; distinguish timestamp without time zone explicitly.

## Test Coverage Gaps

PostgreSQL mixed-type tests cover Date, but no timestamptz values, offset changes, DST boundaries, or non-UTC consumer sessions.

Baseline: **481 passed, 31 deselected**. The separate audit suite has **15 passing characterization checks**. Environment: Python 3.12.13, PostgreSQL 16.13, Polars 1.44.2, psycopg 3.3.5.

## Severity

High

## Confidence

High — executable reproduction observed locally. Where remote responses or source data are simulated, the boundary is identified above.

## Existing Issue Matches

All targeted issue/open-PR searches were empty. Closed #11 concerns Decimal-to-floating-point conversion and is not the same type or code branch.

Reviewed all open issues (0 at audit start), all 20 closed issues (including the 16 recent bug reports), and all open PRs (0). Searched all-state issues and open PRs separately with each of:

- `"timestamptz"`
- `"_pg_type" "Datetime"`
- `"timestamp" "timezone"`

Read the current README, contributor guidance, user guide, provider protocol notes, runtime runbook, and relevant security/limitation sections. No expected limitation explaining this behavior was found.

