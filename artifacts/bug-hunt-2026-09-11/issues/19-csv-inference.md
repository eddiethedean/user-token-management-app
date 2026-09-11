## Description

Full-file upload profiling accepts a mixed-value column as text, but the execution parser infers an integer type from only the first 10,000 rows and rejects later text.

Verified on commit `7d427cbf9c0ce8203b5664d3736a1d88c210ede0` (local checkout matches GitHub `main`).

## Labels

Recommended: `bug`. Applied existing repository label: `bug`.

## Affected Components

- [app/services/csv_uploads.py:89](https://github.com/eddiethedean/user-token-management-app/blob/7d427cbf9c0ce8203b5664d3736a1d88c210ede0/app/services/csv_uploads.py#L89)
- [app/services/csv_uploads.py:211](https://github.com/eddiethedean/user-token-management-app/blob/7d427cbf9c0ce8203b5664d3736a1d88c210ede0/app/services/csv_uploads.py#L211)
- [app/connectors/csv_source.py:104](https://github.com/eddiethedean/user-token-management-app/blob/7d427cbf9c0ce8203b5664d3736a1d88c210ede0/app/connectors/csv_source.py#L104)

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

def test_csv_late_text():
    payload=b'value\n'+b'1\n'*10000+b'other\n'
    assert inspect_csv('input.csv',payload).columns[0].inferred_type == 'text'
    loc=CsvUploadLocator(upload_id=str(uuid.uuid4()),checksum_sha256='a'*64)
    from app.connectors.errors import ConnectorError
    with pytest.raises(ConnectorError) as exc:
        CsvSourceConnector().inspect_object({'content':payload},loc)
    print('accepted CSV with late text:',str(exc.value))
```

</details>

## Expected Behavior

A valid sub-5-MB CSV already inspected as a text column transfers successfully with all values preserved.

## Actual Behavior

The CSV "value\n" + "1\n" repeated 10,000 times + "other\n" is accepted and profiled as text. CsvSourceConnector.inspect_object then raises ConnectorError("The CSV file could not be parsed.").

## Root Cause Analysis

inspect_csv scans every row and merges mixed types to text; _frame independently calls pl.read_csv(infer_schema_length=10_000) without the validated full-file type result.

## Impact

Valid uploads save successfully but their real transfers fail based on the position of the first nonnumeric value.

## Suggested Fix

Use the validated full-file schema or infer the complete bounded upload. Reconcile type policies between inspection and execution, including large integers and leading-zero identifiers.

## Test Coverage Gaps

Existing fixtures are small and put mixed values inside the inference window. Add 10,000/10,001-row boundary tests through upload and real connector parsing.

The baseline suite passed with **336 passed, 31 deselected**. The targeted audit suite reproduced this defect; passing baseline tests did not exercise the failing boundary.

## Severity

Medium

## Confidence

High — executable reproduction verified locally. Simulated components and shortened timing controls, where applicable, are identified above.

## Existing Issue Matches

No existing report or open fix found. Reviewed all open issues (0), all four closed issues (#4–#7, UI framework enhancements), and open pull requests (0). Also searched all-state issues and open PRs for each of:

- `CsvSourceConnector infer_schema_length`
- `CSV 10000`
- `CSV file could not be parsed`

All six targeted searches returned zero matches. None of the reviewed documentation declares this behavior an expected limitation.
