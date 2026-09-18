## Description

The documented manual staging-cleanup helper can drop ordinary application tables outside the `dm_stage_` prefix.

Verified on commit `1865af308509d2b50cf52c3eda64a1654331a4d5`.

## Labels

bug, high-priority. Applied existing repository label: bug; other labels are recommendations.

## Affected Components

- [app/connectors/postgres.py:672](https://github.com/eddiethedean/user-token-management-app/blob/1865af308509d2b50cf52c3eda64a1654331a4d5/app/connectors/postgres.py#L672) — drop_abandoned_staging
- [docs/providers/postgres.md:48](https://github.com/eddiethedean/user-token-management-app/blob/1865af308509d2b50cf52c3eda64a1654331a4d5/docs/providers/postgres.md#L48) — manual cleanup contract

## Steps to Reproduce

In a disposable PostgreSQL database create `dmxstageycustomer_data(id integer)` and insert 42. Call `drop_abandoned_staging(credentials)` with credentials that own the table. Query `to_regclass('public.dmxstageycustomer_data')`.

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


def test_staging_cleanup_deletes_nonstaging_table(postgres_credentials, monkeypatch):
    with connect(postgres_credentials, connector_settings()) as conn:
        conn.execute('CREATE TABLE dmxstageycustomer_data (id integer)')
        conn.execute('INSERT INTO dmxstageycustomer_data VALUES (42)')
    monkeypatch.setattr('app.connectors.postgres.get_settings', connector_settings)
    assert drop_abandoned_staging(postgres_credentials) == 1
    with connect(postgres_credentials, connector_settings()) as conn:
        result = conn.execute("SELECT to_regclass('public.dmxstageycustomer_data')").fetchone()
    print('nonstaging customer table after cleanup:', result)
    assert result == (None,)
```

</details>

## Expected Behavior

A table that does not start with the literal `dm_stage_` prefix is preserved.

## Actual Behavior

The helper returns 1 and the table no longer exists; to_regclass returns NULL. The committed DROP removes its row as well.

## Root Cause Analysis

The query uses `LIKE 'dm_stage_%'`. SQL LIKE treats each underscore as a single-character wildcard, so `dmxstageycustomer_data` matches. Every matching table is passed directly to DROP TABLE and committed. The scan spans all visible schemas.

## Impact

User/data: unrelated tables and their contents can be removed. Production: running the documented manual helper with broad database privileges can cause an outage or require restore. The current periodic app janitor does not call this helper, so this is a manual-maintenance trigger, not automatic deletion during normal runs.

## Suggested Fix

Use a literal prefix check or escape underscores with an explicit ESCAPE clause, and validate the full generated staging-name format. Scope candidates by schema and recorded ownership before dropping anything.

## Test Coverage Gaps

The existing cleanup test creates only `dm_stage_orphan` and verifies removal. Add lookalike non-prefix names, unrelated schemas, and keep-list coverage; assert unrelated rows survive.

Baseline: **481 passed, 31 deselected**. The separate audit suite has **15 passing characterization checks**. Environment: Python 3.12.13, PostgreSQL 16.13, Polars 1.44.2, psycopg 3.3.5.

## Severity

High

## Confidence

High — executable reproduction observed locally. Where remote responses or source data are simulated, the boundary is identified above.

## Existing Issue Matches

All targeted issue and open-PR searches were empty. Reviewed provider documentation explicitly limits this helper to legacy dm_stage_* tables; deleting lookalike tables is not a documented limitation.

Reviewed all open issues (0 at audit start), all 20 closed issues (including the 16 recent bug reports), and all open PRs (0). Searched all-state issues and open PRs separately with each of:

- `"drop_abandoned_staging"`
- `"dm_stage_" "delete"`
- `"cleanup" "unrelated"`

Read the current README, contributor guidance, user guide, provider protocol notes, runtime runbook, and relevant security/limitation sections. No expected limitation explaining this behavior was found.

