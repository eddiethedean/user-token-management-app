## Description

A saved Decimal override on a PostgreSQL text source succeeds or fails solely according to batch boundaries. Rows `1.2` and `1.23` succeed in one batch, but fail at Foundry finalization when split into two batches. Both values fit losslessly in a common Decimal.

Verified October 27, 2026 on commit `2dd38ef00ed3984663a0896e662989a97e7242e0`. Baseline: **990 passed**. Audit: **14 passing characterization cases across 8 distinct defects** (including three existing-report matches). Python 3.12.13, Polars 1.44.2, psycopg 3.3.5. No production data or live provider credentials were used.

## Labels

`bug`, `priority: medium`.

## Affected Components

- [_decimal_dtype](https://github.com/eddiethedean/user-token-management-app/blob/2dd38ef00ed3984663a0896e662989a97e7242e0/app/services/column_casting.py#L136)
- [cast_batch_columns](https://github.com/eddiethedean/user-token-management-app/blob/2dd38ef00ed3984663a0896e662989a97e7242e0/app/services/column_casting.py#L68)
- [_lossless_common_dtype](https://github.com/eddiethedean/user-token-management-app/blob/2dd38ef00ed3984663a0896e662989a97e7242e0/app/connectors/foundry.py#L79)
- [_lossless_common_schema](https://github.com/eddiethedean/user-token-management-app/blob/2dd38ef00ed3984663a0896e662989a97e7242e0/app/connectors/foundry.py#L114)
- [FoundryConnector.finalize](https://github.com/eddiethedean/user-token-management-app/blob/2dd38ef00ed3984663a0896e662989a97e7242e0/app/connectors/foundry.py#L1329)

## Steps to Reproduce

1. Create a PostgreSQL source with text values `1.2` and `1.23`.
2. Save a PostgreSQL-to-MSS/MCS-COP route with the amount column cast to Decimal.
3. Execute with PIPELINE_BATCH_ROWS=1.
4. Execute the same route/data with PIPELINE_BATCH_ROWS=2.

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
from decimal import Decimal
import polars as pl
import pytest
from app.config import get_settings
from app.connectors.errors import ConnectorError, TransferErrorCode
from app.connectors.locators import DefinitionSnapshot, FoundryReplaceFilePolicy, FoundryUploadLocator, postgres_table
from app.connectors.mss import MssConnector
from app.connectors.postgres import PostgresConnector, connect
from app.database import SessionLocal
from app.models import PipelineRun
from app.services.pipeline_runs import claim_run
from app.services.transfer_engine import execute_transfer
from tests.postgres_support import connector_settings

pytest_plugins = ["tests.conftest"]


@pytest.mark.parametrize("batch_rows", [1, 2])
def test_foundry_decimal_override_depends_on_batch_size(
    access_app, make_user, postgres_credentials, tmp_path, batch_rows
):
    from tests.simulators.foundry import FOUNDRY_DATASET, FOUNDRY_TOKEN, FoundrySimulator

    owner = make_user("decimal.audit@example.gov")
    cfg = connector_settings()
    with connect(postgres_credentials, cfg) as conn:
        conn.execute("CREATE TABLE audit_source (amount text)")
        conn.execute("INSERT INTO audit_source VALUES ('1.2'), ('1.23')")
    simulator = FoundrySimulator()
    settings = get_settings().model_copy(
        update={"pipeline_spool_root": str(tmp_path), "pipeline_batch_rows": batch_rows}
    )
    target = FoundryUploadLocator(
        dataset_rid=FOUNDRY_DATASET, branch="master", file_name="copy.parquet"
    )
    snapshot = DefinitionSnapshot(
        name="Decimal override audit",
        source_provider="postgres",
        destination_provider="mss",
        source=postgres_table("public", "audit_source"),
        destination=target,
        write_policy=FoundryReplaceFilePolicy(column_type_overrides={"amount": "decimal"}),
    )
    with simulator.serve(), SessionLocal() as db:
        run = PipelineRun(user_id=owner.id, definition_snapshot_json=snapshot.model_dump_json())
        db.add(run)
        db.commit()
        run, lease = claim_run(db, worker_id="audit", lease_seconds=120, run_id=run.id)

        def execute():
            execute_transfer(
                db,
                run=run,
                lease_token=lease,
                snapshot=snapshot,
                source_credentials=postgres_credentials,
                destination_credentials={
                    "endpoint": simulator.base_url,
                    "token": FOUNDRY_TOKEN,
                    "dataset_rid": FOUNDRY_DATASET,
                },
                settings=settings,
                cancel_requested=lambda: False,
                source_resolver=lambda _: PostgresConnector(cfg),
                destination_resolver=lambda _: MssConnector(settings),
                route_policy=lambda *_: True,
                writer_policy=lambda _: True,
            )

        if batch_rows == 1:
            with pytest.raises(ConnectorError) as failure:
                execute()
            assert failure.value.code == TransferErrorCode.SCHEMA_DRIFT
            assert "incompatible column types" in failure.value.summary
            assert "copy.parquet" not in simulator.files
            print("Decimal override failed for 1.2 / 1.23 split across batches; no upload")
        else:
            execute()
            assert run.status == "succeeded"
            frame = pl.read_parquet(io.BytesIO(simulator.files["copy.parquet"]))
            assert frame["amount"].to_list() == [Decimal("1.20"), Decimal("1.23")]
            print("Identical Decimal override and data succeeded when both rows share a batch")
```

</details>

## Expected Behavior

Both runs succeed with exact Decimal values 1.20 and 1.23. Changing the supported batch size must not change whether a valid column can transfer.

## Actual Behavior

One-row batches raise SCHEMA_DRIFT: Foundry batches have incompatible column types; no file was published. Two rows in one batch succeed and the uploaded Parquet contains Decimal 1.20 and 1.23. The reproduction uses the real transfer engine and PostgreSQL connector with the local Foundry HTTP simulator.

## Root Cause Analysis

_decimal_dtype chooses scale separately for each batch, producing Decimal(38,1) and Decimal(38,2). _lossless_common_dtype accepts identical types and selected integer/float promotions but has no safe Decimal scale reconciliation. Finalization therefore rejects data that a single-batch cast represents correctly.

## Impact

Valid production routes can fail late after completing source scanning and local staging, depending on row order, configured batch size, or byte-driven splitting. No remote file is published in the failing reproduction. PostgreSQL-to-Foundry routes with mixed fractional precision and a selected Decimal cast are affected.

## Suggested Fix

Determine a safe shared Decimal shape for the reviewed transfer, or reconcile observed decimal batches losslessly before publication. Preserve all required integer digits when increasing scale and reject only genuinely unrepresentable combinations beyond the supported precision.

## Test Coverage Gaps

Single-batch casts and fixed-schema decimal sources do not exercise per-batch scale inference. Add end-to-end Decimal overrides across varied batch sizes, null-only batches, differing fractional scales, and actual precision-overflow controls; assert exact uploaded Parquet values.

## Severity

Medium

## Confidence

High — reproduced locally through the code paths and boundaries identified above.

## Existing Issue Matches

Reviewed all open issues (0 at audit start), 100 recently updated closed issues, the prior audit, and the complete patches of open documentation PRs #151 and #153. Each finding also received function/error/symptom searches across all issue states and open PRs. README, provider notes, the worker runbook, and relevant security decisions were checked for expected limitations.

Searched _lossless_common_schema, column_type_overrides, incompatible column types, and Foundry/decimal/batch scales. #74 concerns silent integer-to-float promotion; #78 concerns the CSV parser losing decimal precision. This report starts from PostgreSQL text, selects an explicit Decimal cast, and fails before publication because generated decimal scales differ. No reasonable duplicate or open fix found.
