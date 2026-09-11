## Description

The engine allocates an extracted-batch event in the application database, then performs destination I/O before committing that event. Event allocation updates the same pipeline_runs row that the independent heartbeat must update.

Verified on commit `7d427cbf9c0ce8203b5664d3736a1d88c210ede0` (local checkout matches GitHub `main`).

## Labels

Recommended: `bug`, `high-priority`. Applied existing repository label: `bug`.

## Affected Components

- [app/services/transfer_engine.py:257](https://github.com/eddiethedean/user-token-management-app/blob/7d427cbf9c0ce8203b5664d3736a1d88c210ede0/app/services/transfer_engine.py#L257)
- [app/services/pipeline_runs.py:245](https://github.com/eddiethedean/user-token-management-app/blob/7d427cbf9c0ce8203b5664d3736a1d88c210ede0/app/services/pipeline_runs.py#L245)
- [app/services/pipeline_runs.py:359](https://github.com/eddiethedean/user-token-management-app/blob/7d427cbf9c0ce8203b5664d3736a1d88c210ede0/app/services/pipeline_runs.py#L359)
- [app/worker.py:45](https://github.com/eddiethedean/user-token-management-app/blob/7d427cbf9c0ce8203b5664d3736a1d88c210ede0/app/worker.py#L45)

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

def test_event_write_blocks_heartbeat(access_app):
    from sqlalchemy import select,text
    from sqlalchemy.exc import OperationalError
    from app.database import SessionLocal
    from app.models import User,PipelineRun
    from app.services.pipeline_runs import claim_run,append_event,renew_lease
    with SessionLocal() as db:
        user=db.scalar(select(User))
        run=PipelineRun(user_id=user.id,status='queued',definition_snapshot_json='{}')
        db.add(run);db.commit()
        run,token=claim_run(db,worker_id='audit',lease_seconds=120,run_id=run.id)
        append_event(db,run,'Extracted batch 1: 1 rows.',stage='inspect')
        with SessionLocal() as hb:
            hb.execute(text('PRAGMA busy_timeout=100'))
            with pytest.raises(OperationalError) as exc:
                renew_lease(hb,run_id=run.id,lease_token=token,lease_seconds=120)
            print('heartbeat while extracted event is uncommitted:',str(exc.value).splitlines()[0])
        db.commit()
        with SessionLocal() as hb:
            assert renew_lease(hb,run_id=run.id,lease_token=token,lease_seconds=120)
```

</details>

## Expected Behavior

A slow destination write does not prevent the lease keeper from renewing the active run.

## Actual Behavior

After claim_run and append_event, renew_lease from an independent SQLite session raises database is locked. Committing the event immediately allows renewal. The reproduction shortens busy_timeout to 100 ms; production uses 30 seconds. The database lock itself is real, not mocked.

## Root Cause Analysis

append_event executes UPDATE pipeline_runs and leaves the transaction open. execute_transfer calls destination.write_batch before the following add_counters/commit. The heartbeat competes for that row/write lock. PostgreSQL has the same row-lock contention, although the included reproduction executes SQLite.

## Impact

A healthy long-running COPY or spool operation can prevent renewals, make the keeper declare lease loss, and leave the run for reconciliation. SQLite UI writes also contend with this transaction.

## Suggested Fix

Commit the batch event before entering external destination I/O, or persist progress in a separate short transaction. Keep application-database locks out of remote operations; test the entire slow write path.

## Test Coverage Gaps

Independent-heartbeat tests do not cover the uncommitted Extracted batch event immediately before write_batch. Add a slow destination integration test and require heartbeat renewal throughout it on both supported databases.

The baseline suite passed with **336 passed, 31 deselected**. The targeted audit suite reproduced this defect; passing baseline tests did not exercise the failing boundary.

## Severity

High

## Confidence

High — executable reproduction verified locally. Simulated components and shortened timing controls, where applicable, are identified above.

## Existing Issue Matches

No existing report or open fix found. Reviewed all open issues (0), all four closed issues (#4–#7, UI framework enhancements), and open pull requests (0). Also searched all-state issues and open PRs for each of:

- `append_event renew_lease`
- `database is locked heartbeat`
- `write_batch lease`

All six targeted searches returned zero matches. None of the reviewed documentation declares this behavior an expected limitation.
