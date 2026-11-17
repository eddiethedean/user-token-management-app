## Description

Databricks inspection erases the distinction between TIMESTAMP_NTZ and TIMESTAMP. Their normalized schemas and schema fingerprints are identical, and copying an NTZ source into a new Delta destination generates a TIMESTAMP staging column. Append compatibility uses the same conflated mapping.

Verified October 27, 2026 on commit `2dd38ef00ed3984663a0896e662989a97e7242e0`. Baseline: **990 passed**. Audit: **14 passing characterization cases across 8 distinct defects** (including three existing-report matches). Python 3.12.13, Polars 1.44.2, psycopg 3.3.5. No production data or live provider credentials were used.

## Labels

`bug`, `priority: high`.

## Affected Components

- [_SQL_TYPES](https://github.com/eddiethedean/user-token-management-app/blob/2dd38ef00ed3984663a0896e662989a97e7242e0/app/connectors/databricks.py#L67)
- [DatabricksConnector.inspect_object and schema fingerprint](https://github.com/eddiethedean/user-token-management-app/blob/2dd38ef00ed3984663a0896e662989a97e7242e0/app/connectors/databricks.py#L361)
- [_polars_type](https://github.com/eddiethedean/user-token-management-app/blob/2dd38ef00ed3984663a0896e662989a97e7242e0/app/connectors/databricks.py#L1338)
- [_sql_type](https://github.com/eddiethedean/user-token-management-app/blob/2dd38ef00ed3984663a0896e662989a97e7242e0/app/connectors/databricks.py#L1366)
- [prepare_destination](https://github.com/eddiethedean/user-token-management-app/blob/2dd38ef00ed3984663a0896e662989a97e7242e0/app/connectors/databricks.py#L510)

## Steps to Reproduce

1. Inspect a Unity Catalog Delta table whose column is reported as TIMESTAMP_NTZ.
2. Have the same object report TIMESTAMP for that column and inspect it again.
3. Compare the schemas and schema fingerprints.
4. Prepare a new Databricks target from the original NTZ schema and inspect the real adapter's generated CREATE TABLE SQL.

Save the following self-contained test outside `tests/`, for example `/tmp/test_bug.py`. Install the repository's development dependencies, then run from the repository root:

```sh
.venv/bin/python -m pytest /tmp/test_bug.py -c pyproject.toml -s --tb=short
```

PostgreSQL cases require the local server binaries used by the repository fixtures. All databases and HTTP services are disposable loopback fixtures. Databricks cases use the existing SQL/management harness. **These are characterization assertions: passing means the defect was reproduced.** Invert them when adding regression tests.

<details>
<summary>Executable reproduction</summary>

```python
from __future__ import annotations
import uuid
from app.connectors.locators import DatabricksCreatePolicy
from tests.test_databricks_connector import _sql_connector, _SQLHarness, _table_locator

pytest_plugins = ["tests.conftest"]


def test_databricks_loses_timestamp_ntz_identity(access_app):
    harness = _SQLHarness(
        exists=True, columns=[("occurred", "timestamp_ntz", "TIMESTAMP_NTZ", "YES")]
    )
    source, credentials = _sql_connector(harness)
    schema_ntz = source.inspect_object(credentials, _table_locator())
    harness.columns = [("occurred", "timestamp", "TIMESTAMP", "YES")]
    schema_tz = source.inspect_object(credentials, _table_locator())
    assert schema_ntz.columns == schema_tz.columns
    assert schema_ntz.schema_fingerprint == schema_tz.schema_fingerprint

    target_harness = _SQLHarness(exists=False)
    destination, credentials = _sql_connector(target_harness)
    credentials["_publication_event"] = lambda *a, **kw: None
    load = destination.prepare_destination(
        credentials,
        _table_locator(table="copy_of_ntz"),
        schema_ntz,
        DatabricksCreatePolicy(),
        run_id=str(uuid.uuid4()),
    )
    create = next(sql for sql, _ in target_harness.statements if sql.startswith("CREATE TABLE"))
    assert "`occurred` TIMESTAMP" in create
    assert "TIMESTAMP_NTZ" not in create
    destination.abort(load)
    print(
        "TIMESTAMP and TIMESTAMP_NTZ have identical schema fingerprints; NTZ copy declares TIMESTAMP"
    )
```

</details>

## Expected Behavior

Preserve wall-clock/no-zone and instant/session-zone semantics as distinct types through inspection, fingerprinting, append checks, extraction, and destination creation; alternatively reject an unsupported type explicitly before a transfer.

## Actual Behavior

Both schemas are `Datetime(time_unit='us')` and have identical fingerprints. The NTZ copy emits a staging declaration containing `occurred TIMESTAMP`, with no TIMESTAMP_NTZ. This is verified using the real adapter and the repository's controlled SQL/management harness; a live workspace was not contacted.

## Root Cause Analysis

Both native SQL type names map to the same Polars label in _SQL_TYPES. inspect_object fingerprints that normalized label instead of retaining the native semantic type, and _sql_type maps the resulting label only to TIMESTAMP. The lost distinction cannot be recovered by the writer.

## Impact

Wall-clock data can become an instant-based type without an explicit conversion, and a source or target TIMESTAMP/NTZ change can evade schema-drift checks. The generated type and fingerprint collision are confirmed; the exact value shift for a particular live SQL session depends on provider/session timezone behavior and is not claimed as a live acceptance result. Databricks documents the distinction in its [TIMESTAMP_NTZ reference](https://docs.databricks.com/aws/en/sql/language-manual/data-types/timestamp-ntz-type) and [TIMESTAMP reference](https://docs.databricks.com/aws/en/sql/language-manual/data-types/timestamp-type).

## Suggested Fix

Carry native temporal semantics in the connector schema, preserve them in schema fingerprints, and map naive NTZ and instant timestamps to the appropriate Delta and Polars types. Recheck append compatibility using those distinctions. Reject variants the connector cannot preserve.

## Test Coverage Gaps

Timestamp mapping tests cover the presence of a supported timestamp but not the difference between the two native families. Add distinct fingerprints and writer declarations, negative append/drift cases, and live round-trip acceptance with non-UTC sessions.

## Severity

High

## Confidence

High — reproduced locally through the code paths and boundaries identified above.

## Existing Issue Matches

Reviewed all open issues (0 at audit start), 100 recently updated closed issues, the prior audit, and the complete patches of open documentation PRs #151 and #153. Each finding also received function/error/symptom searches across all issue states and open PRs. README, provider notes, the worker runbook, and relevant security decisions were checked for expected limitations.

Exact all-state TIMESTAMP_NTZ searches returned no issues. Also reviewed #27 (PostgreSQL timestamptz semantics), #155 (false drift from Polars string spelling), and #156 (Databricks rejecting parameterized dtype spelling). This is distinct: accepted native Databricks temporal families collapse to the same type and fingerprint. No open fix found.
