## Description

Full-file profiling accepts Unicode numeric digits and ISO week dates as typed columns, but execution reparses those same strings with a narrower Polars grammar and fails. This violates the documented agreement between the scanned profile and transfer types.

Verified on commit `4d6bbeefd5a6b25f39e4f33adc59fa39919f3671` (October 22, 2026). Baseline suite: **972 passed**. Audit suite: **9 passing characterization cases across 6 defects**. Python 3.12.13, Polars 1.44.2, psycopg 3.3.5.

## Labels

`bug`.

## Affected Components

- [app/domain/csv_inference.py:13](https://github.com/eddiethedean/user-token-management-app/blob/4d6bbeefd5a6b25f39e4f33adc59fa39919f3671/app/domain/csv_inference.py#L13) — Unicode-aware numeric patterns.
- [app/domain/csv_inference.py:131](https://github.com/eddiethedean/user-token-management-app/blob/4d6bbeefd5a6b25f39e4f33adc59fa39919f3671/app/domain/csv_inference.py#L131) — `_value_type` Python numeric/date parsing.
- [app/connectors/csv_source.py:204](https://github.com/eddiethedean/user-token-management-app/blob/4d6bbeefd5a6b25f39e4f33adc59fa39919f3671/app/connectors/csv_source.py#L204) — `_frame` calls `pl.read_csv` with inferred types.
- [app/connectors/csv_source.py:245](https://github.com/eddiethedean/user-token-management-app/blob/4d6bbeefd5a6b25f39e4f33adc59fa39919f3671/app/connectors/csv_source.py#L245) — `_normalize_profiled_cells` does not normalize these forms.

## Steps to Reproduce

1. Check out the audited commit and install its development dependencies. PostgreSQL cases need local `postgres`/`initdb` binaries, as in the repository test suite.
2. Save the following test outside `tests/` as `/tmp/test_bug.py`.
3. From the repository root run `.venv/bin/python -m pytest /tmp/test_bug.py -c pyproject.toml -s`.

These are **characterization assertions**: they pass when the defect is present. Invert them when adding regression tests. The code reuses test helpers from this exact commit. Databases are disposable local fixtures and all credentials are synthetic; no live Databricks or Foundry service is contacted.

<details>
<summary>Executable reproduction</summary>

```python
import uuid
from datetime import timedelta
from unittest.mock import Mock
import polars as pl
import psycopg
import pytest
from app.config import get_settings
from app.connectors.base import ObjectSchema, ColumnSchema, TransferBatch
from app.connectors.errors import ConnectorError, TransferErrorCode
from app.connectors.locators import DefinitionSnapshot, PostgresAppendPolicy, DatabricksCreatePolicy, postgres_table
from app.connectors.postgres import PostgresConnector, connect
from app.database import SessionLocal
from app.models import PipelineRun, DatabricksPublication, utcnow
from app.services.pipeline_runs import claim_run
from app.services.transfer_engine import execute_transfer
from tests.postgres_support import connector_settings
pytest_plugins = ['tests.conftest']
from app.connectors.csv_source import CsvSourceConnector
from app.services.csv_uploads import inspect_csv
from tests.test_csv_decimal_precision import _csv_credentials, _locator


@pytest.mark.parametrize("value", ["１２", "١٢.٣", "2026-W43-4"])
def test_csv_profile_accepts_values_extraction_rejects(value):
    content = ("value,label\n" + value + ",ok\n").encode()
    inspection = inspect_csv("boundary.csv", content)
    assert inspection.columns[0].inferred_type in {"integer", "decimal", "date"}
    with pytest.raises(ConnectorError) as failure:
        list(
            CsvSourceConnector().extract(
                _csv_credentials(content, inspection), _locator(), batch_rows=100, batch_bytes=10000
            )
        )
    print(
        "CSV accepted",
        repr(value),
        inspection.columns[0].inferred_type,
        "then failed:",
        failure.value.code,
    )
    assert failure.value.code == TransferErrorCode.UNSUPPORTED_TYPE
```

</details>

## Expected Behavior

Every accepted typed profile can be extracted consistently, or unsupported forms are classified as text/rejected during inspection with actionable feedback.

## Actual Behavior

`１２` profiles as integer, `١٢.٣` as decimal, and `2026-W43-4` as date. Each one-row UTF-8 CSV passes `inspect_csv`, then extraction raises `UNSUPPORTED_TYPE: The CSV file could not be parsed.`

## Root Cause Analysis

Python regex `\d`, `int`/Decimal, and `date.fromisoformat` accept forms that the downstream Polars CSV parser does not. The shared normalization step only strips typed whitespace and case-folds booleans; it does not reconcile numeric alphabets or date grammars.

## Impact

Users receive a successful scan but cannot execute affected uploads. International numeric input and valid ISO week-date exports are affected. The failure occurs before destination writes.

## Suggested Fix

Use one shared parser/normalization contract for profiling and extraction. Either normalize accepted values losslessly into the execution grammar or conservatively retain unsupported spellings as text. Keep original values for text columns.

## Test Coverage Gaps

Tests cover common ASCII numbers, decimal precision, delimiters, and conventional dates, but not cross-parser acceptance for Unicode decimal digits or ISO week dates. Add a profile/extract parity matrix rather than testing the two parsers independently.

## Severity

Medium

## Confidence

High — executable reproduction passed locally. Simulated boundaries are explicitly identified above.

## Existing Issue Matches

Reviewed all open issues (0 at audit start), the 98 returned closed issues, and both open PRs (#151 and #153, documentation-only). Targeted all-state issue searches and open-PR searches were also performed. README, user guide, provider documentation, and relevant tests were checked for documented limitations. Issue searches: `Unicode CSV`, `week date`, `profile_csv_rows`; open PR search: `CSV Unicode`. #15 is delimiter/header propagation and #19 is bounded schema inference missing later text. This reproduction supplies correct delimiter and full-file types and fails on one row, so neither prior fix addresses this grammar mismatch.
