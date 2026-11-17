An additional stale-health writer is still vulnerable on October 27, 2026 at `2dd38ef00ed3984663a0896e662989a97e7242e0`: `create_foundry_dataset`, even though `test_user_connection` now has revision/check-ID guards.

**Existing Issue Found:** #81 — Stale connection tests overwrite results for replacement credentials. **Match confidence: High. Severity: Medium.** Same user-visible defect and missing generation guard; adding the affected path here instead of opening a duplicate.

Deterministic reproduction: start dataset creation using saved credential bundle A; while its provider call is in flight, use a second real database session to replace A with B and persist a failed connection check for B; then let A's dataset creation succeed. The encrypted stored bundle remains B, but its validation_status becomes connected and its message claims authentication was established by successful dataset creation. The provider boundary is controlled and synthetic; encryption, persistence, replacement, health checking, and dataset service code are real.

Affected current code: [create_foundry_dataset](https://github.com/eddiethedean/user-token-management-app/blob/2dd38ef00ed3984663a0896e662989a97e7242e0/app/services/foundry_datasets.py#L40) (unconditional health assignment near its end). Unlike the protected tester, it does not predicate its health write on the credential revision used for the side effect. It also records the dataset created under A against the owner/provider slot now holding B.

Expected/fix: publish validation and dataset metadata only against the credential generation that performed the operation; treat the older completion as superseded. Preserve B's failed/untested health. Add deterministic interleavings for replacement and deletion during dataset creation. This produces false readiness and unusable selections; provider authorization is still enforced, and no authorization bypass is claimed.

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
from types import SimpleNamespace
from unittest.mock import Mock
from app.config import get_settings
from app.connectors.base import ConnectionHealth, ProvisionedDataset
from app.database import SessionLocal
from app.models import User, UserSecret

pytest_plugins = ["tests.conftest"]


def test_dataset_creation_overwrites_replacement_connection_health(
    access_app, make_user, monkeypatch
):
    from sqlalchemy import select

    import app.services.foundry_datasets as datasets
    from app.services.secrets import (
        decrypt_user_credentials_for_run,
        store_user_credentials,
        test_user_connection,
    )

    owner = make_user("dataset.race.audit@example.gov")
    settings = get_settings()
    old = {"endpoint": "https://old.demo.invalid", "token": "synthetic-old-token"}
    new = {"endpoint": "https://new.demo.invalid", "token": "synthetic-new-token"}
    with SessionLocal() as db:
        store_user_credentials(db, settings, user=owner, provider="mss", credentials=old)

    def interleaved_creation(credentials, *, parent_folder_rid, name):
        assert credentials["endpoint"] == old["endpoint"]
        with SessionLocal() as other:
            current_owner = other.get(User, owner.id)
            store_user_credentials(
                other, settings, user=current_owner, provider="mss", credentials=new
            )
            failed = Mock()
            failed.test_connection.return_value = ConnectionHealth(
                status="failed", message="Replacement endpoint failed", latency_ms=1
            )
            checked = test_user_connection(
                other,
                user=current_owner,
                provider="mss",
                settings=settings,
                connector_resolver=lambda _: failed,
            )
            assert checked.validation_status == "failed"
        return ProvisionedDataset(
            dataset_rid="ri.foundry.main.dataset.synthetic-created",
            name=name,
            parent_folder_rid=parent_folder_rid,
        )

    provisioner = SimpleNamespace(
        capabilities=SimpleNamespace(dataset_creation=True), create_dataset=interleaved_creation
    )
    monkeypatch.setattr(datasets, "dataset_provisioner_for", lambda _: provisioner)
    with SessionLocal() as db:
        datasets.create_foundry_dataset(
            db,
            settings,
            user=db.get(User, owner.id),
            provider="mss",
            parent_folder_rid="ri.compass.main.folder.synthetic",
            name="Audit dataset",
        )
    with SessionLocal() as db:
        stored = db.scalar(
            select(UserSecret).where(UserSecret.user_id == owner.id, UserSecret.provider == "mss")
        )
        current = decrypt_user_credentials_for_run(
            db, settings, user=db.get(User, owner.id), provider="mss"
        )
        assert current["endpoint"] == new["endpoint"]
        assert stored.validation_status == "connected"
        assert "successful dataset creation" in stored.validation_message
    print(
        "Existing #81: dataset creation on old credentials overwrote failed replacement health with connected"
    )
```

</details>
