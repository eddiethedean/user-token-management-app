## Description

The Connect timeout field previously accepted arbitrary strings. Credential validation now rejects invalid values before they reach a connector.

The historical defect was verified on commit `7d427cbf9c0ce8203b5664d3736a1d88c210ede0`; the regression below targets the corrected current checkout.

## Labels

Recommended: `bug`. Applied existing repository label: `bug`.

## Affected Components

- [app/services/secret_validation.py:24](https://github.com/eddiethedean/user-token-management-app/blob/7d427cbf9c0ce8203b5664d3736a1d88c210ede0/app/services/secret_validation.py#L24)
- [app/services/secret_catalog.py:122](https://github.com/eddiethedean/user-token-management-app/blob/7d427cbf9c0ce8203b5664d3736a1d88c210ede0/app/services/secret_catalog.py#L122)
- [app/connectors/postgres.py:92](https://github.com/eddiethedean/user-token-management-app/blob/7d427cbf9c0ce8203b5664d3736a1d88c210ede0/app/connectors/postgres.py#L92)

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

def test_invalid_connect_timeout_rejected():
    from app.services.secret_validation import CredentialValidator
    from app.services.secret_catalog import SECRET_CATALOG
    with pytest.raises(ValueError, match='Connect timeout') as exc:
        CredentialValidator().validate(SECRET_CATALOG.require('postgres'),dict(host='localhost',port='5432',database='x',username='x',password='x',sslmode='require',connect_timeout='abc'))
    print('invalid timeout rejected:',str(exc.value))
```

</details>

## Expected Behavior

Reject nonnumeric or out-of-range connection timeouts when saving credentials, with a field-specific validation message. This behavior is now implemented.

## Actual Behavior

CredentialValidator rejects connect_timeout="abc" with a field-specific ValueError before connection establishment. The connection-test route therefore receives normal validation feedback instead of deferring the failure to connector setup.

## Root Cause Analysis

CredentialValidator validates and bounds connect_timeout before credentials are persisted or passed to a connector.

## Impact

Users receive an actionable validation error when an unusable timeout is submitted.

## Suggested Fix

Completed: parse and bound connect_timeout during credential validation and return an actionable field error.

## Test Coverage Gaps

Credential tests cover invalid timeout strings and related bounds alongside invalid ports and SSL choices.

The baseline suite passed with **336 passed, 31 deselected**. The targeted audit reproduction has been converted into regression coverage for the fixed boundary.

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
