## Description

PostgreSQL upsert with action=ignore cannot ignore duplicate keys within the incoming batch because its staging table already has the destination unique constraints.

Verified on commit `7d427cbf9c0ce8203b5664d3736a1d88c210ede0` (local checkout matches GitHub `main`).

## Labels

Recommended: `bug`. Applied existing repository label: `bug`.

## Affected Components

- [app/connectors/postgres.py:375](https://github.com/eddiethedean/user-token-management-app/blob/7d427cbf9c0ce8203b5664d3736a1d88c210ede0/app/connectors/postgres.py#L375)
- [app/connectors/postgres.py:447](https://github.com/eddiethedean/user-token-management-app/blob/7d427cbf9c0ce8203b5664d3736a1d88c210ede0/app/connectors/postgres.py#L447)

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

def test_upsert_ignore_duplicate_input(postgres_credentials):
    import psycopg
    with connect(postgres_credentials,connector_settings()) as c:
        c.execute('CREATE TABLE dupes(id int PRIMARY KEY, value text)')
    with pytest.raises(psycopg.errors.UniqueViolation) as exc:
        load_frame(postgres_credentials,'dupes',pl.DataFrame({'id':[1,1],'value':['a','a']}),PostgresUpsertPolicy(conflict_columns=['id'],action='ignore'))
    print('upsert ignore duplicate input:',str(exc.value).splitlines()[0])
    assert query(postgres_credentials,'SELECT count(*) FROM dupes') == [(0,)]
```

</details>

## Expected Behavior

An ignore-conflicts load of two rows sharing the selected key inserts one row and ignores the duplicate, following ON CONFLICT DO NOTHING semantics.

## Actual Behavior

For dupes(id int primary key, value text), a frame containing (1,"a"),(1,"a") with PostgresUpsertPolicy(conflict_columns=["id"], action="ignore") raises UniqueViolation on the dm_stage_* primary key during COPY. The destination remains empty.

## Root Cause Analysis

prepare_destination uses CREATE TABLE staging (LIKE destination INCLUDING ALL), which copies unique/primary indexes. write_batch violates those constraints before finalize can execute ON CONFLICT DO NOTHING.

## Impact

Valid conflict-ignore pipelines fail on duplicate input keys, including duplicates across batches, despite a write mode intended to ignore those conflicts.

## Suggested Fix

Use staging without destination uniqueness constraints and apply conflict behavior at final insertion. Define deterministic behavior for action=update when incoming keys repeat.

## Test Coverage Gaps

Existing upsert tests cover conflicts with rows already in the destination, not duplicate keys within the incoming source.

The baseline suite passed with **336 passed, 31 deselected**. The targeted audit suite reproduced this defect; passing baseline tests did not exercise the failing boundary.

## Severity

Medium

## Confidence

High — executable reproduction verified locally. Simulated components and shortened timing controls, where applicable, are identified above.

## Existing Issue Matches

No existing report or open fix found. Reviewed all open issues (0), all four closed issues (#4–#7, UI framework enhancements), and open pull requests (0). Also searched all-state issues and open PRs for each of:

- `prepare_destination INCLUDING ALL`
- `upsert duplicate source`
- `UniqueViolation staging`

All six targeted searches returned zero matches. None of the reviewed documentation declares this behavior an expected limitation.
