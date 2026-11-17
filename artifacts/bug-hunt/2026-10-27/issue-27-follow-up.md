Revalidated the remaining existing-destination variant on October 27, 2026 at `2dd38ef00ed3984663a0896e662989a97e7242e0`, after the intervening timestamp fixes. **The original issue remains reproducible: a TIMESTAMPTZ source appended to an existing naive TIMESTAMP target still finishes succeeded/exact while losing instant semantics.** The local PostgreSQL/real-engine case shows a 14,400-second difference under America/New_York.

**Existing Issue Found:** #27 — PostgreSQL transfers discard timestamptz semantics and shift instants. **Match confidence: High. Severity: High.** This confirms the October 22 follow-up on the current reviewed revision; no duplicate issue is needed.

Affected current code: [_validate_destination_column_casts](https://github.com/eddiethedean/user-token-management-app/blob/2dd38ef00ed3984663a0896e662989a97e7242e0/app/connectors/postgres.py#L1529) and [write_batch](https://github.com/eddiethedean/user-token-management-app/blob/2dd38ef00ed3984663a0896e662989a97e7242e0/app/connectors/postgres.py#L959). The TIMETZ-to-TIME check exists, but there is no equivalent TIMESTAMPTZ-to-naive-TIMESTAMP guard; EXPLAIN permits the lossy assignment. New-table TIMESTAMPTZ mapping is a separate path and does not protect an existing naive target.

Expected/fix: reject this implicit semantic loss before staging unless the owner explicitly selects an approved conversion. Add existing-target append/upsert/replace tests, non-UTC/DST consumer sessions, and value/outcome assertions. Row-count equality cannot verify preserved temporal meaning.

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
import json
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


def test_existing_timezone_issue_remains_on_current_head(
    access_app, make_user, postgres_credentials
):
    owner = make_user("timezone.audit@example.gov")
    cfg = connector_settings()
    with connect(postgres_credentials, cfg) as conn:
        conn.execute("CREATE TABLE audit_source (value timestamptz)")
        conn.execute("CREATE TABLE audit_target (value timestamp)")
        conn.execute("INSERT INTO audit_source VALUES ('2026-10-27 08:00:00-04')")
    with SessionLocal() as db:
        run = _execute_postgres(db, owner, postgres_credentials)
        assert run.status == "succeeded"
        assert json.loads(run.verification_json)["verification_level"] == "exact"
    with connect(postgres_credentials, cfg) as conn:
        conn.execute("SET TIME ZONE 'America/New_York'")
        shift = conn.execute(
            "SELECT extract(epoch FROM (t.value::timestamptz - s.value)) "
            "FROM audit_source s CROSS JOIN audit_target t"
        ).fetchone()[0]
    assert shift == 14400
    print(
        "Existing #27: timezone semantics still lost; 14400-second difference after succeeded/exact"
    )
```

</details>
