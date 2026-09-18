## Description

PostgreSQL upsert reserves the user-visible column name dm_row_number without validating or avoiding collisions. Any otherwise valid destination already containing that column fails during staging preparation.

Verified on commit `1865af308509d2b50cf52c3eda64a1654331a4d5`.

## Labels

bug. Applied existing repository label: bug; other labels are recommendations.

## Affected Components

- [app/connectors/postgres.py:409](https://github.com/eddiethedean/user-token-management-app/blob/1865af308509d2b50cf52c3eda64a1654331a4d5/app/connectors/postgres.py#L409) — PostgresConnector.prepare_destination
- [app/connectors/postgres.py:437](https://github.com/eddiethedean/user-token-management-app/blob/1865af308509d2b50cf52c3eda64a1654331a4d5/app/connectors/postgres.py#L437) — LoadSession staging_sequence metadata
- [app/connectors/postgres.py:504](https://github.com/eddiethedean/user-token-management-app/blob/1865af308509d2b50cf52c3eda64a1654331a4d5/app/connectors/postgres.py#L504) — PostgresConnector.finalize

## Steps to Reproduce

Create `sequence_dest(id integer PRIMARY KEY, dm_row_number integer)`. Upsert the row `{id:1, dm_row_number:2}` on id.

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


def test_internal_sequence_name_collision(postgres_credentials):
    with connect(postgres_credentials, connector_settings()) as conn:
        conn.execute('CREATE TABLE sequence_dest (id integer PRIMARY KEY, dm_row_number integer)')
    with pytest.raises(psycopg.errors.DuplicateColumn) as exc:
        load(postgres_credentials, 'sequence_dest', pl.DataFrame({'id': [1], 'dm_row_number': [2]}),
            PostgresUpsertPolicy(conflict_columns=['id']))
    print('sequence name collision:', str(exc.value).splitlines()[0])
```

</details>

## Expected Behavior

Upsert supports this valid, unreserved PostgreSQL column name using a collision-free internal ordering column.

## Actual Behavior

prepare_destination raises DuplicateColumn: column dm_row_number of relation dm_stage_<id> already exists. The transaction is rolled back.

## Root Cause Analysis

Staging first clones all destination columns, then unconditionally adds a BIGSERIAL column named dm_row_number. Finalization also hardcodes that name instead of using a generated collision-free identifier.

## Impact

User: all upserts to affected tables fail regardless of data. Data: no writes are committed. Production: routes using a common row-number field cannot execute without renaming their schema.

## Suggested Fix

Choose an internal sequence name absent from both source and destination columns, store its actual name in LoadSession, and reference that value in finalization.

## Test Coverage Gaps

Existing upsert tests never contain dm_row_number in the user schema. Add collision cases and assert source values with that name remain intact.

Baseline: **481 passed, 31 deselected**. The separate audit suite has **15 passing characterization checks**. Environment: Python 3.12.13, PostgreSQL 16.13, Polars 1.44.2, psycopg 3.3.5.

## Severity

Medium

## Confidence

High — executable reproduction observed locally. Where remote responses or source data are simulated, the boundary is identified above.

## Existing Issue Matches

All targeted searches were empty. Closed #16 concerns primary/unique constraints rejecting duplicate data during COPY; this failure happens earlier because of a generated column-name collision.

Reviewed all open issues (0 at audit start), all 20 closed issues (including the 16 recent bug reports), and all open PRs (0). Searched all-state issues and open PRs separately with each of:

- `"dm_row_number"`
- `"DuplicateColumn"`
- `"upsert" "column name"`

Read the current README, contributor guidance, user guide, provider protocol notes, runtime runbook, and relevant security/limitation sections. No expected limitation explaining this behavior was found.

