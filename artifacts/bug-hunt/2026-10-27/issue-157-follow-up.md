Additional parser/profile mismatches verified October 27, 2026 at `2dd38ef00ed3984663a0896e662989a97e7242e0`. The Unicode numeric and ISO-week-date fix is present, but the same defect remains for accepted time/timestamp spellings.

**Existing Issue Found:** #157 — CSV profiling accepts numeric and date syntax that extraction cannot parse. **Match confidence: High. Severity: Medium.**

- Uploaded CSV `T12:34:56` profiles as time, then raises UNSUPPORTED_TYPE during extraction.
- A quoted uploaded CSV value `12:34:56,123` also profiles as time, then fails extraction.
- A Foundry CSV value `2026-10-27 12:30` profiles as datetime; the lazy scan advertises that type, then collection raises InvalidOperationError. The upload connector accepts that same minute-only timestamp, confirming inconsistent execution grammars between adapters.

Affected current code: [_value_type](https://github.com/eddiethedean/user-token-management-app/blob/2dd38ef00ed3984663a0896e662989a97e7242e0/app/domain/csv_inference.py#L143), [_normalize_profiled_cells](https://github.com/eddiethedean/user-token-management-app/blob/2dd38ef00ed3984663a0896e662989a97e7242e0/app/connectors/csv_source.py#L232), and [_csv_file_scan](https://github.com/eddiethedean/user-token-management-app/blob/2dd38ef00ed3984663a0896e662989a97e7242e0/app/connectors/foundry.py#L848). The Foundry adapter normalizes additional time forms, while the upload path does not; Foundry's String-to-Datetime cast rejects some spellings the profiler accepts.

Expected/fix: every accepted typed profile must be executable. Share an explicit profiling/normalization/parsing contract, or classify unsupported forms as text before accepting the scan. Extend tests across both adapters for T-prefixed times, comma fractional seconds, minute-only timestamps, nulls, and mixed accepted formats. These are availability failures with no destination publication.

Verified October 27, 2026 on commit `2dd38ef00ed3984663a0896e662989a97e7242e0`. Baseline: **990 passed**. Audit: **14 passing characterization cases across 8 distinct defects** (including three existing-report matches). Python 3.12.13, Polars 1.44.2, psycopg 3.3.5. No production data or live provider credentials were used.

Save the following self-contained test outside `tests/`, for example `/tmp/test_bug.py`. Install the repository's development dependencies, then run from the repository root:

```sh
.venv/bin/python -m pytest /tmp/test_bug.py -c pyproject.toml -s --tb=short
```

PostgreSQL cases require the local server binaries used by the repository fixtures. All databases and HTTP services are disposable loopback fixtures. Databricks cases use the existing SQL/management harness. **These are characterization assertions: passing means the defect was reproduced.** Invert them when adding regression tests.

<details>
<summary>Executable reproduction</summary>

```python
from __future__ import annotations
import polars as pl
import pytest
from app.connectors.csv_source import CsvSourceConnector
from app.connectors.errors import ConnectorError, TransferErrorCode
from app.services.csv_uploads import inspect_csv
from tests.test_csv_decimal_precision import _csv_credentials, _locator

pytest_plugins = ["tests.conftest"]


@pytest.mark.parametrize("value", ["T12:34:56", "12:34:56,123"])
def test_uploaded_csv_still_profiles_unparseable_time_values(value):
    content = ('value,label\n"' + value + '",ok\n').encode()
    inspection = inspect_csv("time.csv", content)
    assert inspection.columns[0].inferred_type == "time"
    credentials = _csv_credentials(content, inspection)
    credentials["quote_char"] = inspection.quote_char
    credentials["skip_initial_space"] = inspection.skip_initial_space
    with pytest.raises(ConnectorError) as failure:
        list(
            CsvSourceConnector().extract(credentials, _locator(), batch_rows=10, batch_bytes=10000)
        )
    assert failure.value.code == TransferErrorCode.UNSUPPORTED_TYPE
    print(f"Existing #157: CSV {value!r} profiles as time, then fails extraction")


def test_foundry_csv_still_profiles_unparseable_minute_timestamp(tmp_path):
    from app.connectors.foundry import _csv_file_scan

    path = tmp_path / "minutes.csv"
    path.write_bytes(b"occurred,label\n2026-10-27 12:30,ok\n")
    scan, profiles = _csv_file_scan(path)
    assert profiles[0].inferred_type == "datetime"
    with pytest.raises(pl.exceptions.InvalidOperationError):
        scan.collect()
    print(
        "Existing #157: Foundry CSV minute-only datetime profiles successfully, then fails extraction"
    )
```

</details>
