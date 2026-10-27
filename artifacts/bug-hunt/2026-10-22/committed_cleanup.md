## Description

A process exit after persisting the Databricks `committed` event but before the staging DROP leaves a durable record that the recovery worker never selects. The staging table can remain indefinitely despite the publication outcome already being known.

Verified on commit `4d6bbeefd5a6b25f39e4f33adc59fa39919f3671` (October 22, 2026). Baseline suite: **972 passed**. Audit suite: **9 passing characterization cases across 6 defects**. Python 3.12.13, Polars 1.44.2, psycopg 3.3.5.

## Labels

`bug`.

## Affected Components

- [app/connectors/databricks.py:727](https://github.com/eddiethedean/user-token-management-app/blob/4d6bbeefd5a6b25f39e4f33adc59fa39919f3671/app/connectors/databricks.py#L727) — `finalize` persists committed before DROP.
- [app/services/databricks_publication.py:105](https://github.com/eddiethedean/user-token-management-app/blob/4d6bbeefd5a6b25f39e4f33adc59fa39919f3671/app/services/databricks_publication.py#L105) — `record_publication_event` sets state committed.
- [app/services/databricks_publication.py:203](https://github.com/eddiethedean/user-token-management-app/blob/4d6bbeefd5a6b25f39e4f33adc59fa39919f3671/app/services/databricks_publication.py#L203) — recovery candidate states omit committed.
- [app/services/pipeline_wakeups.py:56](https://github.com/eddiethedean/user-token-management-app/blob/4d6bbeefd5a6b25f39e4f33adc59fa39919f3671/app/services/pipeline_wakeups.py#L56) — deadline inspection also omits committed.

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
from app.services.databricks_publication import record_publication_event, process_pending_publication_recovery
from tests.test_databricks_coordination import _enabled_databricks_settings, _saved_databricks_credentials, _locator as dbx_locator


def test_committed_publication_crash_has_no_recovery(access_app, make_user):
    owner = make_user("committed.audit@example.gov")
    settings = _enabled_databricks_settings()
    with SessionLocal() as db:
        credentials = _saved_databricks_credentials(db, settings, owner)
        run = PipelineRun(
            user_id=owner.id,
            definition_snapshot_json="{}",
            status="loading",
            stage="transfer",
            lease_token="audit",
            lease_expires_at=utcnow() + timedelta(minutes=1),
        )
        db.add(run)
        db.commit()
        run_id = run.id
        common = dict(
            db=db,
            run_id=run_id,
            lease_token="audit",
            user_id=owner.id,
            locator=dbx_locator(),
            connection_generation=credentials["connection_generation"],
            write_mode="databricks_append",
        )
        record_publication_event(
            **common,
            event="prepare_started",
            staging_name="private.staging.data_mover_" + run_id.replace("-", ""),
        )
        record_publication_event(**common, event="staging_ready")
        record_publication_event(**common, event="publish_submitted")
        record_publication_event(**common, event="committed")
        # Crash immediately after the committed event; lease recovery later terminates the run.
        run.status = "failed_needs_reconciliation"
        run.stage = "failed"
        run.lease_expires_at = utcnow() - timedelta(seconds=1)
        db.commit()
    connector = Mock()
    result = process_pending_publication_recovery(
        settings=settings, session_factory=SessionLocal, connector_resolver=lambda _: connector
    )
    assert result is False
    connector.cleanup_staging.assert_not_called()
    with SessionLocal() as db:
        record = db.get(DatabricksPublication, run_id)
        assert record.state == "committed" and record.confirmed_at is not None
    print("Committed crash state is permanently excluded from staging recovery")
```

</details>

## Expected Behavior

Every confirmed publication with unconfirmed staging cleanup is eligible for idempotent cleanup after lease expiry/restart, without republishing the destination.

## Actual Behavior

After executing real persisted publication events through `committed`, and making the run terminal to represent worker-loss handling, `process_pending_publication_recovery` returns False. It never resolves/calls the cleanup connector and leaves the record in committed. This is a local durable-state reproduction, not a live Databricks crash test.

## Root Cause Analysis

`finalize` commits state `committed` before dropping staging and before recording `cleanup_pending` or `cleanup_confirmed`. Recovery and wakeup queries only include preparing, staging, cleanup_pending, publishing, and uncertain. Thus a crash in that interval strands an intermediate state forever; periodic recovery also cannot select it.

## Impact

Run-scoped Delta copies remain in private staging, consuming storage and extending data retention after a crash. The destination has already committed; cleanup recovery must not publish again. Operators currently need to discover and clean these tables manually.

## Suggested Fix

Persist committed publication as cleanup-pending until DROP is confirmed, or include committed in recovery and deadline eligibility with cleanup-only semantics when `confirmed_at` is set. Update all related state predicates together.

## Test Coverage Gaps

Tests cover uninterrupted committed-to-cleanup transitions and failures during recovery cleanup, but not restart between the original committed event and DROP. Add a fault-injection checkpoint there and verify eventual cleanup, no duplicate publication, and correct listener deadlines.

## Severity

Medium

## Confidence

High — executable reproduction passed locally. Simulated boundaries are explicitly identified above.

## Existing Issue Matches

Reviewed all open issues (0 at audit start), the 98 returned closed issues, and both open PRs (#151 and #153, documentation-only). Targeted all-state issue searches and open-PR searches were also performed. README, user guide, provider documentation, and relevant tests were checked for documented limitations. Issue searches: `committed recovery`, `process_pending_publication_recovery`; open PR search: `publication recovery`. Search matches #132/#138 concern guardrail logging and action outcomes, not Databricks staging cleanup. No matching report or open fix found.
