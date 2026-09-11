## Description

The PostgreSQL CSV serializer produces the same unquoted representation for a literal text value \N and Python None.

Verified on commit `7d427cbf9c0ce8203b5664d3736a1d88c210ede0` (local checkout matches GitHub `main`).

## Labels

Recommended: `bug`, `high-priority`. Applied existing repository label: `bug`.

## Affected Components

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

def test_literal_null_marker(postgres_credentials):
    load_frame(postgres_credentials,'marker',pl.DataFrame({'v':[r'\N',None,'']}))
    rows=query(postgres_credentials,'SELECT v FROM marker')
    print('NULL marker roundtrip:', rows)
    assert rows == [(None,),(None,),('',)] # confirmed corruption
```

</details>

## Expected Behavior

Literal backslash-N text, SQL NULL, and empty strings remain three distinct values.

## Actual Behavior

Loading [r"\N", None, ""] into a text column succeeds but returns [(None,), (None,), ("",)].

## Root Cause Analysis

write_batch replaces None with r"\N" and uses csv.QUOTE_MINIMAL. A real text value r"\N" is not quoted because it contains no CSV special character, so COPY interprets it as the configured NULL marker.

## Impact

Text fields are silently erased; a nonnullable destination can instead fail the load. Successful row counts hide the corruption.

## Suggested Fix

Use psycopg row adaptation/COPY write_row, or quote literal values matching the NULL marker while leaving only actual nulls unquoted. Preserve empty strings and embedded CSV syntax.

## Test Coverage Gaps

Existing tests distinguish empty string from null but omit text equal to the chosen null sentinel.

The baseline suite passed with **336 passed, 31 deselected**. The targeted audit suite reproduced this defect; passing baseline tests did not exercise the failing boundary.

## Severity

High

## Confidence

High — executable reproduction verified locally. Simulated components and shortened timing controls, where applicable, are identified above.

## Existing Issue Matches

No existing report or open fix found. Reviewed all open issues (0), all four closed issues (#4–#7, UI framework enhancements), and open pull requests (0). Also searched all-state issues and open PRs for each of:

- `write_batch null marker`
- `COPY backslash NULL`
- `literal null`

All six targeted searches returned zero matches. None of the reviewed documentation declares this behavior an expected limitation.
