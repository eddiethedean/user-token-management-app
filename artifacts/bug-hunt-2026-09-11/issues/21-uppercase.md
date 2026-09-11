## Description

The app accepts and creates mixed-case table names using sql.Identifier, but its primary-key metadata query later casts an unquoted schema.table string to regclass. PostgreSQL folds that string to lowercase.

Verified on commit `7d427cbf9c0ce8203b5664d3736a1d88c210ede0` (local checkout matches GitHub `main`).

## Labels

Recommended: `bug`. Applied existing repository label: `bug`.

## Affected Components

- [app/connectors/postgres.py:223](https://github.com/eddiethedean/user-token-management-app/blob/7d427cbf9c0ce8203b5664d3736a1d88c210ede0/app/connectors/postgres.py#L223)
- [app/connectors/postgres.py:329](https://github.com/eddiethedean/user-token-management-app/blob/7d427cbf9c0ce8203b5664d3736a1d88c210ede0/app/connectors/postgres.py#L329)
- [app/connectors/locators.py:31](https://github.com/eddiethedean/user-token-management-app/blob/7d427cbf9c0ce8203b5664d3736a1d88c210ede0/app/connectors/locators.py#L31)

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

def test_uppercase_postgres_identifier(postgres_credentials):
    import psycopg
    load_frame(postgres_credentials,'CamelTable',pl.DataFrame({'id':[1]}))
    loc=postgres_table('public','CamelTable')
    with pytest.raises(psycopg.errors.UndefinedTable) as exc:
        PostgresConnector(connector_settings()).inspect_object(postgres_credentials,loc)
    print('accepted CamelTable locator:',str(exc.value).splitlines()[0])
```

</details>

## Expected Behavior

A table created by the connector as CamelTable is inspectable and reusable with the same saved locator.

## Actual Behavior

load_frame creates and loads public."CamelTable" successfully. inspect_object(postgres_table("public", "CamelTable")) then raises UndefinedTable: relation "public.cameltable" does not exist.

## Root Cause Analysis

inspect_object passes f"{locator.schema_name}.{locator.table}" to %s::regclass. Unlike sql.Identifier used for creation/loading, the text does not preserve case with SQL identifier quoting.

## Impact

A new table name accepted by the UI creates a destination that cannot be inspected reliably afterward; source extraction and upsert/schema previews fail for that saved name.

## Suggested Fix

Resolve the relation by schema and table columns in pg_class/pg_namespace, or construct a properly quoted regclass identifier. Keep creation and lookup semantics consistent.

## Test Coverage Gaps

Identifier tests cover injection and odd punctuation but omit uppercase names created through the connector itself.

The baseline suite passed with **336 passed, 31 deselected**. The targeted audit suite reproduced this defect; passing baseline tests did not exercise the failing boundary.

## Severity

Medium

## Confidence

High — executable reproduction verified locally. Simulated components and shortened timing controls, where applicable, are identified above.

## Existing Issue Matches

No existing report or open fix found. Reviewed all open issues (0), all four closed issues (#4–#7, UI framework enhancements), and open pull requests (0). Also searched all-state issues and open PRs for each of:

- `inspect_object regclass`
- `CamelTable`
- `PostgreSQL uppercase`

All six targeted searches returned zero matches. None of the reviewed documentation declares this behavior an expected limitation.
