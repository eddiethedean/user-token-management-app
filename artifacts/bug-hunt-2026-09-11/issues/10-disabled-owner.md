## Description

Disabling an account revokes browser sessions but leaves queued transfers executable. The worker decrypts the disabled owner’s saved credentials and runs the transfer.

Verified on commit `7d427cbf9c0ce8203b5664d3736a1d88c210ede0` (local checkout matches GitHub `main`).

## Labels

Recommended: `bug`, `high-priority`, `security`. Applied existing repository label: `bug`.

## Affected Components

- [app/worker.py:103](https://github.com/eddiethedean/user-token-management-app/blob/7d427cbf9c0ce8203b5664d3736a1d88c210ede0/app/worker.py#L103)
- [app/services/secrets.py:326](https://github.com/eddiethedean/user-token-management-app/blob/7d427cbf9c0ce8203b5664d3736a1d88c210ede0/app/services/secrets.py#L326)
- [app/ui/routes/admin.py:307](https://github.com/eddiethedean/user-token-management-app/blob/7d427cbf9c0ce8203b5664d3736a1d88c210ede0/app/ui/routes/admin.py#L307)

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

def test_disabled_owner_still_executes(access_app,demo_connections):
    from sqlalchemy import select
    from app.database import SessionLocal
    from app.models import User
    from app.config import get_settings
    from app.services.pipelines import save_pipeline
    from app.services.pipeline_runs import enqueue_run,snapshot_from_definition
    from app.worker import process_one
    with SessionLocal() as db:
        user=db.scalar(select(User).where(User.email=='admin@example.gov'))
        p=save_pipeline(db,user=user,name='Disabled owner test',source_provider='mss',source_schema='ri.foundry.main.dataset.demo-operations',source_table='mission_orders.parquet',destination_provider='postgres',destination_schema='public',destination_table='audit',write_mode='append',available_providers={'mss','postgres'})
        run=enqueue_run(db,user=user,pipeline=p,snapshot=snapshot_from_definition(p))
        user.status='disabled';user.security_version+=1;db.commit()
        assert process_one(db,get_settings(),run_id=run.id)
        db.refresh(run)
        print('disabled account run status:',run.status)
        assert run.status=='succeeded'
```

</details>

## Expected Behavior

An account disabled before a queued run is claimed cannot start new provider operations. Record a clear cancelled/denied terminal state before decrypting credentials.

## Actual Behavior

Queue a saved MSS-to-PostgreSQL run, set the owner status to disabled and increment security_version (the status mutation performed by the admin route), then call process_one. The run reaches succeeded using fake providers. The ownership decision is shared with real execution.

## Root Cause Analysis

process_one checks only whether db.get(User, run.user_id) returns a row; it never checks is_active. decrypt_user_credentials_for_run likewise checks ownership/existence but not current account eligibility. Disabling a user does not cancel queued runs.

## Impact

Account suspension during incident response does not stop queued data movement with that account’s stored provider credentials. Destination data may still be changed after an administrator disables the owner.

## Suggested Fix

Recheck owner eligibility at claim/credential use and reject queued work for disabled owners. Define and enforce cooperative cancellation for active runs when an account is disabled.

## Test Coverage Gaps

Session revocation tests and worker execution tests are separate. Add a test that queues as an active owner, disables that owner as another administrator, and verifies that connectors are never opened.

The baseline suite passed with **336 passed, 31 deselected**. The targeted audit suite reproduced this defect; passing baseline tests did not exercise the failing boundary.

## Severity

High

## Confidence

High — executable reproduction verified locally. Simulated components and shortened timing controls, where applicable, are identified above.

## Existing Issue Matches

No existing report or open fix found. Reviewed all open issues (0), all four closed issues (#4–#7, UI framework enhancements), and open pull requests (0). Also searched all-state issues and open PRs for each of:

- `process_one disabled`
- `queued disabled account`
- `decrypt_user_credentials_for_run inactive`

All six targeted searches returned zero matches. None of the reviewed documentation declares this behavior an expected limitation.
