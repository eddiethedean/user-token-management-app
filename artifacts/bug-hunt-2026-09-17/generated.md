## Description

Loading a source containing a generated destination column fails during COPY, even when the supplied value equals the destination's generated expression. Mirroring a table with generated columns is affected because source extraction includes every inspected column.

Verified on commit `1865af308509d2b50cf52c3eda64a1654331a4d5`.

## Labels

bug. Applied existing repository label: bug; other labels are recommendations.

## Affected Components

- [app/connectors/postgres.py:404](https://github.com/eddiethedean/user-token-management-app/blob/1865af308509d2b50cf52c3eda64a1654331a4d5/app/connectors/postgres.py#L404) — PostgresConnector.prepare_destination
- [app/connectors/postgres.py:443](https://github.com/eddiethedean/user-token-management-app/blob/1865af308509d2b50cf52c3eda64a1654331a4d5/app/connectors/postgres.py#L443) — PostgresConnector.write_batch
- [app/connectors/postgres.py:477](https://github.com/eddiethedean/user-token-management-app/blob/1865af308509d2b50cf52c3eda64a1654331a4d5/app/connectors/postgres.py#L477) — PostgresConnector.finalize

## Steps to Reproduce

Create `generated_dest(id integer, doubled integer GENERATED ALWAYS AS (id * 2) STORED)`. Append the source row `{id:3, doubled:6}` with matching column names.

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


def test_generated_column_copy_fails(postgres_credentials):
    with connect(postgres_credentials, connector_settings()) as conn:
        conn.execute('CREATE TABLE generated_dest (id integer, doubled integer GENERATED ALWAYS AS (id * 2) STORED)')
    with pytest.raises(psycopg.errors.InvalidColumnReference) as exc:
        load(postgres_credentials, 'generated_dest', pl.DataFrame({'id': [3], 'doubled': [6]}))
    print('generated column:', str(exc.value).splitlines()[0])
```

</details>

## Expected Behavior

Generated destination columns are recognized as non-writable and computed by PostgreSQL; only writable columns are copied and inserted. If unsupported, reject the route with a clear preflight error before loading.

## Actual Behavior

COPY raises `psycopg.errors.InvalidColumnReference: column "doubled" is a generated column`. No row is committed.

## Root Cause Analysis

Preparation clones generated expressions with INCLUDING GENERATED. The writable column list is built from all source columns and used unchanged for COPY and final INSERT. Inspection does not expose generated-column metadata to filter or validate the load.

## Impact

User: otherwise matching table-to-table append/replace/upsert routes fail. Data: the transaction rolls back; no data loss was observed. Production: automated/manual loads cannot mirror tables with generated columns.

## Suggested Fix

Inspect generated/identity metadata, define the writable projection, omit generated columns from staging COPY and destination INSERT, and validate conflicts involving generated fields explicitly.

## Test Coverage Gaps

Connector tests cover plain columns and constraints but no generated columns. Add matching generated-source/destination cases and inputs containing only writable columns.

Baseline: **481 passed, 31 deselected**. The separate audit suite has **15 passing characterization checks**. Environment: Python 3.12.13, PostgreSQL 16.13, Polars 1.44.2, psycopg 3.3.5.

## Severity

Medium

## Confidence

High — executable reproduction observed locally. Where remote responses or source data are simulated, the boundary is identified above.

## Existing Issue Matches

All targeted issue/open-PR searches were empty. No reviewed provider documentation declares generated-column mirroring unsupported.

Reviewed all open issues (0 at audit start), all 20 closed issues (including the 16 recent bug reports), and all open PRs (0). Searched all-state issues and open PRs separately with each of:

- `"generated column"`
- `"INCLUDING GENERATED"`
- `"write_batch" "InvalidColumnReference"`

Read the current README, contributor guidance, user guide, provider protocol notes, runtime runbook, and relevant security/limitation sections. No expected limitation explaining this behavior was found.

