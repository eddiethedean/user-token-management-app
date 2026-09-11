## Description

Binary source columns map to BYTEA, but CSV serialization passes Python bytes directly to csv.writer, which stringifies their Python representation.

Verified on commit `7d427cbf9c0ce8203b5664d3736a1d88c210ede0` (local checkout matches GitHub `main`).

## Labels

Recommended: `bug`, `high-priority`. Applied existing repository label: `bug`.

## Affected Components

- [app/connectors/postgres.py:49](https://github.com/eddiethedean/user-token-management-app/blob/7d427cbf9c0ce8203b5664d3736a1d88c210ede0/app/connectors/postgres.py#L49)
- [app/connectors/postgres.py:409](https://github.com/eddiethedean/user-token-management-app/blob/7d427cbf9c0ce8203b5664d3736a1d88c210ede0/app/connectors/postgres.py#L409)

## Steps to Reproduce

1. Check out the commit above and install the project with its dev dependencies. PostgreSQL tests require local `initdb` and `postgres` binaries and use a disposable database through the repository fixture.
2. Save the following test as `/tmp/test_bug.py`.
3. From the repository root, run `.venv/bin/python -m pytest /tmp/test_bug.py -c pyproject.toml -s`.

The test is a **characterization of the defect**: its assertions pass while the bug is present. No production database or live provider is used. Convert the assertions to the expected behavior when adding regression coverage.

<details>
<summary>Executable reproduction</summary>

```python
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import Mock
import uuid
import polars as pl
import pytest
from app.connectors.base import ColumnSchema,ObjectSchema,TransferBatch
from app.connectors.locators import CsvUploadLocator,postgres_table,PostgresAppendPolicy,PostgresUpsertPolicy,DefinitionSnapshot,FoundryDatasetFilesLocator
from app.connectors.csv_source import CsvSourceConnector
from app.connectors.postgres import PostgresConnector,connect
from app.services.csv_uploads import inspect_csv
from tests.postgres_support import connector_settings
pytest_plugins = ['tests.conftest']

def load_frame(creds, table, frame, policy=None):
    c=PostgresConnector(connector_settings()); loc=postgres_table('public',table)
    schema=ObjectSchema(locator=loc,columns=tuple(ColumnSchema(name=n,data_type=str(t)) for n,t in frame.schema.items()))
    s=c.prepare_destination(creds,loc,schema,policy or PostgresAppendPolicy(),run_id=str(uuid.uuid4()))
    try:
        c.write_batch(s,TransferBatch(frame=frame,row_count=frame.height,byte_count=frame.estimated_size(),sequence=1))
        return c.finalize(s)
    except Exception:
        c.abort(s); raise

def query(creds,sql):
    with connect(creds,connector_settings()) as c:
        return c.execute(sql).fetchall()

def test_binary_corruption(postgres_credentials):
    load_frame(postgres_credentials,'binary_data',pl.DataFrame({'v':[b'abc']}))
    rows=query(postgres_credentials,'SELECT v FROM binary_data')
    print('binary source:',b'abc','destination:',rows)
    assert rows[0][0] == b"b'abc'"
```

</details>

## Expected Behavior

A binary value b"abc" round-trips as the same three bytes.

## Actual Behavior

Loading b"abc" succeeds and returns b"b'abc'": six bytes including the Python literal prefix and quotes.

## Root Cause Analysis

write_batch uses generic csv.writer string conversion for frame.iter_rows() values without BYTEA-specific encoding or psycopg adaptation. _POLARS_TO_PG advertises a Binary -> BYTEA mapping, so the path is accepted.

## Impact

Binary payloads are silently changed. Other byte sequences can also fail parsing. Downstream hashes, parsers, and identifiers no longer match the original data.

## Suggested Fix

Use a COPY path with psycopg byte adaptation, or explicitly encode bytea as PostgreSQL hex input and apply correct CSV escaping.

## Test Coverage Gaps

The connector’s mixed-type round-trip fixture excludes Binary columns. Add empty bytes, ASCII bytes, NUL, backslash, and arbitrary octet cases.

The baseline suite passed with **336 passed, 31 deselected**. The targeted audit suite reproduced this defect; passing baseline tests did not exercise the failing boundary.

## Severity

High

## Confidence

High — executable reproduction verified locally. Simulated components and shortened timing controls, where applicable, are identified above.

## Existing Issue Matches

No existing report or open fix found. Reviewed all open issues (0), all four closed issues (#4–#7, UI framework enhancements), and open pull requests (0). Also searched all-state issues and open PRs for each of:

- `write_batch Binary`
- `BYTEA corruption`
- `PostgresConnector bytes`

All six targeted searches returned zero matches. None of the reviewed documentation declares this behavior an expected limitation.
