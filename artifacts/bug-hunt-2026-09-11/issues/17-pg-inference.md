## Description

PostgreSQL extraction infers Polars types from only the initial rows of each fetched batch instead of using the database schema. A nullable integer column can therefore become an inferred Null column.

Verified on commit `7d427cbf9c0ce8203b5664d3736a1d88c210ede0` (local checkout matches GitHub `main`).

## Labels

Recommended: `bug`. Applied existing repository label: `bug`.

## Affected Components

- [app/connectors/postgres.py:293](https://github.com/eddiethedean/user-token-management-app/blob/7d427cbf9c0ce8203b5664d3736a1d88c210ede0/app/connectors/postgres.py#L293)
- [app/connectors/postgres.py:317](https://github.com/eddiethedean/user-token-management-app/blob/7d427cbf9c0ce8203b5664d3736a1d88c210ede0/app/connectors/postgres.py#L317)

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

def test_postgres_late_nonnull(postgres_credentials):
    with connect(postgres_credentials,connector_settings()) as c:
        c.execute('CREATE TABLE late(v int)')
        c.execute('INSERT INTO late SELECT NULL FROM generate_series(1,100)')
        c.execute('INSERT INTO late VALUES(42)')
    with pytest.raises(pl.exceptions.ComputeError) as exc:
        list(PostgresConnector(connector_settings()).extract(postgres_credentials,postgres_table('public','late'),batch_rows=1000,batch_bytes=100000))
    print('100 nulls followed by 42:',str(exc.value))
```

</details>

## Expected Behavior

A valid SQL integer column containing 100 NULLs followed by 42 extracts all 101 rows successfully.

## Actual Behavior

With batch_rows=1000, pl.DataFrame raises ComputeError: could not append value: 42 of type: i64 to the builder. All rows are valid values for the same SQL column.

## Root Cause Analysis

extract obtains column metadata but passes only the names to pl.DataFrame(rows, schema=names, orient="row"). The default inference sample is insufficient and is repeated for every batch.

## Impact

Transfers from sparse nullable columns fail based on row ordering and batch boundaries. Users cannot repair these failures through a valid route configuration.

## Suggested Fix

Build an explicit Polars schema from PostgreSQL type metadata, preserving nullability and exact types across all batches. Validate unsupported types rather than inferring from a small sample.

## Test Coverage Gaps

The mixed-null test uses small frames with early non-null values. Add long null prefixes, fully null batches followed by populated batches, and boundary values.

The baseline suite passed with **336 passed, 31 deselected**. The targeted audit suite reproduced this defect; passing baseline tests did not exercise the failing boundary.

## Severity

Medium

## Confidence

High — executable reproduction verified locally. Simulated components and shortened timing controls, where applicable, are identified above.

## Existing Issue Matches

No existing report or open fix found. Reviewed all open issues (0), all four closed issues (#4–#7, UI framework enhancements), and open pull requests (0). Also searched all-state issues and open PRs for each of:

- `PostgresConnector infer_schema_length`
- `could not append value`
- `extract NULL`

All six targeted searches returned zero matches. None of the reviewed documentation declares this behavior an expected limitation.
