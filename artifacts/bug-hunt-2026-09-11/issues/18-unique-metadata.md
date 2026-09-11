## Description

The information_schema join for unique constraints is keyed only by constraint catalog/schema/name. PostgreSQL permits a foreign key on another table to share the name of a unique constraint, and its column is included in the inspected unique key.

Verified on commit `7d427cbf9c0ce8203b5664d3736a1d88c210ede0` (local checkout matches GitHub `main`).

## Labels

Recommended: `bug`. Applied existing repository label: `bug`.

## Affected Components

- [app/connectors/postgres.py:232](https://github.com/eddiethedean/user-token-management-app/blob/7d427cbf9c0ce8203b5664d3736a1d88c210ede0/app/connectors/postgres.py#L232)
- [app/services/transfer_engine.py:80](https://github.com/eddiethedean/user-token-management-app/blob/7d427cbf9c0ce8203b5664d3736a1d88c210ede0/app/services/transfer_engine.py#L80)
- [app/ui/routes/pipeline.py:221](https://github.com/eddiethedean/user-token-management-app/blob/7d427cbf9c0ce8203b5664d3736a1d88c210ede0/app/ui/routes/pipeline.py#L221)

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

def test_unique_constraints_cross_table(postgres_credentials):
    with connect(postgres_credentials,connector_settings()) as c:
        c.execute('CREATE TABLE a(id int, code text, CONSTRAINT shared_name UNIQUE(id))')
        c.execute('CREATE TABLE b(code int, CONSTRAINT shared_name FOREIGN KEY(code) REFERENCES a(id))')
    s=PostgresConnector(connector_settings()).inspect_object(postgres_credentials,postgres_table('public','a'))
    print('actual unique(id), reported:',s.unique_constraints)
    assert set(s.unique_constraints[0])=={'id','code'}
```

</details>

## Expected Behavior

Table a’s UNIQUE(id) is reported as exactly (id), regardless of unrelated constraints on other tables.

## Actual Behavior

Create a(id int, code text, CONSTRAINT shared_name UNIQUE(id)) and b(code int, CONSTRAINT shared_name FOREIGN KEY(code) REFERENCES a(id)). Inspecting a reports (("id", "code"),) instead of (("id",),). Both DDL statements execute successfully.

## Root Cause Analysis

The join between table_constraints tc and key_column_usage kcu omits kcu.table_catalog/table_schema/table_name equality with tc. Filtering tc to table a does not filter the joined kcu rows to table a.

## Impact

The UI offers a nonexistent composite conflict key, and execution rejects or fails upserts against otherwise valid destination constraints.

## Suggested Fix

Include the constrained table identity in the join or read PostgreSQL catalog constraint OIDs and ordered columns directly.

## Test Coverage Gaps

Existing catalog tests use globally distinct constraint names. Add same-named unique and foreign-key constraints on separate tables.

The baseline suite passed with **336 passed, 31 deselected**. The targeted audit suite reproduced this defect; passing baseline tests did not exercise the failing boundary.

## Severity

Medium

## Confidence

High — executable reproduction verified locally. Simulated components and shortened timing controls, where applicable, are identified above.

## Existing Issue Matches

No existing report or open fix found. Reviewed all open issues (0), all four closed issues (#4–#7, UI framework enhancements), and open pull requests (0). Also searched all-state issues and open PRs for each of:

- `inspect_object constraint_name`
- `unique_constraints wrong columns`
- `key_column_usage`

All six targeted searches returned zero matches. None of the reviewed documentation declares this behavior an expected limitation.
