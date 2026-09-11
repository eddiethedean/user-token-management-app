"""Contract and state-fidelity coverage for the offline connection emulator."""

from __future__ import annotations

import polars as pl
import pytest

from app.connectors.base import ColumnSchema, ObjectSchema, TransferBatch
from app.connectors.errors import ConnectorError, TransferErrorCode
from app.connectors.fake import FakeCsvConnector
from app.connectors.locators import (
    CsvUploadLocator,
    FoundryDatasetFilesLocator,
    FoundryReplaceFilePolicy,
    FoundryUploadLocator,
    PostgresAppendPolicy,
    PostgresUpsertPolicy,
    postgres_table,
)
from app.connectors.mcscop import McscopConnector
from app.connectors.mss import MssConnector
from app.connectors.registry import capabilities_for, connector_for, load_builtin_connectors
from app.services.demo import DEMO_CONNECTION_CREDENTIALS


def _batch(frame: pl.DataFrame, sequence: int = 1) -> TransferBatch:
    return TransferBatch(
        frame=frame,
        row_count=frame.height,
        byte_count=int(frame.estimated_size()),
        sequence=sequence,
    )


def _schema_for(frame: pl.DataFrame, locator) -> ObjectSchema:
    return ObjectSchema(
        locator=locator,
        columns=tuple(
            ColumnSchema(name=name, data_type=str(dtype), nullable=True)
            for name, dtype in frame.schema.items()
        ),
    )


def test_emulated_health_is_explicit_and_never_claims_network_latency() -> None:
    load_builtin_connectors(demo=True)
    postgres = connector_for("postgres")
    health = postgres.test_connection(DEMO_CONNECTION_CREDENTIALS["postgres"])
    assert health.status == "connected"
    assert health.latency_ms == 0
    assert "Emulated connection only" in health.message
    assert "no network request" in health.message
    assert "emulator" in health.server_identity

    mss = connector_for("mss")
    without_rid = dict(DEMO_CONNECTION_CREDENTIALS["mss"])
    without_rid.pop("dataset_rid")
    incomplete = mss.test_connection(without_rid)
    assert incomplete.status == "untested"
    assert incomplete.latency_ms == 0

    with pytest.raises(ConnectorError) as excinfo:
        postgres.test_connection({"host": "demo.invalid"})
    assert excinfo.value.code == TransferErrorCode.CREDENTIALS_MISSING


def test_emulated_foundry_capabilities_match_live_metadata_limits() -> None:
    load_builtin_connectors(demo=True)
    for provider, live in (("mss", MssConnector), ("mcscop", McscopConnector)):
        emulated = capabilities_for(provider)
        assert emulated.source is live.capabilities.source
        assert emulated.destination is live.capabilities.destination
        assert emulated.write_modes == live.capabilities.write_modes
        assert emulated.schema_inspection is live.capabilities.schema_inspection is False
        assert emulated.exact_row_counts is live.capabilities.exact_row_counts is False
        assert emulated.verification_level == live.capabilities.verification_level
        assert emulated.limitations == live.capabilities.limitations


def test_emulated_foundry_honors_branch_batching_and_missing_files() -> None:
    load_builtin_connectors(demo=True)
    credentials = {**DEMO_CONNECTION_CREDENTIALS["mss"], "branch": "release"}
    connector = connector_for("mss")
    namespaces = connector.list_namespaces(credentials)
    assert [item.name for item in namespaces] == [credentials["dataset_rid"]]
    objects = connector.list_objects(connector_credentials := credentials, namespaces[0].name)
    assert objects.items
    assert all(item.locator.branch == "release" for item in objects.items)

    source = FoundryDatasetFilesLocator(
        dataset_rid=credentials["dataset_rid"],
        branch="release",
        file_paths=["mission_orders.parquet"],
    )
    inspected = connector.inspect_object(connector_credentials, source)
    assert inspected.columns == ()
    assert inspected.estimated_rows is None
    assert connector.count_rows(connector_credentials, source) is None
    batches = list(
        connector.extract(connector_credentials, source, batch_rows=1, batch_bytes=1_024)
    )
    assert [batch.sequence for batch in batches] == [1, 2, 3]
    assert [batch.row_count for batch in batches] == [1, 1, 1]

    missing = source.model_copy(update={"file_paths": ["missing.parquet"]})
    with pytest.raises(ConnectorError) as excinfo:
        list(connector.extract(credentials, missing, batch_rows=10, batch_bytes=1_024))
    assert excinfo.value.code == TransferErrorCode.SOURCE_NOT_FOUND


def test_emulated_postgres_commits_persist_and_write_modes_are_realistic() -> None:
    load_builtin_connectors(demo=True)
    credentials = DEMO_CONNECTION_CREDENTIALS["postgres"]
    locator = postgres_table("public", "readiness_events")
    destination = connector_for("postgres")
    schema = destination.inspect_object(credentials, locator)
    assert destination.count_rows(credentials, locator) == 3
    assert schema.primary_key == ("event_id",)

    appended = pl.DataFrame(
        {
            "event_id": [4, 5],
            "unit_name": ["Unit-4", "Unit-5"],
            "ready": [True, False],
            "score": [88.0, 77.0],
        }
    )
    session = destination.prepare_destination(
        credentials, locator, schema, PostgresAppendPolicy(), run_id="append-run"
    )
    assert destination.write_batch(session, _batch(appended)).rows_acknowledged == 2
    assert destination.finalize(session).rows == 2
    assert connector_for("postgres").count_rows(credentials, locator) == 5

    upserted = pl.DataFrame(
        {
            "event_id": [1, 6],
            "unit_name": ["Ignored", "Unit-6"],
            "ready": [False, True],
            "score": [0.0, 66.0],
        }
    )
    upsert = PostgresUpsertPolicy(conflict_columns=["event_id"], action="ignore")
    session = destination.prepare_destination(
        credentials, locator, schema, upsert, run_id="upsert-run"
    )
    destination.write_batch(session, _batch(upserted))
    assert destination.finalize(session).rows == 1
    assert connector_for("postgres").count_rows(credentials, locator) == 6

    session = destination.prepare_destination(
        credentials, locator, schema, PostgresAppendPolicy(), run_id="abort-run"
    )
    destination.write_batch(session, _batch(appended))
    destination.abort(session)
    assert connector_for("postgres").count_rows(credentials, locator) == 6

    duplicate = pl.DataFrame(
        {
            "event_id": [6],
            "unit_name": ["Duplicate"],
            "ready": [True],
            "score": [1.0],
        }
    )
    session = destination.prepare_destination(
        credentials, locator, schema, PostgresAppendPolicy(), run_id="constraint-run"
    )
    destination.write_batch(session, _batch(duplicate))
    with pytest.raises(ConnectorError) as conflict:
        destination.finalize(session)
    assert conflict.value.code == TransferErrorCode.DESTINATION_CONFLICT
    assert connector_for("postgres").count_rows(credentials, locator) == 6

    isolated = {**credentials, "database": "another_demo_database"}
    assert connector_for("postgres").count_rows(isolated, locator) == 3


def test_emulated_foundry_upload_is_visible_to_later_connector_instances() -> None:
    load_builtin_connectors(demo=True)
    credentials = {**DEMO_CONNECTION_CREDENTIALS["mss"], "branch": "release"}
    locator = FoundryUploadLocator(
        dataset_rid=credentials["dataset_rid"],
        branch="release",
        file_name="emulated-output.parquet",
    )
    frame = pl.DataFrame({"event_id": [10, 11], "unit_name": ["A", "B"]})
    destination = connector_for("mss")
    session = destination.prepare_destination(
        credentials,
        locator,
        _schema_for(frame, locator),
        FoundryReplaceFilePolicy(),
        run_id="foundry-run",
    )
    destination.write_batch(session, _batch(frame))
    manifest = destination.finalize(session)
    assert manifest.rows == 2
    assert manifest.bytes > 0
    assert manifest.remote_id == "emulated-output.parquet"

    later = connector_for("mss")
    listed = later.list_objects(credentials, credentials["dataset_rid"])
    assert "emulated-output.parquet" in {item.name for item in listed.items}
    source = FoundryDatasetFilesLocator(
        dataset_rid=credentials["dataset_rid"],
        branch="release",
        file_paths=["emulated-output.parquet"],
    )
    extracted = list(later.extract(credentials, source, batch_rows=10, batch_bytes=4_096))
    assert sum(batch.row_count for batch in extracted) == 2


def test_emulated_csv_never_substitutes_synthetic_data() -> None:
    connector = FakeCsvConnector()
    locator = CsvUploadLocator(
        upload_id="11111111-1111-1111-1111-111111111111",
        checksum_sha256="a" * 64,
    )
    with pytest.raises(ConnectorError) as excinfo:
        connector.inspect_object({}, locator)
    assert excinfo.value.code == TransferErrorCode.SOURCE_NOT_FOUND


def test_emulated_destination_rejects_false_batch_acknowledgements() -> None:
    load_builtin_connectors(demo=True)
    credentials = DEMO_CONNECTION_CREDENTIALS["postgres"]
    locator = postgres_table("public", "mission_orders")
    connector = connector_for("postgres")
    schema = connector.inspect_object(credentials, locator)
    session = connector.prepare_destination(
        credentials, locator, schema, PostgresAppendPolicy(), run_id="bad-batch"
    )
    frame = pl.DataFrame(
        {"event_id": [4], "unit_name": ["Unit-4"], "ready": [True], "score": [4.0]}
    )
    dishonest = TransferBatch(frame=frame, row_count=2, byte_count=1, sequence=1)
    with pytest.raises(ConnectorError) as excinfo:
        connector.write_batch(session, dishonest)
    assert excinfo.value.code == TransferErrorCode.PARTIAL_WRITE
    connector.abort(session)
