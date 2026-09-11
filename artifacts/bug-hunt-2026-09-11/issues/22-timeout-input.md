## Description

The Connect timeout field is stored as an arbitrary string. Credential validation accepts "abc", but the connector immediately calls int() on it.

Verified on commit `7d427cbf9c0ce8203b5664d3736a1d88c210ede0` (local checkout matches GitHub `main`).

## Labels

Recommended: `bug`. Applied existing repository label: `bug`.

## Affected Components

- [app/services/secret_validation.py:24](https://github.com/eddiethedean/user-token-management-app/blob/7d427cbf9c0ce8203b5664d3736a1d88c210ede0/app/services/secret_validation.py#L24)
- [app/services/secret_catalog.py:122](https://github.com/eddiethedean/user-token-management-app/blob/7d427cbf9c0ce8203b5664d3736a1d88c210ede0/app/services/secret_catalog.py#L122)
- [app/connectors/postgres.py:92](https://github.com/eddiethedean/user-token-management-app/blob/7d427cbf9c0ce8203b5664d3736a1d88c210ede0/app/connectors/postgres.py#L92)

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

def test_invalid_connect_timeout_stored():
    from app.services.secret_validation import validate_credentials
    from app.services.secret_catalog import SECRET_CATALOG
    creds=validate_credentials(SECRET_CATALOG.require('postgres'),dict(host='localhost',port='5432',database='x',username='x',password='x',sslmode='require',connect_timeout='abc'))
    assert creds['connect_timeout']=='abc'
    with pytest.raises(ValueError) as exc:
        connect(creds,connector_settings())
    print('validated timeout crashes:',str(exc.value))
```

</details>

## Expected Behavior

Reject nonnumeric or out-of-range connection timeouts when saving credentials, with a field-specific validation message.

## Actual Behavior

validate_credentials accepts connect_timeout="abc" and returns it unchanged. connect then raises ValueError("invalid literal for int() with base 10: 'abc'"). The connection-test route catches ValueError and returns 400; this is not an authentication bypass or an uncaught HTTP 500.

## Root Cause Analysis

CredentialValidator validates port and selected transport choices but has no connect_timeout validation. Conversion occurs only at connection establishment.

## Impact

Users receive a successful save for an unusable connection and must discover the mistake in a separate test. The validation state remains untested rather than containing a normal recorded health result.

## Suggested Fix

Parse and bound connect_timeout during credential validation and return an actionable field error. Normalize the saved value before use.

## Test Coverage Gaps

Credential tests cover invalid ports and SSL choices but not invalid timeout strings, zero/negative values, or excessive values.

The baseline suite passed with **336 passed, 31 deselected**. The targeted audit suite reproduced this defect; passing baseline tests did not exercise the failing boundary.

## Severity

Low

## Confidence

High — executable reproduction verified locally. Simulated components and shortened timing controls, where applicable, are identified above.

## Existing Issue Matches

No existing report or open fix found. Reviewed all open issues (0), all four closed issues (#4–#7, UI framework enhancements), and open pull requests (0). Also searched all-state issues and open PRs for each of:

- `connect_timeout validation`
- `invalid literal int`
- `CredentialValidator timeout`

All six targeted searches returned zero matches. None of the reviewed documentation declares this behavior an expected limitation.
