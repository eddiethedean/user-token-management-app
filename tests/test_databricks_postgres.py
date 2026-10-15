"""Databricks durable coordination with PostgreSQL locks and concurrent sessions."""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier, local

import pytest
from sqlalchemy import create_engine, event, select, text
from sqlalchemy.engine import URL
from sqlalchemy.orm import sessionmaker

from app.config import Settings
from app.connectors.errors import ConnectorError, TransferErrorCode
from app.database import Base
from app.domain.locators import DatabricksTableLocator
from app.models import DatabricksComputeAttempt, DatabricksPublication, PipelineRun, User, utcnow
from app.services.databricks_compute import (
    claim_due_attempt,
    create_compute_attempt,
    observe_attempt,
)
from app.services.databricks_publication import record_publication_event
from tests.postgres_support import postgres_available

pytestmark = [pytest.mark.integration, pytest.mark.postgres]
CREDENTIALS = {
    "workspace_url": "https://dbc-postgres.cloud.databricks.com",
    "compute_kind": "warehouse",
    "compute_id": "shared-warehouse",
    "connection_generation": "revision-1",
}


def _settings() -> Settings:
    return Settings(_env_file=None)  # pyright: ignore[reportCallIssue]


@pytest.fixture()
def coordination_db(request):
    if not postgres_available() and os.environ.get("DATABRICKS_TEST_TOOLS_REQUIRED") == "1":
        pytest.fail("Databricks coordination checks require PostgreSQL server binaries on PATH.")
    postgresql = request.getfixturevalue("postgresql")
    dsn = postgresql.dsn()
    engine = create_engine(
        URL.create(
            "postgresql+psycopg",
            username=dsn["user"],
            host=dsn["host"],
            port=dsn["port"],
            database=dsn["database"],
        ),
        connect_args={"options": "-c statement_timeout=10000 -c lock_timeout=5000"},
    )
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    with sessions() as db:
        db.add(
            User(
                id="owner",
                email="dbx@example.gov",
                email_original="dbx@example.gov",
                status="active",
            )
        )
        db.commit()
    try:
        yield engine, sessions
    finally:
        engine.dispose()


def _attempt(sessions, compute_id: str) -> str:
    with sessions() as db:
        owner = db.get(User, "owner")
        assert owner is not None
        attempt, _ = create_compute_attempt(
            db,
            user=owner,
            credentials={**CREDENTIALS, "compute_id": compute_id},
            settings=_settings(),
        )
        attempt.next_poll_at = utcnow() - timedelta(seconds=1)
        db.commit()
        return attempt.id


def test_simultaneous_start_reservations_return_one_durable_attempt(coordination_db) -> None:
    engine, sessions = coordination_db
    both_read = Barrier(2)
    synchronized = local()

    # Force both real transactions past the initial read before either inserts.
    def synchronize_reads(conn, cursor, statement, parameters, context, executemany):
        if (
            statement.startswith("SELECT")
            and "WHERE databricks_compute_attempts.active_key =" in statement
            and not getattr(synchronized, "done", False)
        ):
            synchronized.done = True
            both_read.wait(timeout=5)

    event.listen(engine, "after_cursor_execute", synchronize_reads)

    def reserve():
        with sessions() as db:
            owner = db.get(User, "owner")
            assert owner is not None
            attempt, created = create_compute_attempt(
                db, user=owner, credentials=CREDENTIALS, settings=_settings()
            )
            db.commit()
            return attempt.id, created

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(reserve) for _ in range(2)]
            results = [future.result(timeout=15) for future in futures]
    finally:
        event.remove(engine, "after_cursor_execute", synchronize_reads)
    assert results[0][0] == results[1][0]
    assert sorted(created for _, created in results) == [False, True]
    with sessions() as db:
        assert len(db.scalars(select(DatabricksComputeAttempt)).all()) == 1


def test_poller_skips_locked_attempt_and_claims_other_work(coordination_db) -> None:
    _, sessions = coordination_db
    locked_id = _attempt(sessions, "locked-warehouse")
    available_id = _attempt(sessions, "available-warehouse")
    with sessions() as locker:
        locker.execute(
            select(DatabricksComputeAttempt)
            .where(DatabricksComputeAttempt.id == locked_id)
            .with_for_update()
        ).scalar_one()
        with ThreadPoolExecutor(max_workers=1) as pool:

            def claim():
                with sessions() as db:
                    return claim_due_attempt(db)

            claimed = pool.submit(claim).result(timeout=5)
        assert claimed is not None
        assert claimed[0] == available_id
        with sessions() as db:
            assert claim_due_attempt(db) is None
        locker.rollback()
    with sessions() as db:
        claimed = claim_due_attempt(db)
        assert claimed is not None
        assert claimed[0] == locked_id


def test_expired_poll_lease_cannot_overwrite_new_owner(coordination_db) -> None:
    _, sessions = coordination_db
    attempt_id = _attempt(sessions, "reclaimed-warehouse")
    now = utcnow()
    with sessions() as stale:
        first = claim_due_attempt(stale, now=now, lease_seconds=15)
        assert first is not None
        # Keep a cached ORM instance while a separate connection reclaims the lease.
        cached = stale.get(DatabricksComputeAttempt, attempt_id)
        assert cached is not None
        with sessions() as fresh:
            second = claim_due_attempt(fresh, now=now + timedelta(seconds=16))
            assert second is not None and second[0] == attempt_id
            assert second[1] != first[1]
        assert (
            observe_attempt(
                stale,
                attempt_id=attempt_id,
                lease_token=first[1],
                state="ERROR",
                settings=_settings(),
            )
            is False
        )
        stale.rollback()
    with sessions() as fresh:
        assert (
            observe_attempt(
                fresh,
                attempt_id=attempt_id,
                lease_token=second[1],
                state="RUNNING",
                settings=_settings(),
            )
            is True
        )
    with sessions() as db:
        saved = db.get(DatabricksComputeAttempt, attempt_id)
        assert saved is not None and saved.status == "ready"
        assert saved.poll_count == 1
        assert saved.active_key is None


def test_simultaneous_publications_fence_one_target_then_allow_retry(coordination_db) -> None:
    _, sessions = coordination_db
    locator = DatabricksTableLocator(
        workspace_id="a" * 64,
        catalog="main",
        schema="analytics",
        table="events",
        connection_generation="revision-1",
    )
    with sessions() as db:
        for run_id in ("writer-a", "writer-b"):
            db.add(
                PipelineRun(
                    id=run_id,
                    user_id="owner",
                    definition_snapshot_json="{}",
                    status="loading",
                    stage="transfer",
                    lease_token=run_id,
                    lease_expires_at=utcnow() + timedelta(minutes=5),
                )
            )
        db.commit()
    start = Barrier(2)

    def prepare(run_id: str):
        with sessions() as db:
            start.wait(timeout=5)
            try:
                record_publication_event(
                    db,
                    run_id=run_id,
                    lease_token=run_id,
                    user_id="owner",
                    locator=locator,
                    connection_generation="revision-1",
                    write_mode="append",
                    event="prepare_started",
                )
                return run_id, None
            except ConnectorError as exc:
                # A constraint loser must retain a usable outer transaction.
                assert db.scalar(text("SELECT 1")) == 1
                return run_id, exc.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(prepare, run_id) for run_id in ("writer-a", "writer-b")]
        outcomes = [future.result(timeout=15) for future in futures]
    winner = next(run_id for run_id, code in outcomes if code is None)
    loser = next(
        run_id for run_id, code in outcomes if code is TransferErrorCode.DESTINATION_CONFLICT
    )
    with sessions() as db:
        assert len(db.scalars(select(DatabricksPublication)).all()) == 1
        record_publication_event(
            db,
            run_id=winner,
            lease_token=winner,
            user_id="owner",
            locator=locator,
            connection_generation="revision-1",
            write_mode="append",
            event="abort_confirmed",
        )
        retried = record_publication_event(
            db,
            run_id=loser,
            lease_token=loser,
            user_id="owner",
            locator=locator,
            connection_generation="revision-1",
            write_mode="append",
            event="prepare_started",
        )
        assert retried.active_target_key is not None
