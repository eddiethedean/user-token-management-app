## Description

A valid zero-row Foundry source file is listed and inspected successfully, but extract raises SOURCE_NOT_FOUND claiming that the dataset has no CSV or Parquet files. The failure occurs in the source adapter before a destination can handle the empty schema.

Verified October 27, 2026 on commit `2dd38ef00ed3984663a0896e662989a97e7242e0`. Baseline: **990 passed**. Audit: **14 passing characterization cases across 8 distinct defects** (including three existing-report matches). Python 3.12.13, Polars 1.44.2, psycopg 3.3.5. No production data or live provider credentials were used.

## Labels

`bug`, `priority: medium`.

## Affected Components

- [FoundryConnector.inspect_object](https://github.com/eddiethedean/user-token-management-app/blob/2dd38ef00ed3984663a0896e662989a97e7242e0/app/connectors/foundry.py#L1047)
- [FoundryConnector.extract](https://github.com/eddiethedean/user-token-management-app/blob/2dd38ef00ed3984663a0896e662989a97e7242e0/app/connectors/foundry.py#L1170)
- [MssConnector](https://github.com/eddiethedean/user-token-management-app/blob/2dd38ef00ed3984663a0896e662989a97e7242e0/app/connectors/mss.py#L10)
- [McscopConnector](https://github.com/eddiethedean/user-token-management-app/blob/2dd38ef00ed3984663a0896e662989a97e7242e0/app/connectors/mcscop.py#L10)

## Steps to Reproduce

1. Put a header-only CSV (`value,label`) or a typed, zero-row Parquet file into a listed Foundry dataset/branch.
2. Select that file as an MSS/MCS-COP source and inspect it; the schema is available.
3. Execute extraction or any transfer using that source.

Save the following self-contained test outside `tests/`, for example `/tmp/test_bug.py`. Install the repository's development dependencies, then run from the repository root:

```sh
.venv/bin/python -m pytest /tmp/test_bug.py -c pyproject.toml -s --tb=short
```

PostgreSQL cases require the local server binaries used by the repository fixtures. All databases and HTTP services are disposable loopback fixtures. Databricks cases use the existing SQL/management harness. **These are characterization assertions: passing means the defect was reproduced.** Invert them when adding regression tests.

<details>
<summary>Executable reproduction</summary>

```python
from __future__ import annotations
import io
import polars as pl
import pytest
from app.config import get_settings
from app.connectors.errors import ConnectorError, TransferErrorCode
from app.connectors.foundry import FoundryConnector
from app.connectors.locators import FoundryDatasetFilesLocator

pytest_plugins = ["tests.conftest"]


@pytest.mark.parametrize("format", ["csv", "parquet"])
def test_empty_foundry_source_is_reported_missing(access_app, tmp_path, format):
    from tests.simulators.foundry import FOUNDRY_DATASET, FOUNDRY_TOKEN, FoundrySimulator

    simulator = FoundrySimulator()
    path = "notes.csv" if format == "csv" else "readiness.parquet"
    if format == "csv":
        simulator.files[path] = b"value,label\n"
    else:
        buffer = io.BytesIO()
        pl.DataFrame(schema={"value": pl.Int64, "label": pl.String}).write_parquet(buffer)
        simulator.files[path] = buffer.getvalue()
    settings = get_settings().model_copy(update={"pipeline_spool_root": str(tmp_path)})
    with simulator.serve():
        connector = FoundryConnector(settings)
        credentials = {
            "endpoint": simulator.base_url,
            "token": FOUNDRY_TOKEN,
            "dataset_rid": FOUNDRY_DATASET,
        }
        source = FoundryDatasetFilesLocator(
            dataset_rid=FOUNDRY_DATASET, branch="master", file_paths=[path]
        )
        schema = connector.inspect_object(credentials, source)
        assert [column.name for column in schema.columns] == ["value", "label"]
        with pytest.raises(ConnectorError) as failure:
            list(connector.extract(credentials, source, batch_rows=10, batch_bytes=10000))
        assert failure.value.code == TransferErrorCode.SOURCE_NOT_FOUND
        assert "no CSV or Parquet files" in failure.value.summary
    print(
        f"Existing zero-row Foundry {format} is inspected successfully, then reported as no files"
    )
```

</details>

## Expected Behavior

A selected file that exists and has a valid empty schema yields zero batches without being classified as missing. The engine can then apply its normal zero-row destination/write-policy semantics.

## Actual Behavior

Both existing file formats inspect successfully with columns value and label. Extraction then raises SOURCE_NOT_FOUND with The dataset has no CSV or Parquet files. The characterization test uses the real connector and local Foundry HTTP simulator.

## Root Cause Analysis

extract tracks a yielded flag only when a nonempty bounded batch is produced. Its final if-not-yielded branch equates zero rows with no supported files, even though the selected paths were resolved and downloaded. File availability and row availability are different conditions.

## Impact

Empty snapshots and legitimate no-data days cannot be transferred from Foundry to any destination. In particular, an intended replace-with-empty operation fails instead of applying the selected destination policy. No destination write occurs in the reproduced failure.

## Suggested Fix

Decide missing-file errors from the resolved selected paths, not from whether a nonempty batch was emitted. Preserve the inspected empty schema and finish the source iterator normally for supported zero-row files; keep missing/unsupported-file errors distinct.

## Test Coverage Gaps

Tests cover nonempty source extraction and zero-row destination finalization separately. Add header-only CSV and typed-empty Parquet sources to both PostgreSQL and Foundry destinations, including append and replace semantics, plus a genuinely missing-file control.

## Severity

Medium

## Confidence

High — reproduced locally through the code paths and boundaries identified above.

## Existing Issue Matches

Reviewed all open issues (0 at audit start), 100 recently updated closed issues, the prior audit, and the complete patches of open documentation PRs #151 and #153. Each finding also received function/error/symptom searches across all issue states and open PRs. README, provider notes, the worker runbook, and relevant security decisions were checked for expected limitations.

Searched Foundry/empty, extract, SOURCE_NOT_FOUND, and the exact no CSV or Parquet files message. Closed #20 fixes Foundry destination finalization when no batches arrive; this defect is the Foundry source iterator throwing before any destination gets that opportunity. It reproduces independently of destination and the #20 writer fix. No matching open PR found.
