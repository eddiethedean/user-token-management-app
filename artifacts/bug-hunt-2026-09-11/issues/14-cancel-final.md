## Description

A cancellation requested during the last destination write_batch is not checked again before finalize commits/publishes the transfer.

Verified on commit `7d427cbf9c0ce8203b5664d3736a1d88c210ede0` (local checkout matches GitHub `main`).

## Labels

Recommended: `bug`, `high-priority`. Applied existing repository label: `bug`.

## Affected Components

- [app/services/transfer_engine.py:223](https://github.com/eddiethedean/user-token-management-app/blob/7d427cbf9c0ce8203b5664d3736a1d88c210ede0/app/services/transfer_engine.py#L223)
- [app/services/transfer_engine.py:293](https://github.com/eddiethedean/user-token-management-app/blob/7d427cbf9c0ce8203b5664d3736a1d88c210ede0/app/services/transfer_engine.py#L293)

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

def engine_setup(monkeypatch,source,dest):
    from app.services import transfer_engine as te
    monkeypatch.setattr(te,'connector_for',lambda p: source if p=='mss' else dest)
    monkeypatch.setattr(te,'route_allowed',lambda *a:True)
    monkeypatch.setattr(te,'writer_enabled',lambda *a:True)
    for name in ['heartbeat','transition','add_counters','append_event','complete_run','cancel_claimed_run']:
        monkeypatch.setattr(te.pipeline_runs,name,Mock())
    snapshot=DefinitionSnapshot(name='Audit',source_provider='mss',destination_provider='postgres',source=FoundryDatasetFilesLocator(dataset_rid='ri.foundry.main.dataset.audit',branch='master'),destination=postgres_table('public','events'),write_policy=PostgresAppendPolicy())
    settings=SimpleNamespace(is_demo_mode=False,app_env='test',pipeline_lease_seconds=120,pipeline_batch_rows=1000,pipeline_batch_target_bytes=1000000,pipeline_max_run_seconds=60,pipeline_max_source_bytes=1000000)
    return te,dict(db=Mock(),run=SimpleNamespace(id='audit'),lease_token='lease',snapshot=snapshot,source_credentials={},destination_credentials={},settings=settings)

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
    assert cancelled and dest.committed
    te.pipeline_runs.cancel_claimed_run.assert_not_called()
```

</details>

## Expected Behavior

When cancellation is already requested before publication starts, abort staged changes and mark the run cancelled.

## Actual Behavior

A controlled destination sets the cancellation flag during the only write_batch call. execute_transfer then calls finalize, marks the destination committed, and never calls cancel_claimed_run. This reproduces the scheduling boundary with fake source/destination objects.

## Root Cause Analysis

The loop checks cancel_requested before each write_batch, but after the last iteration the code transitions to verifying and checks only lease_lost before destination.finalize.

## Impact

A user’s acknowledged cancellation can still append or replace destination data even though the cancellation arrived while changes remained abortable.

## Suggested Fix

Recheck cancellation immediately before finalization and abort the destination session when set. Cover empty sources and the last-batch boundary as well as cancellation between batches.

## Test Coverage Gaps

Existing cancellation coverage exercises earlier checkpoints; it does not request cancellation inside the last write_batch.

The baseline suite passed with **336 passed, 31 deselected**. The targeted audit suite reproduced this defect; passing baseline tests did not exercise the failing boundary.

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
