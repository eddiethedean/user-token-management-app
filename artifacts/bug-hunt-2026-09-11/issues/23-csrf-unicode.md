## Description

CSRF validators pass user-controlled Python strings directly to hmac.compare_digest. That function rejects non-ASCII strings rather than returning false, and the validators do not catch the resulting TypeError.

Verified on commit `7d427cbf9c0ce8203b5664d3736a1d88c210ede0` (local checkout matches GitHub `main`).

## Labels

Recommended: `bug`. Applied existing repository label: `bug`.

## Affected Components

- [app/security/csrf.py:41](https://github.com/eddiethedean/user-token-management-app/blob/7d427cbf9c0ce8203b5664d3736a1d88c210ede0/app/security/csrf.py#L41)
- [app/security/csrf.py:143](https://github.com/eddiethedean/user-token-management-app/blob/7d427cbf9c0ce8203b5664d3736a1d88c210ede0/app/security/csrf.py#L143)
- [app/dependencies.py:175](https://github.com/eddiethedean/user-token-management-app/blob/7d427cbf9c0ce8203b5664d3736a1d88c210ede0/app/dependencies.py#L175)

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

def test_non_ascii_csrf_returns_500(client):
    from tests.helpers import web_login
    import httpx2,asyncio
    client.get('/login')
    async def submit():
        async with httpx2.AsyncClient(transport=httpx2.ASGITransport(app=client.app,raise_app_exceptions=False),base_url='http://testserver',cookies=client.cookies) as ac:
            return await ac.post('/login',data={'email':'admin@example.gov','password':'not-used','preauth_csrf_token':'é'})
    response=asyncio.run(submit())
    print('non-ASCII preauth CSRF HTTP status:',response.status_code)
    assert response.status_code==500
    web_login(client)
    async def logout():
        async with httpx2.AsyncClient(transport=httpx2.ASGITransport(app=client.app,raise_app_exceptions=False),base_url='http://testserver',cookies=client.cookies) as ac:
            return await ac.post('/logout',data={'csrf_token':'é'})
    response=asyncio.run(logout())
    print('non-ASCII session CSRF HTTP status:',response.status_code)
    assert response.status_code==500
```

</details>

## Expected Behavior

Malformed or non-ASCII CSRF input is denied with HTTP 403 without raising a server exception.

## Actual Behavior

After GET /login creates a preauth cookie, POST /login with preauth_csrf_token="é" returns HTTP 500. After a normal login, POST /logout with csrf_token="é" also returns HTTP 500. Both were verified through ASGI HTTP requests.

## Root Cause Analysis

validate_preauth_csrf and assert_csrf call compare_digest(str, str) before checking that submitted values are ASCII. Form inputs allow arbitrary Unicode.

## Impact

Unauthenticated malformed requests can trigger server errors and exception logging, and authenticated forms return the wrong error for invalid input. The checks do not permit a CSRF bypass.

## Suggested Fix

Validate the expected token alphabet or compare explicitly encoded byte strings, treating invalid encodings and malformed values as false/403.

## Test Coverage Gaps

Existing invalid-token tests use ASCII mismatches. Add non-ASCII form values and malformed preauth cookie values to both validation paths.

The baseline suite passed with **336 passed, 31 deselected**. The targeted audit suite reproduced this defect; passing baseline tests did not exercise the failing boundary.

## Severity

Low

## Confidence

High — executable reproduction verified locally. Simulated components and shortened timing controls, where applicable, are identified above.

## Existing Issue Matches

No existing report or open fix found. Reviewed all open issues (0), all four closed issues (#4–#7, UI framework enhancements), and open pull requests (0). Also searched all-state issues and open PRs for each of:

- `assert_csrf TypeError`
- `compare_digest non ASCII`
- `CSRF Unicode`

All six targeted searches returned zero matches. None of the reviewed documentation declares this behavior an expected limitation.
