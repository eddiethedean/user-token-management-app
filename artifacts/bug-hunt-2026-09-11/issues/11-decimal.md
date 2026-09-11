## Description

Creating a PostgreSQL destination from a Polars Decimal source maps the decimal type to DOUBLE PRECISION, losing exact values while the load reports success.

Verified on commit `7d427cbf9c0ce8203b5664d3736a1d88c210ede0` (local checkout matches GitHub `main`).

## Labels

Recommended: `bug`, `high-priority`. Applied existing repository label: `bug`.

## Affected Components

- [app/connectors/postgres.py:556](https://github.com/eddiethedean/user-token-management-app/blob/7d427cbf9c0ce8203b5664d3736a1d88c210ede0/app/connectors/postgres.py#L556)
- [app/connectors/postgres.py:329](https://github.com/eddiethedean/user-token-management-app/blob/7d427cbf9c0ce8203b5664d3736a1d88c210ede0/app/connectors/postgres.py#L329)
- [app/services/transfer_engine.py:183](https://github.com/eddiethedean/user-token-management-app/blob/7d427cbf9c0ce8203b5664d3736a1d88c210ede0/app/services/transfer_engine.py#L183)

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

def test_decimal_precision(postgres_credentials):
    value=Decimal('12345678901234567890.123456789')
    load_frame(postgres_credentials,'precise',pl.DataFrame({'v':[value]}))
    rows=query(postgres_credentials,'SELECT v::text,pg_typeof(v)::text FROM precise')
    print('decimal source:',value,'destination:',rows)
    assert rows[0][1] == 'double precision'
    assert Decimal(rows[0][0]) != value
```

</details>

## Expected Behavior

Preserve decimal precision and scale with PostgreSQL NUMERIC, or reject an unsupported conversion explicitly.

## Actual Behavior

Decimal("12345678901234567890.123456789") loads successfully but reads back as 1.2345678901234567e+19 with pg_typeof(v) = double precision.

## Root Cause Analysis

_pg_type falls through to a substring branch that maps every type containing decimal to DOUBLE PRECISION. prepare_destination uses that mapping to build the destination/staging schema.

## Impact

Exact identifiers, monetary quantities, and other high-precision data can be permanently rounded in new/recreated tables. Row-count verification does not detect it.

## Suggested Fix

Carry structured precision/scale metadata into the type mapper and emit NUMERIC(precision, scale), using unbounded NUMERIC if necessary. Add exact value round trips.

## Test Coverage Gaps

Mixed-type tests cover ordinary floats and integers but not Decimal values beyond binary64 precision.

The baseline suite passed with **336 passed, 31 deselected**. The targeted audit suite reproduced this defect; passing baseline tests did not exercise the failing boundary.

## Severity

High

## Confidence

High — executable reproduction verified locally. Simulated components and shortened timing controls, where applicable, are identified above.

## Existing Issue Matches

No existing report or open fix found. Reviewed all open issues (0), all four closed issues (#4–#7, UI framework enhancements), and open pull requests (0). Also searched all-state issues and open PRs for each of:

- `_pg_type decimal`
- `DOUBLE PRECISION precision`
- `PostgresConnector numeric`

All six targeted searches returned zero matches. None of the reviewed documentation declares this behavior an expected limitation.
