## Description

One CSV file in an MCS-COP dataset makes listing the entire destination catalog fail, including otherwise usable Parquet files.

Verified on commit `1865af308509d2b50cf52c3eda64a1654331a4d5`.

## Labels

bug. Applied existing repository label: bug; other labels are recommendations.

## Affected Components

- [app/connectors/foundry.py:527](https://github.com/eddiethedean/user-token-management-app/blob/1865af308509d2b50cf52c3eda64a1654331a4d5/app/connectors/foundry.py#L527) — supported_files
- [app/connectors/foundry.py:597](https://github.com/eddiethedean/user-token-management-app/blob/1865af308509d2b50cf52c3eda64a1654331a4d5/app/connectors/foundry.py#L597) — FoundryConnector.list_objects
- [app/domain/locators.py:90](https://github.com/eddiethedean/user-token-management-app/blob/1865af308509d2b50cf52c3eda64a1654331a4d5/app/domain/locators.py#L90) — FoundryUploadLocator.validate_file_name

## Steps to Reproduce

Return two ordinary entries from the Foundry files listing: good.parquet and old.csv. Call McscopConnector.list_objects for that dataset. The executable test stubs only the remote listing/client; it executes the real connector and locator validation.

1. Check out the commit above and install the project's development dependencies.
2. Save the code below as `/tmp/test_bug.py` (outside `tests/`, because it explicitly loads the shared fixtures).
3. From the repository root run `.venv/bin/python -m pytest /tmp/test_bug.py -c pyproject.toml -s`.

These are **characterization assertions**: passing means the defect was reproduced. Convert them to expected-behavior assertions for regression coverage. PostgreSQL fixtures create disposable local databases and need `postgres`/`initdb`; no production database or live Foundry service is used.

<details>
<summary>Executable reproduction</summary>

```python
"""Characterization tests: pass only when the audited defect is present."""
import hashlib
import json
import uuid
from datetime import datetime, timezone
from unittest.mock import Mock

import polars as pl
import psycopg
import pytest

from app.config import Settings
from app.connectors.base import ColumnSchema, ObjectSchema, TransferBatch
from app.connectors.locators import (
    CsvUploadLocator, FoundryDatasetFilesLocator, PostgresAppendPolicy,
    PostgresUpsertPolicy, postgres_table,
)
from app.connectors.postgres import PostgresConnector, connect, drop_abandoned_staging
from tests.postgres_support import connector_settings

pytest_plugins = ['tests.conftest']


def load(creds, table, frame, policy=None, schema=None):
    connector = PostgresConnector(connector_settings())
    locator = postgres_table('public', table)
    schema = schema or ObjectSchema(locator=locator, columns=tuple(
        ColumnSchema(name=name, data_type=str(dtype)) for name, dtype in frame.schema.items()
    ))
    session = connector.prepare_destination(creds, locator, schema,
        policy or PostgresAppendPolicy(), run_id=str(uuid.uuid4()))
    try:
        connector.write_batch(session, TransferBatch(frame=frame, row_count=frame.height,
            byte_count=frame.estimated_size(), sequence=1))
        return connector.finalize(session)
    finally:
        connector.abort(session)


def foundry_stub(monkeypatch, tmp_path, entries, frame=None, destination=False):
    from app.connectors.mss import MssConnector
    from app.connectors.mcscop import McscopConnector
    connector = (McscopConnector if destination else MssConnector)(Settings(_env_file=None, pipeline_spool_root=str(tmp_path)))
    client = Mock(default_branch='master')
    client.list_all_files.return_value = entries
    client.resolve_branch.return_value = ('master', entries)
    if frame is not None:
        client.download_file.side_effect = lambda rid, branch, path, dest: frame.write_parquet(dest)
    monkeypatch.setattr(connector, '_client', lambda _: client)
    return connector


def test_foundry_destination_csv_catalog_crashes(monkeypatch, tmp_path):
    from pydantic import ValidationError
    connector = foundry_stub(monkeypatch, tmp_path, [{'path': 'good.parquet'}, {'path': 'old.csv'}], destination=True)
    with pytest.raises(ValidationError) as exc:
        connector.list_objects({}, 'ri.foundry.main.dataset.audit')
    print('CSV destination catalog:', str(exc.value).splitlines()[1])
```

</details>

## Expected Behavior

The destination catalog returns supported writable Parquet entries and skips or marks the read-only CSV entry without failing the entire catalog.

## Actual Behavior

The call raises pydantic.ValidationError for file_name: Foundry uploads must use a .parquet filename. No catalog page is returned.

## Root Cause Analysis

`supported_files()` accepts both CSV and Parquet for reading. Destination-only list_objects reuses that list and constructs a FoundryUploadLocator for every entry, but that locator requires the .parquet suffix.

## Impact

User: an existing CSV can prevent browsing/selecting usable MCS-COP destinations. Production: unrelated valid files become unavailable to route authoring until the catalog error is corrected. No live Foundry request or HTTP-route status was asserted in the reproduction.

## Suggested Fix

Separate source-readable suffixes from destination-writable entries; validate/filter each destination entry before constructing the page. Keep unsupported files from invalidating supported entries.

## Test Coverage Gaps

Destination catalog tests use Parquet-only entries. Add mixed CSV/Parquet, uppercase suffix, and unsupported-file cases using destination-only capabilities.

Baseline: **481 passed, 31 deselected**. The separate audit suite has **15 passing characterization checks**. Environment: Python 3.12.13, PostgreSQL 16.13, Polars 1.44.2, psycopg 3.3.5.

## Severity

Medium

## Confidence

High — executable reproduction observed locally. Where remote responses or source data are simulated, the boundary is identified above.

## Existing Issue Matches

All targeted issue/open-PR searches were empty. The .parquet-only write policy is documented; failing an entire mixed dataset's catalog is not.

Reviewed all open issues (0 at audit start), all 20 closed issues (including the 16 recent bug reports), and all open PRs (0). Searched all-state issues and open PRs separately with each of:

- `"list_objects" "csv"`
- `"FoundryUploadLocator" "ValidationError"`
- `"MCS-COP" "catalog"`

Read the current README, contributor guidance, user guide, provider protocol notes, runtime runbook, and relevant security/limitation sections. No expected limitation explaining this behavior was found.

