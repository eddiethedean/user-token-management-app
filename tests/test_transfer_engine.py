"""Transfer-engine commit-boundary and reconciliation coverage."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock

import polars as pl
import pytest

from app.connectors.base import (
    BatchWriteResult,
    ColumnSchema,
    DestinationManifest,
    LoadSession,
    ObjectSchema,
    TransferBatch,
)
from app.connectors.errors import ConnectorError, TransferErrorCode
from app.connectors.locators import (
    DefinitionSnapshot,
    FoundryDatasetFilesLocator,
    PostgresAppendPolicy,
    PostgresUpsertPolicy,
    postgres_table,
)
from app.services import transfer_engine


class _Source:
    def test_connection(self, credentials):
        return None

    def inspect_object(self, credentials, locator):
        return ObjectSchema(locator=locator, columns=(ColumnSchema(name="id", data_type="Int64"),))

    def extract(self, credentials, locator, *, batch_rows, batch_bytes):
        frame = pl.DataFrame({"id": [1]})
        yield TransferBatch(frame=frame, row_count=1, byte_count=frame.estimated_size(), sequence=1)


class _Destination:
    def __init__(self) -> None:
        self.committed = False
        self.aborted = False

    def test_connection(self, credentials):
        return None

    def inspect_object(self, credentials, locator):
        return ObjectSchema(locator=locator, columns=(ColumnSchema(name="id", data_type="Int64"),))

    def count_rows(self, credentials, locator):
        return 1 if self.committed else 0

    def prepare_destination(self, credentials, locator, schema, write_policy, *, run_id):
        return LoadSession(locator=locator, write_policy=write_policy, columns=("id",))

    def write_batch(self, session, batch):
        return BatchWriteResult(
            rows_acknowledged=batch.row_count, bytes_acknowledged=batch.byte_count
        )

    def finalize(self, session):
        self.committed = True
        return DestinationManifest(locator=session.locator, rows=1, bytes=8)

    def abort(self, session):
        self.aborted = True


def test_failure_after_destination_commit_requires_reconciliation(monkeypatch) -> None:
    source = _Source()
    destination = _Destination()
    monkeypatch.setattr(
        transfer_engine,
        "connector_for",
        lambda provider: source if provider == "mss" else destination,
    )
    monkeypatch.setattr(transfer_engine, "route_allowed", lambda *_args: True)
    monkeypatch.setattr(transfer_engine, "writer_enabled", lambda provider: True)
    monkeypatch.setattr(transfer_engine.pipeline_runs, "heartbeat", lambda *args, **kwargs: None)
    monkeypatch.setattr(transfer_engine.pipeline_runs, "transition", lambda *args, **kwargs: None)
    monkeypatch.setattr(transfer_engine.pipeline_runs, "add_counters", lambda *args, **kwargs: None)
    monkeypatch.setattr(transfer_engine.pipeline_runs, "append_event", lambda *args, **kwargs: None)

    def fail_persistence(*args, **kwargs):
        raise RuntimeError("application database unavailable")

    monkeypatch.setattr(transfer_engine.pipeline_runs, "complete_run", fail_persistence)
    snapshot = DefinitionSnapshot(
        name="commit boundary",
        source_provider="mss",
        destination_provider="postgres",
        source=FoundryDatasetFilesLocator(
            dataset_rid="ri.foundry.main.dataset.example",
            branch="master",
            file_paths=["source.parquet"],
        ),
        destination=postgres_table("public", "events"),
        write_policy=PostgresAppendPolicy(),
    )
    settings = SimpleNamespace(
        is_demo_mode=False,
        app_env="test",
        pipeline_lease_seconds=120,
        pipeline_batch_rows=1_000,
        pipeline_batch_target_bytes=1_048_576,
        pipeline_max_run_seconds=60,
        pipeline_max_source_bytes=1_048_576,
    )

    with pytest.raises(ConnectorError) as excinfo:
        transfer_engine.execute_transfer(
            Mock(),
            run=SimpleNamespace(id="run-1"),
            lease_token="lease-1",
            snapshot=snapshot,
            source_credentials={},
            destination_credentials={},
            settings=settings,
            cancel_requested=lambda: False,
        )

    assert destination.committed is True
    assert destination.aborted is True
    assert excinfo.value.code == TransferErrorCode.PUBLISH_UNCERTAIN


def test_execution_rechecks_route_policy_before_connector_access(monkeypatch) -> None:
    connector_for = Mock(side_effect=AssertionError("connectors must not be opened"))
    monkeypatch.setattr(transfer_engine, "route_allowed", lambda *_args: False)
    monkeypatch.setattr(transfer_engine, "connector_for", connector_for)
    snapshot = DefinitionSnapshot(
        name="disabled route",
        source_provider="mss",
        destination_provider="postgres",
        source=FoundryDatasetFilesLocator(
            dataset_rid="ri.foundry.main.dataset.example",
            branch="master",
            file_paths=["source.parquet"],
        ),
        destination=postgres_table("public", "events"),
        write_policy=PostgresAppendPolicy(),
    )

    with pytest.raises(ConnectorError) as excinfo:
        transfer_engine.execute_transfer(
            Mock(),
            run=SimpleNamespace(id="run-disabled"),
            lease_token="lease-disabled",
            snapshot=snapshot,
            source_credentials={},
            destination_credentials={},
            settings=SimpleNamespace(is_demo_mode=False),
            cancel_requested=lambda: False,
        )

    assert excinfo.value.code == TransferErrorCode.PERMISSION_DENIED
    connector_for.assert_not_called()


def test_upsert_policy_requires_a_current_destination_key_and_source_columns() -> None:
    locator = postgres_table("public", "events")
    destination_schema = ObjectSchema(
        locator=locator,
        columns=(
            ColumnSchema(name="event_id", data_type="Int64"),
            ColumnSchema(name="unit_name", data_type="Utf8"),
        ),
        primary_key=("event_id",),
        unique_constraints=(("unit_name",),),
    )
    source_schema = ObjectSchema(locator=locator, columns=destination_schema.columns)
    snapshot = DefinitionSnapshot(
        name="unique-key upsert",
        source_provider="mss",
        destination_provider="postgres",
        source=FoundryDatasetFilesLocator(
            dataset_rid="ri.foundry.main.dataset.example",
            branch="master",
            file_paths=["source.parquet"],
        ),
        destination=locator,
        write_policy=PostgresUpsertPolicy(conflict_columns=["unit_name"]),
    )

    assert (
        transfer_engine._validate_upsert_policy(
            Mock(), {}, snapshot, source_schema, destination_schema
        )
        is destination_schema
    )

    stale_snapshot = snapshot.model_copy(
        update={"write_policy": PostgresUpsertPolicy(conflict_columns=["missing_key"])}
    )
    with pytest.raises(ConnectorError) as stale:
        transfer_engine._validate_upsert_policy(
            Mock(), {}, stale_snapshot, source_schema, destination_schema
        )
    assert stale.value.code == TransferErrorCode.SCHEMA_DRIFT

    incomplete_source = ObjectSchema(
        locator=locator,
        columns=(ColumnSchema(name="event_id", data_type="Int64"),),
    )
    with pytest.raises(ConnectorError) as missing:
        transfer_engine._validate_upsert_policy(
            Mock(), {}, snapshot, incomplete_source, destination_schema
        )
    assert missing.value.code == TransferErrorCode.SCHEMA_DRIFT
