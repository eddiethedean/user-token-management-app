Follow-up audit on October 22, 2026 at `4d6bbeefd5a6b25f39e4f33adc59fa39919f3671`: **the new-destination fix holds, but an existing-destination variant remains reproducible**.

**Match confidence: High. Severity: High.** Adding this evidence here rather than opening a duplicate timezone-loss issue. The original issue remains closed; this comment records the remaining case for follow-up.

A TIMESTAMPTZ source containing `2026-09-17 08:00:00-04` can be appended to an existing TIMESTAMP WITHOUT TIME ZONE target. Preflight accepts the route, COPY silently discards `+00:00` from the normalized source value, and the full transfer engine reports **succeeded / exact**. Reading/casting the result under America/New_York gives a **14,400-second drift**. The destination now stores a wall-clock timestamp rather than the source instant; row-count verification cannot detect this loss.

Affected code: [app/connectors/postgres.py:1519](https://github.com/eddiethedean/user-token-management-app/blob/4d6bbeefd5a6b25f39e4f33adc59fa39919f3671/app/connectors/postgres.py#L1519) (`_validate_destination_column_casts`) and [app/connectors/postgres.py:949](https://github.com/eddiethedean/user-token-management-app/blob/4d6bbeefd5a6b25f39e4f33adc59fa39919f3671/app/connectors/postgres.py#L949) (`write_batch`). The compatibility check explicitly rejects TIMETZ to TIME but has no corresponding guard for TIMESTAMPTZ to naive TIMESTAMP. Its EXPLAIN check accepts the implicit lossy cast; staging clones the existing destination type.

Expected: reject timezone loss before staging unless the user explicitly chooses a documented conversion. Suggested fix: validate temporal type/zone compatibility for existing destinations, and keep this distinct from ordinary row-count reconciliation.

Additional coverage needed: append and upsert into existing naive timestamp columns, non-UTC consumer sessions and DST boundaries, and full-engine outcome assertions. Current tests prove new-table TIMESTAMPTZ creation and TIMETZ rejection but miss this existing-table case.

Baseline: 972 passed. Audit: 9 characterization cases passed. All PostgreSQL I/O below uses disposable local databases; the application fixture supplies isolated SQLite run storage. The source/destination are real PostgreSQL connectors injected into the real transfer engine.

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


def test_timezone_loss_is_reported_as_success_by_real_transfer_engine(
    access_app, make_user, postgres_credentials
):
    owner = make_user("tz.engine.audit@example.gov")
    with connect(postgres_credentials, connector_settings()) as conn:
        conn.execute("CREATE TABLE tz_engine_source (occurred timestamptz)")
        conn.execute("INSERT INTO tz_engine_source VALUES ('2026-09-17 08:00:00-04')")
        conn.execute("CREATE TABLE tz_engine_target (occurred timestamp)")
    snapshot = DefinitionSnapshot(
        name="timezone audit",
        source_provider="postgres",
        destination_provider="postgres",
        source=postgres_table("public", "tz_engine_source"),
        destination=postgres_table("public", "tz_engine_target"),
        write_policy=PostgresAppendPolicy(),
    )
    with SessionLocal() as db:
        run = PipelineRun(user_id=owner.id, definition_snapshot_json=snapshot.model_dump_json())
        db.add(run)
        db.commit()
        run, lease = claim_run(db, worker_id="audit", lease_seconds=120, run_id=run.id)
        execute_transfer(
            db,
            run=run,
            lease_token=lease,
            snapshot=snapshot,
            source_credentials=postgres_credentials,
            destination_credentials=postgres_credentials,
            settings=get_settings(),
            cancel_requested=lambda: False,
            source_resolver=lambda _: PostgresConnector(connector_settings()),
            destination_resolver=lambda _: PostgresConnector(connector_settings()),
            route_policy=lambda *_: True,
            writer_policy=lambda _: True,
        )
        db.refresh(run)
        assert run.status == "succeeded"
        print("Timezone-loss pipeline status:", run.status, "verification:", run.verification_json)
    with connect(postgres_credentials, connector_settings()) as conn:
        conn.execute("SET TIME ZONE 'America/New_York'")
        drift = conn.execute(
            "SELECT EXTRACT(EPOCH FROM d.occurred::timestamptz)-EXTRACT(EPOCH FROM s.occurred) FROM tz_engine_target d,tz_engine_source s"
        ).fetchone()[0]
        assert drift == 14400
```

</details>


Deduplication: reviewed 0 open issues, 98 closed issues and open documentation PRs #151/#153. Searched all-state issues for `timestamptz` and `timezone`, and open PRs for `timezone`. #27 is the existing issue match; no open fix covers this case.
