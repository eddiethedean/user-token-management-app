## Description

The retention janitor deletes Databricks publication evidence while recovery is unresolved. It purges succeeded runs with pending staging cleanup, and reviewed uncertain runs whose publication still owns an active target fence. Deleting the run cascades to the publication row.

Verified October 27, 2026 on commit `2dd38ef00ed3984663a0896e662989a97e7242e0`. Baseline: **990 passed**. Audit: **14 passing characterization cases across 8 distinct defects** (including three existing-report matches). Python 3.12.13, Polars 1.44.2, psycopg 3.3.5. No production data or live provider credentials were used.

## Labels

`bug`, `priority: high`.

## Affected Components

- [janitor](https://github.com/eddiethedean/user-token-management-app/blob/2dd38ef00ed3984663a0896e662989a97e7242e0/app/services/pipeline_runs.py#L1272)
- [DatabricksPublication foreign key and unique target fence](https://github.com/eddiethedean/user-token-management-app/blob/2dd38ef00ed3984663a0896e662989a97e7242e0/app/models.py#L311)
- [record_reconciliation_review](https://github.com/eddiethedean/user-token-management-app/blob/2dd38ef00ed3984663a0896e662989a97e7242e0/app/services/pipeline_runs.py#L386)
- [process_pending_publication_recovery](https://github.com/eddiethedean/user-token-management-app/blob/2dd38ef00ed3984663a0896e662989a97e7242e0/app/services/databricks_publication.py#L180)

## Steps to Reproduce

1. Persist a terminal run older than PIPELINE_RUN_RETENTION_DAYS and an associated DatabricksPublication.
2. For the first case use state `uncertain`, an active_target_key, and a failed_needs_reconciliation run; record an operator reconciliation review.
3. For the second case use state `cleanup_pending`, a confirmed commit, and a succeeded run.
4. Run the real retention janitor, then query both the run and publication tables.

Save the following self-contained test outside `tests/`, for example `/tmp/test_bug.py`. Install the repository's development dependencies, then run from the repository root:

```sh
.venv/bin/python -m pytest /tmp/test_bug.py -c pyproject.toml -s --tb=short
```

PostgreSQL cases require the local server binaries used by the repository fixtures. All databases and HTTP services are disposable loopback fixtures. Databricks cases use the existing SQL/management harness. **These are characterization assertions: passing means the defect was reproduced.** Invert them when adding regression tests.

<details>
<summary>Executable reproduction</summary>

```python
from __future__ import annotations
from datetime import timedelta
import pytest
from app.config import get_settings
from app.database import SessionLocal
from app.models import DatabricksPublication, PipelineRun, utcnow
from app.services.pipeline_runs import janitor, record_reconciliation_review
from tests.test_databricks_connector import _table_locator

pytest_plugins = ["tests.conftest"]


@pytest.mark.parametrize("publication_state", ["uncertain", "cleanup_pending"])
def test_retention_deletes_pending_publication(access_app, make_user, publication_state):
    owner = make_user("retention.audit@example.gov")
    stale = utcnow() - timedelta(days=100)
    settings = get_settings()
    with SessionLocal() as db:
        run = PipelineRun(
            user_id=owner.id,
            definition_snapshot_json="{}",
            status="failed_needs_reconciliation"
            if publication_state == "uncertain"
            else "succeeded",
            stage="reconcile" if publication_state == "uncertain" else "verify",
            finished_at=stale,
            created_at=stale,
            queued_at=stale,
            started_at=stale,
            reconciliation_required=publication_state == "uncertain",
            data_impact="uncertain" if publication_state == "uncertain" else "verified",
        )
        db.add(run)
        db.flush()
        run_id = run.id
        publication = DatabricksPublication(
            run_id=run.id,
            user_id=owner.id,
            workspace_id="a" * 64,
            connection_generation="test-generation",
            target_key="b" * 64,
            active_target_key="b" * 64 if publication_state == "uncertain" else None,
            target_locator_json=_table_locator().model_dump_json(),
            write_mode="databricks_append",
            staging_name="private.staging.audit",
            state=publication_state,
            submission_id=run.id,
            submitted_at=stale,
            created_at=stale,
            updated_at=stale,
            confirmed_at=stale if publication_state == "cleanup_pending" else None,
        )
        db.add(publication)
        db.commit()
        if publication_state == "uncertain":
            record_reconciliation_review(db, user=owner, run_id=run.id)
        assert db.get(DatabricksPublication, run_id) is not None
    with SessionLocal() as db:
        counts = janitor(db, settings)
        assert counts["runs"] == 1
        assert db.get(DatabricksPublication, run_id) is None
    print(
        f"Janitor cascaded unresolved {publication_state} publication, including its fence/evidence"
    )
```

</details>

## Expected Behavior

Retain unresolved publication evidence and any active target fence until provider reconciliation and required cleanup finish, regardless of the ordinary run-retention cutoff or a recorded operator review.

## Actual Behavior

Both runs are purged and their publication rows disappear through ON DELETE CASCADE. The uncertain case loses its active target fence; the confirmed-commit case loses the only durable record directing staging cleanup. Both cases pass executable characterization assertions.

## Root Cause Analysis

janitor checks only run-level reconciliation requirements/review flags before deletion. It never checks DatabricksPublication.state, active_target_key, or pending cleanup. The foreign key cascades deletion to the durable operation table, bypassing its recovery lifecycle and uniqueness fence.

## Impact

A long-unresolved publication can lose its recovery/audit evidence and the database constraint that prevents another publication to the same target. Confirmed publications can leave remote staging data indefinitely. Defaults allow this after 90 days; supported shorter retention settings shorten the window. Loss of the fence is verified in persistence; no live duplicate Databricks write was attempted.

## Suggested Fix

Protect runs and their events whenever an associated publication is unresolved, owns a fence, or still needs staging cleanup. Resolve the provider operation before allowing run deletion, or retain publication evidence independently with suitable referential integrity. Operator review must not substitute for provider commit/cleanup confirmation.

## Test Coverage Gaps

The existing janitor test protects an unreviewed uncertain run but does not attach a DatabricksPublication. Recovery tests cover operation states independently of run retention. Add both reproduced cases, an unreviewed control, and resolved complete/unchanged publication controls.

## Severity

High

## Confidence

High — reproduced locally through the code paths and boundaries identified above.

## Existing Issue Matches

Reviewed all open issues (0 at audit start), 100 recently updated closed issues, the prior audit, and the complete patches of open documentation PRs #151 and #153. Each finding also received function/error/symptom searches across all issue states and open PRs. README, provider notes, the worker runbook, and relevant security decisions were checked for expected limitations.

Searched janitor, retention, DatabricksPublication, cascade, fence, and cleanup. #158 concerns a committed record excluded from the recovery selector; that selector now includes committed, but this report concerns deletion of the record itself by a different service. No existing report or open PR covers the retention/cascade defect.
