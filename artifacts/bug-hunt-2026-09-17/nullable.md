## Description

Upserting into an ordinary nullable UNIQUE key silently discards distinct incoming rows whose key is NULL. The staging deduplication groups NULLs together even though the destination UNIQUE constraint treats them as distinct.

Verified on commit `1865af308509d2b50cf52c3eda64a1654331a4d5`.

## Labels

bug, high-priority. Applied existing repository label: bug; other labels are recommendations.

## Affected Components

- [app/connectors/postgres.py:500](https://github.com/eddiethedean/user-token-management-app/blob/1865af308509d2b50cf52c3eda64a1654331a4d5/app/connectors/postgres.py#L500) — PostgresConnector.finalize
- [app/services/transfer_engine.py:441](https://github.com/eddiethedean/user-token-management-app/blob/1865af308509d2b50cf52c3eda64a1654331a4d5/app/services/transfer_engine.py#L441) — execute_transfer verification

## Steps to Reproduce

Create `nullable_keys(id integer UNIQUE, value text)`. Load `(NULL, 'first')` and `(NULL, 'second')` with `PostgresUpsertPolicy(conflict_columns=['id'], action='ignore')`. Compare with a direct PostgreSQL INSERT using ON CONFLICT (id) DO NOTHING.

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


def test_nullable_unique_upsert_loses_rows(postgres_credentials):
    with connect(postgres_credentials, connector_settings()) as conn:
        conn.execute('CREATE TABLE nullable_keys (id integer UNIQUE, value text)')
    frame = pl.DataFrame({'id': [None, None], 'value': ['first', 'second']},
        schema={'id': pl.Int32, 'value': pl.String})
    manifest = load(postgres_credentials, 'nullable_keys', frame,
        PostgresUpsertPolicy(conflict_columns=['id'], action='ignore'))
    with connect(postgres_credentials, connector_settings()) as conn:
        rows = conn.execute('SELECT id, value FROM nullable_keys').fetchall()
        conn.execute('CREATE TABLE direct_control (id integer UNIQUE, value text)')
        conn.execute("INSERT INTO direct_control VALUES (NULL, 'first'), (NULL, 'second') ON CONFLICT (id) DO NOTHING")
        assert conn.execute('SELECT count(*) FROM direct_control').fetchone() == (2,)
    print('nullable upsert:', rows, 'manifest rows:', manifest.rows)
    assert rows == [(None, 'second')]
```

</details>

## Expected Behavior

Both rows are inserted for a normal UNIQUE constraint. PostgreSQL's own ON CONFLICT control inserts two rows. NULLS NOT DISTINCT constraints, where supported, need their own distinct handling.

## Actual Behavior

The connector commits only `(NULL, 'second')`, returns `manifest.rows == 1`, and raises no error. The same two-row direct PostgreSQL INSERT produces two rows.

## Root Cause Analysis

`finalize()` always uses SELECT DISTINCT ON (conflict_columns) before INSERT. DISTINCT ON collapses NULL keys, while a normal UNIQUE constraint does not. This affects both upsert actions and composite keys containing a NULL. Transfer completion records the counts without detecting that this deduplication removed nonconflicting rows.

## Impact

User: successful-looking transfers are incomplete. Data: valid records are permanently omitted from the destination unless rerun after repair. Production: silent incomplete downstream reports and repeated loss on scheduled/manual repeat runs.

## Suggested Fix

Make staging deduplication follow the destination key's actual NULL semantics. Preserve every row containing NULL under ordinary UNIQUE semantics; deduplicate the non-NULL keys deterministically. Inspect NULLS NOT DISTINCT metadata if supporting that variant.

## Test Coverage Gaps

Existing upsert tests use NOT NULL primary/composite keys and conflicts against existing rows. Add both actions, nullable single/composite UNIQUE keys, multiple batches, and NULLS NOT DISTINCT controls.

Baseline: **481 passed, 31 deselected**. The separate audit suite has **15 passing characterization checks**. Environment: Python 3.12.13, PostgreSQL 16.13, Polars 1.44.2, psycopg 3.3.5.

## Severity

High

## Confidence

High — executable reproduction observed locally. Where remote responses or source data are simulated, the boundary is identified above.

## Existing Issue Matches

The search also returned closed #12 because its report contains NULL/COPY terms; that issue concerns literal backslash-N serialization, not upsert deduplication. Closed #16 concerns staging uniqueness errors on duplicate non-NULL keys; its original failure is different from the silent deletion confirmed here. No matching open PR.

Reviewed all open issues (0 at audit start), all 20 closed issues (including the 16 recent bug reports), and all open PRs (0). Searched all-state issues and open PRs separately with each of:

- `"upsert" "NULL"`
- `"DISTINCT ON"`
- `"finalize" "nullable"`

Read the current README, contributor guidance, user guide, provider protocol notes, runtime runbook, and relevant security/limitation sections. No expected limitation explaining this behavior was found.

