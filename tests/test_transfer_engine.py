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
