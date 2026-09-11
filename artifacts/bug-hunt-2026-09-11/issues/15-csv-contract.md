## Description

Upload inspection recognizes comma, semicolon, tab, and pipe delimiters and trims header names, but the real CSV connector reparses the original bytes with comma-only defaults and untrimmed headers.

Verified on commit `7d427cbf9c0ce8203b5664d3736a1d88c210ede0` (local checkout matches GitHub `main`).

## Labels

Recommended: `bug`, `high-priority`. Applied existing repository label: `bug`.

## Affected Components

- [app/services/csv_uploads.py:60](https://github.com/eddiethedean/user-token-management-app/blob/7d427cbf9c0ce8203b5664d3736a1d88c210ede0/app/services/csv_uploads.py#L60)
- [app/services/csv_uploads.py:73](https://github.com/eddiethedean/user-token-management-app/blob/7d427cbf9c0ce8203b5664d3736a1d88c210ede0/app/services/csv_uploads.py#L73)
- [app/connectors/csv_source.py:89](https://github.com/eddiethedean/user-token-management-app/blob/7d427cbf9c0ce8203b5664d3736a1d88c210ede0/app/connectors/csv_source.py#L89)

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

@pytest.mark.parametrize('payload',[b'id;name\n1;Alice\n',b' id , name \n1,Alice\n'])
def test_csv_inspection_disagrees(payload):
    profile=inspect_csv('input.csv',payload)
    loc=CsvUploadLocator(upload_id=str(uuid.uuid4()),checksum_sha256='a'*64)
    actual=CsvSourceConnector().inspect_object({'content':payload},loc)
    print('upload columns:',[c.name for c in profile.columns],'transfer columns:',[c.name for c in actual.columns])
    assert [c.name for c in profile.columns] != [c.name for c in actual.columns]
```

</details>

## Expected Behavior

The schema and column names displayed during upload inspection match the data actually transferred, including the documented alternate delimiters.

## Actual Behavior

id;name\n1;Alice\n is accepted as two columns id/name but transferred as one column id;name. A comma CSV with headers " id , name " is displayed as id/name but transferred with surrounding spaces in the column names.

## Root Cause Analysis

inspect_csv discovers a dialect and normalizes headers, but those parsing settings are not persisted or passed to CsvSourceConnector._frame. That method calls pl.read_csv without separator or normalized column names.

## Impact

New destination tables can receive a silently different schema and combined values. Loads into existing tables fail because the actual column names differ from the inspected names.

## Suggested Fix

Share a single parsing contract between inspection and execution; persist or deterministically reuse the validated delimiter and normalized headers. Reject unsupported dialects during inspection if they cannot be transferred.

## Test Coverage Gaps

Upload-profile tests and connector tests independently pass because they do not round-trip semicolon/tab/pipe inputs or padded headers through both layers. docs/user-guide.md explicitly promises these delimiters.

The baseline suite passed with **336 passed, 31 deselected**. The targeted audit suite reproduced this defect; passing baseline tests did not exercise the failing boundary.

## Severity

High

## Confidence

High — executable reproduction verified locally. Simulated components and shortened timing controls, where applicable, are identified above.

## Existing Issue Matches

No existing report or open fix found. Reviewed all open issues (0), all four closed issues (#4–#7, UI framework enhancements), and open pull requests (0). Also searched all-state issues and open PRs for each of:

- `inspect_csv delimiter`
- `CsvSourceConnector semicolon`
- `CSV headers`

All six targeted searches returned zero matches. None of the reviewed documentation declares this behavior an expected limitation.
