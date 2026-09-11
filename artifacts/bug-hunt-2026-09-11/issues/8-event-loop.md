## Description

The asynchronous connection-test route executes synchronous connector I/O directly on the ASGI event-loop thread. Pipeline page rendering likewise calls synchronous catalog/provider operations from an async handler. A slow provider can stall unrelated requests handled by the same app process.

Verified on commit `7d427cbf9c0ce8203b5664d3736a1d88c210ede0` (local checkout matches GitHub `main`).

## Labels

Recommended: `bug`, `high-priority`. Applied existing repository label: `bug`.

## Affected Components

- [app/ui/routes/security.py:432](https://github.com/eddiethedean/user-token-management-app/blob/7d427cbf9c0ce8203b5664d3736a1d88c210ede0/app/ui/routes/security.py#L432)
- [app/ui/routes/pipeline.py:2500](https://github.com/eddiethedean/user-token-management-app/blob/7d427cbf9c0ce8203b5664d3736a1d88c210ede0/app/ui/routes/pipeline.py#L2500)
- [app/services/secrets.py:202](https://github.com/eddiethedean/user-token-management-app/blob/7d427cbf9c0ce8203b5664d3736a1d88c210ede0/app/services/secrets.py#L202)
- [app/services/catalogs.py:76](https://github.com/eddiethedean/user-token-management-app/blob/7d427cbf9c0ce8203b5664d3736a1d88c210ede0/app/services/catalogs.py#L76)

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

def test_slow_connection_test_blocks_event_loop(client, demo_connections, monkeypatch):
    import asyncio,time,threading
    import httpx2
    from tests.helpers import web_login,csrf_from
    from app.ui.routes import security
    web_login(client)
    csrf=csrf_from(client.get('/security').text)
    original=security.test_user_connection
    observed=[]
    def slow(*args,**kwargs):
        observed.append(threading.get_ident())
        time.sleep(.25)
        return original(*args,**kwargs)
    monkeypatch.setattr(security,'test_user_connection',slow)
    async def run():
        loop_thread=threading.get_ident()
        ticks=[]
        async def ticker():
            for _ in range(50):
                ticks.append(time.monotonic());await asyncio.sleep(.01)
        async with httpx2.AsyncClient(transport=httpx2.ASGITransport(app=client.app),base_url='http://testserver',cookies=client.cookies) as ac:
            async def request():
                r=await ac.post('/security/secrets/postgres/test',data={'csrf_token':csrf})
                assert r.status_code==303
            await asyncio.gather(ticker(),request())
        gap=max(b-a for a,b in zip(ticks,ticks[1:]))
        print('slow provider runs on event loop:',observed==[loop_thread],'max ticker gap:',gap)
        assert observed == [loop_thread] and gap>.24
    asyncio.run(run())
```

</details>

## Expected Behavior

Provider I/O runs off the event loop, so other requests, polling, health checks, and async runtime supervision remain responsive.

## Actual Behavior

A 250 ms delay at the connection-test service boundary ran on the event-loop thread and caused a 266 ms gap in an independent 10 ms async ticker. The request still returned 303. This used a simulated slow service; no real provider was contacted.

## Root Cause Analysis

connection_test_submit is async but calls test_user_connection without offloading; that service calls synchronous psycopg/httpx2 connector methods. pipeline_page evaluates _pipeline_body synchronously before awaiting render_authenticated_view.

## Impact

One authenticated user waiting on a slow provider can stall every request on that process. Configured provider timeouts reach minutes; health-check failures and process restarts can interrupt active transfers.

## Suggested Fix

Use async provider clients or offload the complete synchronous service/render operation to a worker thread with a session owned by that thread. Do not share an SQLAlchemy Session across simultaneous operations.

## Test Coverage Gaps

Existing HTTP tests check responses against fast fake providers. Add concurrent slow-provider and unrelated health-request tests that measure event-loop responsiveness.

The baseline suite passed with **336 passed, 31 deselected**. The targeted audit suite reproduced this defect; passing baseline tests did not exercise the failing boundary.

## Severity

High

## Confidence

High — executable reproduction verified locally. Simulated components and shortened timing controls, where applicable, are identified above.

## Existing Issue Matches

No existing report or open fix found. Reviewed all open issues (0), all four closed issues (#4–#7, UI framework enhancements), and open pull requests (0). Also searched all-state issues and open PRs for each of:

- `connection_test_submit`
- `test_user_connection event loop`
- `pipeline_page blocking`

All six targeted searches returned zero matches. None of the reviewed documentation declares this behavior an expected limitation.
