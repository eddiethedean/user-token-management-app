## Verified follow-up: CSV inspection and extraction disagree on all-empty rows

Existing Issue Found: #15 — [BUG] CSV transfers ignore the delimiter and normalized headers shown by upload inspection. Match confidence: Medium (same inspection/execution parsing-contract mismatch, additional row-policy scenario; the original delimiter/header bug is not claimed to remain broken). Added to that report rather than opening a second parsing-contract issue. Closed #19 concerns type inference rather than row inclusion. No open PR matches.

## Description

CSV inspection and execution still apply different row rules after the delimiter/header fix. An all-empty record is omitted from the displayed row count but transferred as an additional NULL row.

Verified on commit `1865af308509d2b50cf52c3eda64a1654331a4d5`.

## Labels

bug. Recommendations for this follow-up.

## Affected Components

- [app/services/csv_uploads.py:88](https://github.com/eddiethedean/user-token-management-app/blob/1865af308509d2b50cf52c3eda64a1654331a4d5/app/services/csv_uploads.py#L88) — inspect_csv
- [app/connectors/csv_source.py:115](https://github.com/eddiethedean/user-token-management-app/blob/1865af308509d2b50cf52c3eda64a1654331a4d5/app/connectors/csv_source.py#L115) — CsvSourceConnector._frame

## Steps to Reproduce

Inspect and extract `a,b\n1,2\n,\n3,4\n`, passing the persisted delimiter, normalized columns and inferred column types exactly as the worker does.

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


def test_csv_blank_rows_differ_from_inspection():
    from app.connectors.csv_source import CsvSourceConnector
    from app.services.csv_uploads import inspect_csv
    payload = b'a,b\n1,2\n,\n3,4\n'
    profile = inspect_csv('sample.csv', payload)
    locator = CsvUploadLocator(upload_id=str(uuid.uuid4()), checksum_sha256=hashlib.sha256(payload).hexdigest())
    credentials = {'content': payload, 'delimiter': profile.delimiter,
        'columns': json.dumps([c.name for c in profile.columns]),
        'column_types': json.dumps([c.inferred_type for c in profile.columns])}
    batches = list(CsvSourceConnector().extract(credentials, locator, batch_rows=100, batch_bytes=10000))
    print('CSV inspection rows:', profile.row_count, 'extracted rows:', sum(b.row_count for b in batches))
    assert profile.row_count == 2 and sum(b.row_count for b in batches) == 3
```

</details>

## Expected Behavior

Displayed inspection counts and transferred records follow one documented rule for all-empty records.

## Actual Behavior

Inspection reports two rows. Extraction emits three rows, including a row with both cells NULL.

## Root Cause Analysis

inspect_csv skips records when all stripped fields are empty; pl.read_csv in the execution connector retains an all-empty delimited record. Passing delimiter and header metadata does not unify this row policy.

## Impact

User: the approved preview differs from the actual transfer. Data: an unpreviewed NULL record can be inserted, or a NOT NULL destination can reject the load. Production: apparently valid uploads fail or produce unexpected rows.

## Suggested Fix

Share the row-normalization policy between inspection and execution. Either consistently retain empty records or consistently remove them; ensure counts, null statistics and loaded data agree.

## Test Coverage Gaps

Add an end-to-end upload-to-extract test for empty delimited records, whitespace-only cells, and files with only empty records; verify row counts and actual data.

Baseline: **481 passed, 31 deselected**. The separate audit suite has **15 passing characterization checks**. Environment: Python 3.12.13, PostgreSQL 16.13, Polars 1.44.2, psycopg 3.3.5.

## Severity

Medium

## Confidence

High — executable reproduction observed locally. Where remote responses or source data are simulated, the boundary is identified above.

## Existing Issue Matches

Existing Issue Found: #15 — [BUG] CSV transfers ignore the delimiter and normalized headers shown by upload inspection. Match confidence: Medium (same inspection/execution parsing-contract mismatch, additional row-policy scenario; the original delimiter/header bug is not claimed to remain broken). Added to that report rather than opening a second parsing-contract issue. Closed #19 concerns type inference rather than row inclusion. No open PR matches.

Reviewed all open issues (0 at audit start), all 20 closed issues (including the 16 recent bug reports), and all open PRs (0). Searched all-state issues and open PRs separately with each of:

- `"inspect_csv"`
- `"CSV" "blank rows"`
- `"CsvSourceConnector" "row count"`

Read the current README, contributor guidance, user guide, provider protocol notes, runtime runbook, and relevant security/limitation sections. No expected limitation explaining this behavior was found.

