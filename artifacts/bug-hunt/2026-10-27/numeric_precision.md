## Description

A BIGINT source can pass destination preflight and finish as succeeded with exact verification while an existing REAL or DOUBLE PRECISION destination silently changes its values. This occurs with the default append policy and no user-selected cast.

Verified October 27, 2026 on commit `2dd38ef00ed3984663a0896e662989a97e7242e0`. Baseline: **990 passed**. Audit: **14 passing characterization cases across 8 distinct defects** (including three existing-report matches). Python 3.12.13, Polars 1.44.2, psycopg 3.3.5. No production data or live provider credentials were used.

## Labels

`bug`, `priority: high`.

## Affected Components

- [_validate_destination_column_casts](https://github.com/eddiethedean/user-token-management-app/blob/2dd38ef00ed3984663a0896e662989a97e7242e0/app/connectors/postgres.py#L1529)
- [PostgresConnector.prepare_destination](https://github.com/eddiethedean/user-token-management-app/blob/2dd38ef00ed3984663a0896e662989a97e7242e0/app/connectors/postgres.py#L716)
- [PostgresConnector.write_batch](https://github.com/eddiethedean/user-token-management-app/blob/2dd38ef00ed3984663a0896e662989a97e7242e0/app/connectors/postgres.py#L959)
- [_reconcile_write_result](https://github.com/eddiethedean/user-token-management-app/blob/2dd38ef00ed3984663a0896e662989a97e7242e0/app/services/transfer_engine.py#L115)

## Steps to Reproduce

1. Create a source table with a BIGINT value of `1000000001` and an existing target with a REAL column.
2. Select an append route without column-type overrides; run read-only preflight and execute it.
3. Read the destination value and persisted run verification.
4. Repeat with BIGINT `9007199254740993` and a DOUBLE PRECISION target.

Save the following self-contained test outside `tests/`, for example `/tmp/test_bug.py`. Install the repository's development dependencies, then run from the repository root:

```sh
.venv/bin/python -m pytest /tmp/test_bug.py -c pyproject.toml -s --tb=short
```

PostgreSQL cases require the local server binaries used by the repository fixtures. All databases and HTTP services are disposable loopback fixtures. Databricks cases use the existing SQL/management harness. **These are characterization assertions: passing means the defect was reproduced.** Invert them when adding regression tests.

<details>
<summary>Executable reproduction</summary>

```python
from __future__ import annotations
import json
from decimal import Decimal
import pytest
from app.config import get_settings
from app.connectors.locators import DefinitionSnapshot, PostgresAppendPolicy, postgres_table
from app.connectors.postgres import PostgresConnector, connect
from app.database import SessionLocal
from app.models import PipelineRun
from app.services.pipeline_runs import claim_run
from app.services.transfer_engine import execute_transfer
from tests.postgres_support import connector_settings

pytest_plugins = ["tests.conftest"]


def _execute_postgres(db, owner, credentials, *, settings=None):
    snapshot = DefinitionSnapshot(
        name="PostgreSQL type audit",
        source_provider="postgres",
        destination_provider="postgres",
        source=postgres_table("public", "audit_source"),
        destination=postgres_table("public", "audit_target"),
        write_policy=PostgresAppendPolicy(),
    )
    run = PipelineRun(user_id=owner.id, definition_snapshot_json=snapshot.model_dump_json())
    db.add(run)
    db.commit()
    run, lease = claim_run(db, worker_id="audit", lease_seconds=120, run_id=run.id)
    execute_transfer(
        db,
        run=run,
        lease_token=lease,
        snapshot=snapshot,
        source_credentials=credentials,
        destination_credentials=credentials,
        settings=settings or get_settings(),
        cancel_requested=lambda: False,
        source_resolver=lambda _: PostgresConnector(connector_settings()),
        destination_resolver=lambda _: PostgresConnector(connector_settings()),
        route_policy=lambda *_: True,
        writer_policy=lambda _: True,
    )
    return run


@pytest.mark.parametrize(
    ("destination_type", "source_value"),
    [("real", 1_000_000_001), ("double precision", 9_007_199_254_740_993)],
)
def test_existing_float_target_silently_rounds_integer(
    access_app, make_user, postgres_credentials, destination_type, source_value
):
    owner = make_user("numeric.audit@example.gov")
    cfg = connector_settings()
    with connect(postgres_credentials, cfg) as conn:
        conn.execute("CREATE TABLE audit_source (value bigint)")
        conn.execute(f"CREATE TABLE audit_target (value {destination_type})")
        conn.execute("INSERT INTO audit_source VALUES (%s)", (source_value,))
    connector = PostgresConnector(cfg)
    source_schema = connector.inspect_object(
        postgres_credentials, postgres_table("public", "audit_source")
    )
    connector.preflight_destination(
        postgres_credentials,
        postgres_table("public", "audit_target"),
        source_schema,
        PostgresAppendPolicy(),
    )
    with SessionLocal() as db:
        run = _execute_postgres(db, owner, postgres_credentials)
        assert run.status == "succeeded"
        assert json.loads(run.verification_json)["verification_level"] == "exact"
    with connect(postgres_credentials, cfg) as conn:
        actual = Decimal.from_float(conn.execute("SELECT value FROM audit_target").fetchone()[0])
    assert actual != Decimal(source_value)
    print(f"Integer {source_value} -> {destination_type} {actual}; run succeeded/exact")
```

</details>

## Expected Behavior

Reject an implicitly lossy numeric route before publication, or require a deliberate, clearly communicated conversion policy. An exact integer must not silently change under the default transfer.

## Actual Behavior

Preflight accepts both routes. REAL stores `1000000000`; DOUBLE PRECISION stores `9007199254740992`. Both runs report `succeeded` and `verification_level: exact`. Values were retrieved as Python floats and converted with Decimal.from_float, avoiding any additional PostgreSQL float-to-NUMERIC display rounding.

## Root Cause Analysis

Compatibility validation asks EXPLAIN whether PostgreSQL permits the assignment; it does not check whether the destination can represent the source numeric domain or actual values exactly. Staging clones the destination float type, so COPY has already rounded the integer before finalization. Decimal-specific safeguards do not cover integer sources. Row-count reconciliation cannot detect changed values.

## Impact

Identifiers, counters, and exact integer measures can be corrupted in an otherwise successful production transfer. All row-count checks can pass, leaving the problem invisible in the monitor. The reproduced path uses real disposable PostgreSQL databases and the real transfer engine.

## Suggested Fix

Extend destination compatibility checks to integer-to-floating-point and other narrowing numeric conversions. Reject values outside the target exact-integer range unless an explicit conversion is chosen; preserve an exact staging representation until that decision is enforced. Keep row-count verification accurately distinguished from value-fidelity guarantees.

## Test Coverage Gaps

The baseline checks decimal preservation and row-count reconciliation, but misses BIGINT-to-existing-REAL/DOUBLE routes. Add append/upsert/replace coverage at and just outside the exact-integer boundaries, asserting stored values and run outcomes, with and without explicit overrides.

## Severity

High

## Confidence

High — reproduced locally through the code paths and boundaries identified above.

## Existing Issue Matches

Reviewed all open issues (0 at audit start), 100 recently updated closed issues, the prior audit, and the complete patches of open documentation PRs #151 and #153. Each finding also received function/error/symptom searches across all issue states and open PRs. README, provider notes, the worker runbook, and relevant security decisions were checked for expected limitations.

Searched `_validate_destination_column_casts`, PostgreSQL/integer/precision/rounding/float, and related symptoms across all issue states. #11 concerns a Decimal source mapped to floating point when creating a new table; #78 concerns CSV parsing; #74 concerns Foundry batch promotion. This report uses native integer sources and an already existing PostgreSQL target. #27 is the separate temporal-semantics variant. No matching open fix was found.
