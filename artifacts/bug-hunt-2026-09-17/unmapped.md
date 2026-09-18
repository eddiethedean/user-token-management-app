## Verified follow-up: PostgreSQL source schema mapping rejects valid non-text values

Existing Issue Found: #17 — [BUG] PostgreSQL extraction fails when the first 100 values are NULL. Match confidence: Medium (same source frame-construction/type-stability area and an incomplete follow-up to its recommended schema mapping; the original sparse-integer reproduction is not claimed to remain broken). Added evidence to the existing report conservatively instead of creating a competing source-type report. No open PR matches.

## Description

The explicit PostgreSQL-to-Polars schema introduced to avoid inference failures still rejects valid source values. JSONB, UUID, integer arrays and interval values are all assigned pl.String without converting their psycopg Python objects; high-precision NUMERIC also fails.

Verified on commit `1865af308509d2b50cf52c3eda64a1654331a4d5`.

## Labels

bug. Recommendations for this follow-up.

## Affected Components

- [app/connectors/postgres.py:316](https://github.com/eddiethedean/user-token-management-app/blob/1865af308509d2b50cf52c3eda64a1654331a4d5/app/connectors/postgres.py#L316) — PostgresConnector.extract
- [app/connectors/postgres.py:639](https://github.com/eddiethedean/user-token-management-app/blob/1865af308509d2b50cf52c3eda64a1654331a4d5/app/connectors/postgres.py#L639) — _polars_type

## Steps to Reproduce

Create separate one-column tables containing JSONB {"a":1}, a UUID, ARRAY[1,2], and INTERVAL '1 day', and call extract. Separately extract numeric(40,39) containing 0.123456789012345678901234567890123456789.

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


@pytest.mark.parametrize('sql_type,sql_value', [('jsonb', "'{\"a\":1}'"), ('uuid', "'c155b362-4647-47ba-993c-3ba3ac6b8ce0'"), ('integer[]', 'ARRAY[1,2]'), ('interval', "'1 day'")])
def test_postgres_unmapped_types(postgres_credentials, sql_type, sql_value):
    with connect(postgres_credentials, connector_settings()) as conn:
        conn.execute(f'CREATE TABLE special_source (value {sql_type})')
        conn.execute(f'INSERT INTO special_source VALUES ({sql_value})')
    connector = PostgresConnector(connector_settings())
    with pytest.raises(pl.exceptions.ComputeError) as exc:
        list(connector.extract(postgres_credentials, postgres_table('public', 'special_source'), batch_rows=10, batch_bytes=10000))
    print('type conversion exception:', sql_type, type(exc.value).__name__, str(exc.value).splitlines()[0])


def test_large_numeric_precision(postgres_credentials):
    from decimal import Decimal
    literal = '0.123456789012345678901234567890123456789'
    with connect(postgres_credentials, connector_settings()) as conn:
        conn.execute('CREATE TABLE precise_source (value numeric(40,39))')
        conn.execute('INSERT INTO precise_source VALUES (%s)', (Decimal(literal),))
    connector = PostgresConnector(connector_settings())
    with pytest.raises(RuntimeError, match='Decimal is too large to fit in Decimal128') as exc:
        next(connector.extract(postgres_credentials, postgres_table('public', 'precise_source'), batch_rows=10, batch_bytes=10000))
    print('valid numeric(40,39) fails:', str(exc.value))
```

</details>

## Expected Behavior

Use a defined lossless conversion for supported PostgreSQL types; unsupported types/ranges should be identified clearly during inspection or preflight, rather than failing an apparently valid route inside frame construction.

## Actual Behavior

All four object-valued types raise Polars ComputeError in pl.DataFrame. The valid numeric(40,39) value raises RuntimeError: Decimal is too large to fit in Decimal128. None produces a transfer batch.

## Root Cause Analysis

`_polars_type()` defaults unknown SQL types to pl.String but extract passes dict/UUID/list/timedelta objects unchanged. Numeric precision and scale are clamped to 38 without validating that the source values can be represented.

## Impact

User: valid source tables fail to transfer based on their column types. Data: no destination writes are needed to reproduce; no silent numeric truncation is claimed. Production: common business tables with UUID/JSON/array/interval columns cannot execute.

## Suggested Fix

Define explicit adaptations and compatible destination mappings for these types; preserve exact values, or fail with UNSUPPORTED_TYPE during preflight. Handle high-precision numeric explicitly instead of clamping its declared precision.

## Test Coverage Gaps

Expand the existing #17 source-schema regression coverage beyond sparse integer columns to psycopg object-valued types, nullable variants and precision/range boundaries.

Baseline: **481 passed, 31 deselected**. The separate audit suite has **15 passing characterization checks**. Environment: Python 3.12.13, PostgreSQL 16.13, Polars 1.44.2, psycopg 3.3.5.

## Severity

Medium

## Confidence

High — executable reproduction observed locally. Where remote responses or source data are simulated, the boundary is identified above.

## Existing Issue Matches

Existing Issue Found: #17 — [BUG] PostgreSQL extraction fails when the first 100 values are NULL. Match confidence: Medium (same source frame-construction/type-stability area and an incomplete follow-up to its recommended schema mapping; the original sparse-integer reproduction is not claimed to remain broken). Added evidence to the existing report conservatively instead of creating a competing source-type report. No open PR matches.

Reviewed all open issues (0 at audit start), all 20 closed issues (including the 16 recent bug reports), and all open PRs (0). Searched all-state issues and open PRs separately with each of:

- `"_polars_type"`
- `"could not append value"`
- `"jsonb" "extract"`

Read the current README, contributor guidance, user guide, provider protocol notes, runtime runbook, and relevant security/limitation sections. No expected limitation explaining this behavior was found.

