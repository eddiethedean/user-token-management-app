## Description

A cancellation requested during the last destination write_batch is not checked again before finalize commits/publishes the transfer.

Verified on commit `7d427cbf9c0ce8203b5664d3736a1d88c210ede0` (local checkout matches GitHub `main`).

## Labels

Recommended: `bug`, `high-priority`. Applied existing repository label: `bug`.

## Affected Components

- [app/services/transfer_engine.py:223](https://github.com/eddiethedean/user-token-management-app/blob/7d427cbf9c0ce8203b5664d3736a1d88c210ede0/app/services/transfer_engine.py#L223)
- [app/services/transfer_engine.py:293](https://github.com/eddiethedean/user-token-management-app/blob/7d427cbf9c0ce8203b5664d3736a1d88c210ede0/app/services/transfer_engine.py#L293)

## Steps to Reproduce

1. Use the current checkout and install the project with its dev dependencies. PostgreSQL tests require local `initdb` and `postgres` binaries and use a disposable database through the repository fixture.
2. Save the following test as `/tmp/test_bug.py`.
3. From the repository root, run `.venv/bin/python -m pytest /tmp/test_bug.py -c pyproject.toml -s`.

The test is a regression check for the corrected behavior. No production database or live provider is used.

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

def engine_setup(monkeypatch,source,dest):
    from app.services import transfer_engine as te
    monkeypatch.setattr(te,'route_allowed',lambda *a:True)
    monkeypatch.setattr(te,'writer_enabled',lambda *a:True)
    for name in ['heartbeat','transition','add_counters','append_event','complete_run','cancel_claimed_run']:
        monkeypatch.setattr(te.pipeline_runs,name,Mock())
    snapshot=DefinitionSnapshot(name='Audit',source_provider='mss',destination_provider='postgres',source=FoundryDatasetFilesLocator(dataset_rid='ri.foundry.main.dataset.audit',branch='master'),destination=postgres_table('public','events'),write_policy=PostgresAppendPolicy())
    settings=SimpleNamespace(is_demo_mode=False,app_env='test',pipeline_lease_seconds=120,pipeline_batch_rows=1000,pipeline_batch_target_bytes=1000000,pipeline_max_run_seconds=60,pipeline_max_source_bytes=1000000)
    return te,dict(db=Mock(),run=SimpleNamespace(id='audit'),lease_token='lease',snapshot=snapshot,source_credentials={},destination_credentials={},settings=settings,source_resolver=lambda p: source,destination_resolver=lambda p: dest)

def test_cancel_during_last_batch(monkeypatch):
    from tests.test_transfer_engine import _Source,_Destination
    source=_Source();dest=_Destination();cancelled=False
    original=dest.write_batch
    def write(s,b):
        nonlocal cancelled
        cancelled=True
        return original(s,b)
    dest.write_batch=write
    te,kwargs=engine_setup(monkeypatch,source,dest)
    te.execute_transfer(**kwargs,cancel_requested=lambda:cancelled)
    print('cancel requested during final batch; committed:',dest.committed)
    assert cancelled and not dest.committed
    assert dest.aborted
    te.pipeline_runs.cancel_claimed_run.assert_called_once()
```

</details>

## Expected Behavior

When cancellation is requested during the final write, abort staged changes and mark the run cancelled before publication.

## Actual Behavior

A controlled destination sets the cancellation flag during the only write_batch call. execute_transfer rechecks the flag, aborts the destination session, and calls cancel_claimed_run before publication. This verifies the scheduling boundary with fake source/destination objects.

## Root Cause Analysis

The loop now checks cancel_requested again after the last batch and before destination.finalize.

## Impact

A user’s acknowledged cancellation is honored while changes remain abortable, preventing publication of the final staged batch.

## Suggested Fix

Completed: recheck cancellation immediately before finalization and abort the destination session when set. Coverage includes empty sources and the last-batch boundary.

## Test Coverage Gaps

Existing cancellation coverage now exercises cancellation inside the last write_batch.

The historical baseline suite passed with **336 passed, 31 deselected** while missing this boundary. The current checkout includes the corrected last-batch regression check.

## Severity

High

## Confidence

High — executable reproduction verified locally. Simulated components and shortened timing controls, where applicable, are identified above.

## Existing Issue Matches

No existing report or open fix found. Reviewed all open issues (0), all four closed issues (#4–#7, UI framework enhancements), and open pull requests (0). Also searched all-state issues and open PRs for each of:

- `execute_transfer cancellation`
- `cancel_requested finalize`
- `last batch cancel`

All six targeted searches returned zero matches. None of the reviewed documentation declares this behavior an expected limitation.
