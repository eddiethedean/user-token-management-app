## Description

A valid empty source with a known schema produces no transfer batches. The Foundry destination saves only column names and never creates an empty typed Parquet file, so finalization fails before attempting any upload.

Verified on commit `7d427cbf9c0ce8203b5664d3736a1d88c210ede0` (local checkout matches GitHub `main`).

## Labels

Recommended: `bug`. Applied existing repository label: `bug`.

## Affected Components

- [app/connectors/foundry.py:669](https://github.com/eddiethedean/user-token-management-app/blob/7d427cbf9c0ce8203b5664d3736a1d88c210ede0/app/connectors/foundry.py#L669)
- [app/connectors/foundry.py:740](https://github.com/eddiethedean/user-token-management-app/blob/7d427cbf9c0ce8203b5664d3736a1d88c210ede0/app/connectors/foundry.py#L740)
- [app/services/transfer_engine.py:172](https://github.com/eddiethedean/user-token-management-app/blob/7d427cbf9c0ce8203b5664d3736a1d88c210ede0/app/services/transfer_engine.py#L172)

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

def test_empty_foundry_destination(tmp_path):
    from app.connectors.mss import MssConnector
    from app.connectors.locators import FoundryUploadLocator,FoundryReplaceFilePolicy
    from app.connectors.errors import ConnectorError
    from app.config import Settings
    c=MssConnector(Settings(_env_file=None,pipeline_spool_root=str(tmp_path)))
    loc=FoundryUploadLocator(dataset_rid='ri.foundry.main.dataset.audit',branch='master',file_name='empty.parquet')
    s=c.prepare_destination({},loc,ObjectSchema(locator=loc,columns=(ColumnSchema(name='id',data_type='Int64'),)),FoundryReplaceFilePolicy(),run_id=str(uuid.uuid4()))
    with pytest.raises(ConnectorError) as exc:
        c.finalize(s)
    print('empty typed source:',str(exc.value))
    assert str(exc.value)=='No Parquet spool was produced.'
```

</details>

## Expected Behavior

A zero-row source can produce a zero-row Parquet replacement with its schema, or an explicitly documented no-op policy is enforced before enqueue.

## Actual Behavior

prepare_destination with a known id:Int64 schema, followed by finalize without write_batch, raises PARTIAL_WRITE / "No Parquet spool was produced." No provider call is needed to reproduce this. The engine reaches this path for a header-only CSV or an empty PostgreSQL source.

## Root Cause Analysis

prepare_destination creates only the chunk directory. finalize constructs the final spool only when chunk files exist. No schema-only empty-output path is implemented.

## Impact

Pipelines fail when an otherwise valid source is temporarily empty. Replace operations leave stale destination contents rather than representing the empty source.

## Suggested Fix

Retain the full typed source schema and write a valid empty Parquet spool when zero rows were extracted; distinguish empty input from missing/inaccessible input.

## Test Coverage Gaps

Destination tests always call write_batch before finalize. Add empty PostgreSQL and header-only CSV end-to-end tests to both Foundry destinations.

The baseline suite passed with **336 passed, 31 deselected**. The targeted audit suite reproduced this defect; passing baseline tests did not exercise the failing boundary.

## Severity

Medium

## Confidence

High — executable reproduction verified locally. Simulated components and shortened timing controls, where applicable, are identified above.

## Existing Issue Matches

No existing report or open fix found. Reviewed all open issues (0), all four closed issues (#4–#7, UI framework enhancements), and open pull requests (0). Also searched all-state issues and open PRs for each of:

- `No Parquet spool was produced`
- `Foundry empty source`
- `finalize zero rows`

All six targeted searches returned zero matches. None of the reviewed documentation declares this behavior an expected limitation.
