"""Transfer-engine commit-boundary and reconciliation coverage."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import replace
from types import SimpleNamespace
from typing import cast
from unittest.mock import Mock

import polars as pl
import pytest

from app.config import Settings
from app.connectors.base import (
    BatchWriteResult,
    ColumnSchema,
    ConnectionHealth,
    Credentials,
    DestinationManifest,
    DestinationWriter,
    LoadSession,
    ObjectSchema,
    ProviderCapabilities,
    SourceReader,
    TransferBatch,
)
from app.connectors.errors import ConnectorError, TransferErrorCode
from app.connectors.locators import (
    DefinitionSnapshot,
    FoundryDatasetFilesLocator,
    Locator,
    PostgresAppendPolicy,
    PostgresUpsertPolicy,
    WritePolicy,
    postgres_table,
)
from app.models import PipelineRun
from app.services import transfer_engine


def _settings(**values: object) -> Settings:
    return cast(Settings, SimpleNamespace(**values))


def _run(run_id: str) -> PipelineRun:
    return cast(PipelineRun, SimpleNamespace(id=run_id))


class _Source:
    capabilities = ProviderCapabilities(
        provider="mss",
        label="MSS",
        technology="test",
        mark="MSS",
        source=True,
        destination=False,
        object_model="file",
        write_modes=(),
        namespaces_label="Dataset",
        objects_label="File",
    )

    def test_connection(self, credentials: Credentials) -> ConnectionHealth:
        return ConnectionHealth(status="connected", message="ok", latency_ms=0)

    def inspect_object(self, credentials: Credentials, locator: Locator) -> ObjectSchema:
        return ObjectSchema(locator=locator, columns=(ColumnSchema(name="id", data_type="Int64"),))

    def extract(
        self,
        credentials: Credentials,
        locator: Locator,
        *,
        batch_rows: int,
        batch_bytes: int,
    ) -> Iterator[TransferBatch]:
        frame = pl.DataFrame({"id": [1]})
        yield TransferBatch(
            frame=frame,
            row_count=1,
            byte_count=int(frame.estimated_size()),
            sequence=1,
        )


class _Destination:
    capabilities = ProviderCapabilities(
        provider="postgres",
        label="PostgreSQL",
        technology="test",
        mark="PG",
        source=False,
        destination=True,
        object_model="table",
        write_modes=("append",),
        namespaces_label="Schema",
        objects_label="Table",
    )

    def __init__(self) -> None:
        self.committed = False
        self.aborted = False

    def test_connection(self, credentials: Credentials) -> ConnectionHealth:
        return ConnectionHealth(status="connected", message="ok", latency_ms=0)

    def inspect_object(self, credentials: Credentials, locator: Locator) -> ObjectSchema:
        return ObjectSchema(locator=locator, columns=(ColumnSchema(name="id", data_type="Int64"),))

    def count_rows(self, credentials: Credentials, locator: Locator) -> int:
        return 1 if self.committed else 0

    def prepare_destination(
        self,
        credentials: Credentials,
        locator: Locator,
        schema: ObjectSchema,
        write_policy: WritePolicy,
        *,
        run_id: str,
    ) -> LoadSession:
        return LoadSession(locator=locator, write_policy=write_policy, columns=("id",))

    def write_batch(self, load_session: LoadSession, batch: TransferBatch) -> BatchWriteResult:
        return BatchWriteResult(
            rows_acknowledged=batch.row_count, bytes_acknowledged=batch.byte_count
        )

    def finalize(self, load_session: LoadSession) -> DestinationManifest:
        self.committed = True
        return DestinationManifest(locator=load_session.locator, rows=1, bytes=8)

    def abort(self, load_session: LoadSession) -> None:
        self.aborted = True


class _CapabilityDisabledSource(_Source):
    capabilities = replace(_Source.capabilities, source=False)


class _CapabilityDisabledDestination(_Destination):
    capabilities = replace(_Destination.capabilities, destination=False)


class _WriteOnlyDestination(_Destination):
    capabilities = replace(
        _Destination.capabilities,
        schema_inspection=False,
        exact_row_counts=False,
    )


class _EmptySource(_Source):
    def inspect_object(self, credentials: Credentials, locator: Locator) -> ObjectSchema:
        return ObjectSchema(locator=locator, columns=())

    def extract(
        self,
        credentials: Credentials,
        locator: Locator,
        *,
        batch_rows: int,
        batch_bytes: int,
    ) -> Iterator[TransferBatch]:
        yield from ()


class _EmptyDestination(_Destination):
    def finalize(self, load_session: LoadSession) -> DestinationManifest:
        self.committed = True
        return DestinationManifest(locator=load_session.locator, rows=0, bytes=0)


def test_demo_stage_pause_uses_injected_sleeper() -> None:
    calls: list[float] = []
    settings = _settings(is_demo_mode=True, app_env="development")

    transfer_engine._demo_stage_pause(settings, sleeper=calls.append)

    assert calls == [0.7]


def test_failure_after_destination_commit_requires_reconciliation(monkeypatch) -> None:
    source = _Source()
    destination = _Destination()
    monkeypatch.setattr(transfer_engine, "route_allowed", lambda *_args: True)
    monkeypatch.setattr(transfer_engine, "writer_enabled", lambda provider, **kwargs: True)
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
    settings = _settings(
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
            run=_run("run-1"),
            lease_token="lease-1",
            snapshot=snapshot,
            source_credentials={},
            destination_credentials={},
            settings=settings,
            cancel_requested=lambda: False,
            source_resolver=lambda provider: source,
            destination_resolver=lambda provider: destination,
        )

    assert destination.committed is True
    assert destination.aborted is True
    assert excinfo.value.code == TransferErrorCode.PUBLISH_UNCERTAIN


def test_execution_rechecks_route_policy_before_connector_access(monkeypatch) -> None:
    monkeypatch.setattr(transfer_engine, "route_allowed", lambda *_args: False)
    source_resolver = Mock(side_effect=AssertionError("source resolver must not run"))
    destination_resolver = Mock(side_effect=AssertionError("destination resolver must not run"))
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
            run=_run("run-disabled"),
            lease_token="lease-disabled",
            snapshot=snapshot,
            source_credentials={},
            destination_credentials={},
            settings=_settings(is_demo_mode=False),
            cancel_requested=lambda: False,
            source_resolver=source_resolver,
            destination_resolver=destination_resolver,
        )

    assert excinfo.value.code == TransferErrorCode.PERMISSION_DENIED
    source_resolver.assert_not_called()
    destination_resolver.assert_not_called()


@pytest.mark.parametrize("is_demo_mode", [False, True])
def test_execution_binds_default_writer_policy_to_supplied_settings(
    monkeypatch, is_demo_mode: bool
) -> None:
    source = _Source()
    destination = _Destination()
    supplied_settings = _settings(is_demo_mode=is_demo_mode)
    observed_settings: list[object] = []

    def writer_policy(provider: str, *, settings=None) -> bool:
        observed_settings.append(settings)
        return False

    monkeypatch.setattr(transfer_engine, "route_allowed", lambda *_args: True)
    monkeypatch.setattr(transfer_engine, "writer_enabled", writer_policy)

    snapshot = DefinitionSnapshot(
        name="settings-bound writer policy",
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

    with pytest.raises(ConnectorError, match="not enabled"):
        transfer_engine.execute_transfer(
            Mock(),
            run=_run("run-settings-bound"),
            lease_token="lease-settings-bound",
            snapshot=snapshot,
            source_credentials={},
            destination_credentials={},
            settings=supplied_settings,
            cancel_requested=lambda: False,
            source_resolver=lambda provider: source,
            destination_resolver=lambda provider: destination,
        )

    assert len(observed_settings) == 1
    assert observed_settings[0] is supplied_settings


def test_execution_honors_injected_writer_policy_in_demo_mode(monkeypatch) -> None:
    source = _Source()
    destination = _Destination()
    writer_policy = Mock(return_value=False)
    state_operations = {
        name: Mock()
        for name in ("heartbeat", "transition", "add_counters", "append_event", "complete_run")
    }
    for name, operation in state_operations.items():
        monkeypatch.setattr(transfer_engine.pipeline_runs, name, operation)
    source_operations = {
        name: Mock(wraps=getattr(source, name))
        for name in ("test_connection", "inspect_object", "extract")
    }
    destination_operations = {
        name: Mock(wraps=getattr(destination, name))
        for name in (
            "test_connection",
            "inspect_object",
            "count_rows",
            "prepare_destination",
            "write_batch",
            "finalize",
            "abort",
        )
    }
    for name, operation in source_operations.items():
        setattr(source, name, operation)
    for name, operation in destination_operations.items():
        setattr(destination, name, operation)
    default_writer_policy = Mock(side_effect=AssertionError("default writer policy should not run"))
    monkeypatch.setattr(transfer_engine, "writer_enabled", default_writer_policy)
    db = Mock()
    snapshot = DefinitionSnapshot(
        name="injected demo policy",
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
    settings = _settings(
        is_demo_mode=True,
        app_env="test",
        pipeline_lease_seconds=120,
        pipeline_batch_rows=1_000,
        pipeline_batch_target_bytes=1_048_576,
        pipeline_max_run_seconds=60,
        pipeline_max_source_bytes=1_048_576,
    )

    with pytest.raises(ConnectorError, match="not enabled") as excinfo:
        transfer_engine.execute_transfer(
            db,
            run=_run("run-injected-demo-policy"),
            lease_token="lease-injected-demo-policy",
            snapshot=snapshot,
            source_credentials={},
            destination_credentials={},
            settings=settings,
            cancel_requested=lambda: False,
            source_resolver=lambda provider: source,
            destination_resolver=lambda provider: destination,
            route_policy=lambda source_provider, destination_provider: True,
            writer_policy=writer_policy,
        )

    assert excinfo.value.code == TransferErrorCode.PERMISSION_DENIED
    assert excinfo.value.retryable is False
    writer_policy.assert_called_once_with("postgres")
    default_writer_policy.assert_not_called()
    db.commit.assert_not_called()
    for operation in state_operations.values():
        operation.assert_not_called()
    for operation in (*source_operations.values(), *destination_operations.values()):
        operation.assert_not_called()
    assert destination.committed is False


@pytest.mark.parametrize(
    "invalid_role",
    (
        "source",
        "destination",
        "reversed_source",
        "reversed_destination",
        "capability_disabled_source",
        "capability_disabled_destination",
    ),
)
def test_execution_rejects_invalid_injected_roles(monkeypatch, invalid_role: str) -> None:
    monkeypatch.setattr(transfer_engine, "route_allowed", lambda *_args: True)
    heartbeat = Mock()
    monkeypatch.setattr(transfer_engine.pipeline_runs, "heartbeat", heartbeat)
    snapshot = DefinitionSnapshot(
        name="invalid injected role",
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

    source = (
        _CapabilityDisabledSource() if invalid_role == "capability_disabled_source" else _Source()
    )
    destination = (
        _CapabilityDisabledDestination()
        if invalid_role == "capability_disabled_destination"
        else _Destination()
    )
    source_resolver = cast(
        Callable[[str], SourceReader],
        (
            (lambda provider: object())
            if invalid_role == "source"
            else (lambda provider: destination)
            if invalid_role == "reversed_source"
            else (lambda provider: source)
        ),
    )
    destination_resolver = cast(
        Callable[[str], DestinationWriter],
        (
            (lambda provider: object())
            if invalid_role == "destination"
            else (lambda provider: source)
            if invalid_role == "reversed_destination"
            else (lambda provider: destination)
        ),
    )
    expected_message = (
        "destination writes"
        if invalid_role
        in {
            "destination",
            "reversed_destination",
            "capability_disabled_destination",
        }
        else "source extraction"
    )

    with pytest.raises(ConnectorError, match=expected_message) as excinfo:
        transfer_engine.execute_transfer(
            Mock(),
            run=_run("run-invalid-role"),
            lease_token="lease-invalid-role",
            snapshot=snapshot,
            source_credentials={},
            destination_credentials={},
            settings=_settings(is_demo_mode=False),
            cancel_requested=lambda: False,
            source_resolver=source_resolver,
            destination_resolver=destination_resolver,
        )

    assert excinfo.value.code == TransferErrorCode.INTERNAL_ERROR
    heartbeat.assert_not_called()


def test_write_only_destination_completes_without_optional_inspection(monkeypatch) -> None:
    source = _Source()
    destination = _WriteOnlyDestination()
    monkeypatch.setattr(transfer_engine, "route_allowed", lambda *_args: True)
    monkeypatch.setattr(
        transfer_engine,
        "writer_enabled",
        Mock(side_effect=AssertionError("default writer policy should not run")),
    )
    monkeypatch.setattr(transfer_engine.pipeline_runs, "heartbeat", lambda *args, **kwargs: None)
    monkeypatch.setattr(transfer_engine.pipeline_runs, "transition", lambda *args, **kwargs: None)
    monkeypatch.setattr(transfer_engine.pipeline_runs, "add_counters", lambda *args, **kwargs: None)
    monkeypatch.setattr(transfer_engine.pipeline_runs, "append_event", lambda *args, **kwargs: None)
    completed: dict = {}
    monkeypatch.setattr(
        transfer_engine.pipeline_runs,
        "complete_run",
        lambda *args, **kwargs: completed.update(kwargs),
    )
    snapshot = DefinitionSnapshot(
        name="write-only destination",
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

    transfer_engine.execute_transfer(
        Mock(),
        run=_run("run-write-only"),
        lease_token="lease-write-only",
        snapshot=snapshot,
        source_credentials={},
        destination_credentials={},
        settings=_settings(
            is_demo_mode=True,
            app_env="test",
            pipeline_lease_seconds=120,
            pipeline_batch_rows=1_000,
            pipeline_batch_target_bytes=1_048_576,
            pipeline_max_run_seconds=60,
            pipeline_max_source_bytes=1_048_576,
        ),
        cancel_requested=lambda: False,
        source_resolver=lambda provider: source,
        destination_resolver=lambda provider: destination,
        writer_policy=lambda provider: True,
    )

    assert destination.committed is True
    assert completed["destination_manifest"]["metadata"]["schema"]["provenance"] == "local_manifest"


def test_empty_source_schema_is_marked_unavailable(monkeypatch) -> None:
    source = _EmptySource()
    destination = _EmptyDestination()
    monkeypatch.setattr(transfer_engine, "route_allowed", lambda *_args: True)
    monkeypatch.setattr(transfer_engine, "writer_enabled", lambda provider, **kwargs: True)
    monkeypatch.setattr(transfer_engine.pipeline_runs, "heartbeat", lambda *args, **kwargs: None)
    monkeypatch.setattr(transfer_engine.pipeline_runs, "transition", lambda *args, **kwargs: None)
    monkeypatch.setattr(transfer_engine.pipeline_runs, "add_counters", lambda *args, **kwargs: None)
    monkeypatch.setattr(transfer_engine.pipeline_runs, "append_event", lambda *args, **kwargs: None)
    completed: dict = {}
    monkeypatch.setattr(
        transfer_engine.pipeline_runs,
        "complete_run",
        lambda *args, **kwargs: completed.update(kwargs),
    )
    snapshot = DefinitionSnapshot(
        name="empty source schema",
        source_provider="mss",
        destination_provider="postgres",
        source=FoundryDatasetFilesLocator(
            dataset_rid="ri.foundry.main.dataset.example",
            branch="master",
            file_paths=["empty.parquet"],
        ),
        destination=postgres_table("public", "events"),
        write_policy=PostgresAppendPolicy(),
    )
    settings = _settings(
        is_demo_mode=False,
        app_env="test",
        pipeline_lease_seconds=120,
        pipeline_batch_rows=1_000,
        pipeline_batch_target_bytes=1_048_576,
        pipeline_max_run_seconds=60,
        pipeline_max_source_bytes=1_048_576,
    )

    transfer_engine.execute_transfer(
        Mock(),
        run=_run("run-empty-schema"),
        lease_token="lease-empty-schema",
        snapshot=snapshot,
        source_credentials={},
        destination_credentials={},
        settings=settings,
        cancel_requested=lambda: False,
        source_resolver=lambda provider: source,
        destination_resolver=lambda provider: destination,
    )

    source_metadata = completed["source_manifest"]["metadata"]
    assert source_metadata["schema"] == {"available": False, "provenance": "unavailable"}


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


def test_destination_inspection_is_optional_except_for_upsert() -> None:
    destination = SimpleNamespace()
    locator = postgres_table("public", "events")

    assert transfer_engine._destination_row_count(destination, {}, locator) is None
    assert transfer_engine._inspect_destination_schema(destination, {}, locator) is None

    snapshot = DefinitionSnapshot(
        name="write-only upsert",
        source_provider="mss",
        destination_provider="postgres",
        source=FoundryDatasetFilesLocator(
            dataset_rid="ri.foundry.main.dataset.example",
            branch="master",
            file_paths=["source.parquet"],
        ),
        destination=locator,
        write_policy=PostgresUpsertPolicy(conflict_columns=["event_id"]),
    )
    source_schema = ObjectSchema(
        locator=locator,
        columns=(ColumnSchema(name="event_id", data_type="Int64"),),
    )
    with pytest.raises(ConnectorError, match="schema inspection") as excinfo:
        transfer_engine._validate_upsert_policy(
            destination, {}, snapshot, source_schema, destination_schema=None
        )
    assert excinfo.value.code == TransferErrorCode.SCHEMA_DRIFT


def test_destination_schema_and_row_count_capabilities_are_independent() -> None:
    locator = postgres_table("public", "events")
    schema = ObjectSchema(
        locator=locator,
        columns=(ColumnSchema(name="event_id", data_type="Int64"),),
        primary_key=("event_id",),
    )
    schema_only = SimpleNamespace(
        capabilities=SimpleNamespace(schema_inspection=True, exact_row_counts=False),
        inspect_object=lambda credentials, value: schema,
    )
    count_only = SimpleNamespace(
        capabilities=SimpleNamespace(schema_inspection=False, exact_row_counts=True),
        count_rows=lambda credentials, value: 7,
    )

    assert transfer_engine._inspect_destination_schema(schema_only, {}, locator) == schema
    assert transfer_engine._destination_row_count(schema_only, {}, locator) is None
    assert transfer_engine._inspect_destination_schema(count_only, {}, locator) is None
    assert transfer_engine._destination_row_count(count_only, {}, locator) == 7

    snapshot = DefinitionSnapshot(
        name="schema-only upsert",
        source_provider="mss",
        destination_provider="postgres",
        source=FoundryDatasetFilesLocator(
            dataset_rid="ri.foundry.main.dataset.example",
            branch="master",
            file_paths=["source.parquet"],
        ),
        destination=locator,
        write_policy=PostgresUpsertPolicy(conflict_columns=["event_id"]),
    )
    assert (
        transfer_engine._validate_upsert_policy(
            schema_only, {}, snapshot, schema, destination_schema=None
        )
        == schema
    )


def test_empty_destination_schema_is_marked_unavailable() -> None:
    locator = postgres_table("public", "empty")
    empty = ObjectSchema(locator=locator, columns=())

    selected, available, provenance = transfer_engine._destination_schema_projection(None, empty)

    assert selected == empty
    assert available is False
    assert provenance == "unavailable"
