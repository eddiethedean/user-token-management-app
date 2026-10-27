## Description

A PostgreSQL TIMESTAMP WITHOUT TIME ZONE column is extracted correctly, but the transfer engine rejects its first nonempty batch as schema drift. The provider schema and Polars schema denote the same type; only their string representations differ.

Verified on commit `4d6bbeefd5a6b25f39e4f33adc59fa39919f3671` (October 22, 2026). Baseline suite: **972 passed**. Audit suite: **9 passing characterization cases across 6 defects**. Python 3.12.13, Polars 1.44.2, psycopg 3.3.5.

## Labels

`bug`, `priority: high`.

## Affected Components

- [app/services/transfer_engine.py:233](https://github.com/eddiethedean/user-token-management-app/blob/4d6bbeefd5a6b25f39e4f33adc59fa39919f3671/app/services/transfer_engine.py#L233) — `_normalized_source_type`.
- [app/services/transfer_engine.py:884](https://github.com/eddiethedean/user-token-management-app/blob/4d6bbeefd5a6b25f39e4f33adc59fa39919f3671/app/services/transfer_engine.py#L884) — `execute_transfer` batch schema comparison.
- [app/connectors/postgres.py:653](https://github.com/eddiethedean/user-token-management-app/blob/4d6bbeefd5a6b25f39e4f33adc59fa39919f3671/app/connectors/postgres.py#L653) — `PostgresConnector.extract`.

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


def test_native_postgres_timestamp_falsely_fails_schema_drift(
    access_app, make_user, postgres_credentials
):
    owner = make_user("timestamp.audit@example.gov")
    settings = get_settings()
    with connect(postgres_credentials, connector_settings()) as conn:
        conn.execute("CREATE TABLE timestamp_source (occurred timestamp)")
        conn.execute("INSERT INTO timestamp_source VALUES ('2026-10-22 12:30:45')")
    snapshot = DefinitionSnapshot(
        name="timestamp audit",
        source_provider="postgres",
        destination_provider="postgres",
        source=postgres_table("public", "timestamp_source"),
        destination=postgres_table("public", "timestamp_target"),
        write_policy=PostgresAppendPolicy(),
    )
    with SessionLocal() as db:
        run = PipelineRun(user_id=owner.id, definition_snapshot_json=snapshot.model_dump_json())
        db.add(run)
        db.commit()
        run, lease = claim_run(db, worker_id="audit", lease_seconds=120, run_id=run.id)
        with pytest.raises(ConnectorError) as failure:
            execute_transfer(
                db,
                run=run,
                lease_token=lease,
                snapshot=snapshot,
                source_credentials=postgres_credentials,
                destination_credentials=postgres_credentials,
                settings=settings,
                cancel_requested=lambda: False,
                source_resolver=lambda _: PostgresConnector(connector_settings()),
                destination_resolver=lambda _: PostgresConnector(connector_settings()),
                route_policy=lambda *_: True,
                writer_policy=lambda _: True,
            )
        assert failure.value.code == TransferErrorCode.SCHEMA_DRIFT
        assert failure.value.summary == "The source schema changed during extraction."
        print("Unchanged PostgreSQL timestamp fails:", failure.value.code, failure.value.summary)
    with connect(postgres_credentials, connector_settings()) as conn:
        assert conn.execute("SELECT to_regclass('public.timestamp_target')").fetchone() == (None,)
```

</details>

## Expected Behavior

An unchanged supported timestamp source passes the scan-time schema check and transfers its values.

## Actual Behavior

A real PostgreSQL source containing `2026-10-22 12:30:45` raises `SCHEMA_DRIFT: The source schema changed during extraction.` The destination table is never created. No concurrent schema change occurs.

## Root Cause Analysis

The native label `timestamp without time zone` and shortened label `Datetime(time_unit='us')` normalize to `timestamp`, but the installed Polars version emits `Datetime(time_unit='us', time_zone=None)`. That representation falls through unchanged and compares unequal. Databricks also advertises the shortened timestamp schema label, so its timestamp extraction follows the same mismatched comparison; the executable integration case here uses real PostgreSQL.

## Impact

Nonempty PostgreSQL routes containing a common timestamp type cannot run, regardless of destination. The failure happens before destination writes, so this is an availability defect rather than data corruption.

## Suggested Fix

Normalize structured datetime types consistently, including explicit `time_zone=None`, without conflating timezone-aware and naive timestamps. Centralize supported type normalization and exercise real connector batches through the engine.

## Test Coverage Gaps

Connector tests prove timestamp extraction and destination creation independently; engine tests predominantly use integer-only sources. Add end-to-end PostgreSQL timestamp routes and Databricks timestamp batches, plus negative checks that genuinely changed types still fail.

## Severity

High

## Confidence

High — executable reproduction passed locally. Simulated boundaries are explicitly identified above.

## Existing Issue Matches

Reviewed all open issues (0 at audit start), the 98 returned closed issues, and both open PRs (#151 and #153, documentation-only). Targeted all-state issue searches and open-PR searches were also performed. README, user guide, provider documentation, and relevant tests were checked for documented limitations. Issue searches: `_normalized_source_type`, `"source schema changed" timestamp`; open PR search: `_normalized_source_type`. #27 concerns lost timezone semantics and #29 concerns creating TEXT destinations. Neither reports false drift on an unchanged timestamp source.
