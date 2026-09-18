## Verified follow-up: Empty Foundry source files still fail before destination finalization

Existing Issue Found: #20 — [BUG] Foundry destinations cannot finalize valid zero-row sources. Match confidence: Medium (same zero-row workflow, additional source-side failure; its original destination-finalization branch has been fixed). Added this additional affected area to the existing report to avoid splitting the empty-transfer investigation. No open PR matches.

## Description

The zero-row pipeline case remains broken when the source is an existing empty Foundry Parquet file: source extraction raises SOURCE_NOT_FOUND before the destination's empty-output support is reached.

Verified on commit `1865af308509d2b50cf52c3eda64a1654331a4d5`.

## Labels

bug. Recommendations for this follow-up.

## Affected Components

- [app/connectors/foundry.py:636](https://github.com/eddiethedean/user-token-management-app/blob/1865af308509d2b50cf52c3eda64a1654331a4d5/app/connectors/foundry.py#L636) — FoundryConnector.extract
- [app/connectors/foundry.py:688](https://github.com/eddiethedean/user-token-management-app/blob/1865af308509d2b50cf52c3eda64a1654331a4d5/app/connectors/foundry.py#L688) — no-yield failure branch

## Steps to Reproduce

List an existing empty.parquet file containing a valid id:Int64 schema and zero rows. Extract that selected file with MssConnector. The reproduction writes a real empty Parquet file locally and stubs the Foundry download/listing only.

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


def test_foundry_empty_file_reported_missing(monkeypatch, tmp_path):
    from app.connectors.errors import ConnectorError
    connector = foundry_stub(monkeypatch, tmp_path, [{'path': 'empty.parquet'}], pl.DataFrame(schema={'id': pl.Int64}))
    locator = FoundryDatasetFilesLocator(dataset_rid='ri.foundry.main.dataset.audit', branch='master', file_paths=['empty.parquet'])
    with pytest.raises(ConnectorError) as exc:
        list(connector.extract({}, locator, batch_rows=100, batch_bytes=10000))
    print('empty Foundry source:', exc.value.code, str(exc.value))
    assert 'no CSV or Parquet files' in str(exc.value)
```

</details>

## Expected Behavior

Distinguish an existing zero-row file from missing files, and preserve its schema so append or replace can handle the empty source correctly.

## Actual Behavior

extract raises source_not_found / The dataset has no CSV or Parquet files even though empty.parquet exists and was read.

## Root Cause Analysis

All zero-height frames are skipped and the yielded flag is only set for nonempty batches. If no rows were emitted, the connector unconditionally treats the dataset as lacking supported files. inspect_object also returns no source columns, so empty schema information is lost.

## Impact

User: a valid temporarily empty upstream file causes the run to fail. Data: replace operations leave old destination contents intact instead of reflecting the empty source. Production: recurring empty-day transfers fail.

## Suggested Fix

Track whether supported files were found separately from row emission. Preserve the schema of empty files and carry it through the transfer engine to the destination.

## Test Coverage Gaps

The destination fix for #20 covers schema-known empty PostgreSQL/CSV input. Add empty Parquet/CSV Foundry source tests, including append and replace end to end.

Baseline: **481 passed, 31 deselected**. The separate audit suite has **15 passing characterization checks**. Environment: Python 3.12.13, PostgreSQL 16.13, Polars 1.44.2, psycopg 3.3.5.

## Severity

Medium

## Confidence

High — executable reproduction observed locally. Where remote responses or source data are simulated, the boundary is identified above.

## Existing Issue Matches

Existing Issue Found: #20 — [BUG] Foundry destinations cannot finalize valid zero-row sources. Match confidence: Medium (same zero-row workflow, additional source-side failure; its original destination-finalization branch has been fixed). Added this additional affected area to the existing report to avoid splitting the empty-transfer investigation. No open PR matches.

Reviewed all open issues (0 at audit start), all 20 closed issues (including the 16 recent bug reports), and all open PRs (0). Searched all-state issues and open PRs separately with each of:

- `"Foundry" "empty"`
- `"dataset has no CSV or Parquet files"`
- `"extract" "zero-row"`

Read the current README, contributor guidance, user guide, provider protocol notes, runtime runbook, and relevant security/limitation sections. No expected limitation explaining this behavior was found.

