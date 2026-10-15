from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast

import polars as pl
import pytest

from app.config import get_settings
from app.connectors.base import AbortResult, TransferBatch
from app.connectors.databricks import (
    DatabricksConnector,
    _parse_cursor,
    _parse_namespace,
    _polars_type,
    _provider_error,
    _quote,
    _sql_type,
    _tag_marker,
)
from app.connectors.errors import ConnectorError, TransferErrorCode
from app.connectors.locators import (
    DatabricksAppendPolicy,
    DatabricksCreatePolicy,
    DatabricksReplacePolicy,
    DatabricksTableLocator,
    PostgresTableLocator,
    databricks_workspace_id,
)
from app.domain.catalogs import ColumnSchema, ObjectSchema

HOST = "dbc-12345678.cloud.databricks.com"
WORKSPACE_URL = f"https://{HOST}"
GENERATION = "2026-10-15T12:00:00.000000"


def _credentials() -> dict[str, str]:
    return {
        "workspace_url": WORKSPACE_URL,
        "client_id": "client-id",
        "client_secret": "client-secret",
        "compute_kind": "warehouse",
        "compute_id": "warehouse-1",
        "http_path": "sql/1.0/warehouses/warehouse-1",
        "connection_generation": GENERATION,
    }


def _settings(**updates):
    return get_settings().model_copy(
        update={
            "pipeline_enable_databricks": True,
            "pipeline_allowed_https_hosts": HOST,
            **updates,
        }
    )


class _ManagementWorkspace:
    def __init__(self, state: str = "STOPPED") -> None:
        self.state = state
        self.starts = 0
        self.clusters = SimpleNamespace()
        self.warehouses = SimpleNamespace(get=self.get_warehouse, start=self.start_warehouse)

    def get_warehouse(self, *, id: str):
        assert id == "warehouse-1"
        return SimpleNamespace(state=self.state)

    def start_warehouse(self, *, id: str):
        assert id == "warehouse-1"
        self.starts += 1


def test_management_validation_is_read_only_and_start_is_explicit() -> None:
    workspace = _ManagementWorkspace()
    sql_calls = []
    connector = DatabricksConnector(
        _settings(),
        workspace_client_factory=lambda **_: workspace,
        sql_connect=lambda **kwargs: sql_calls.append(kwargs),
    )

    health = connector.test_connection(_credentials())
    assert health.status == "connected"
    assert health.runtime_status == "STOPPED"
    assert "SQL readiness: not ready" in health.message
    assert workspace.starts == 0
    assert sql_calls == []

    result = connector.start_compute(_credentials())
    assert result == {
        "state": "STOPPED",
        "outcome": "start_submitted",
        "kind": "warehouse",
    }
    assert workspace.starts == 1
    assert sql_calls == []


def test_sql_readiness_is_gated_by_live_startup_contract() -> None:
    workspace = _ManagementWorkspace("RUNNING")
    sql_calls = []
    connector = DatabricksConnector(
        _settings(pipeline_databricks_startup_contract_verified=False),
        workspace_client_factory=lambda **_: workspace,
        sql_connect=lambda **kwargs: sql_calls.append(kwargs),
    )

    with pytest.raises(ConnectorError) as failure:
        connector.list_namespaces(_credentials())

    assert failure.value.code is TransferErrorCode.COMPUTE_NOT_READY
    assert sql_calls == []


def test_direct_databricks_writer_requires_durable_worker_coordinator() -> None:
    sql_calls = []
    connector = DatabricksConnector(
        _settings(
            pipeline_enable_databricks_writer=True,
            pipeline_databricks_staging_schema="private.staging",
        ),
        workspace_client_factory=lambda **_: pytest.fail("unexpected management request"),
        sql_connect=lambda **kwargs: sql_calls.append(kwargs),
    )
    locator = DatabricksTableLocator(
        workspace_id=databricks_workspace_id(WORKSPACE_URL),
        catalog="main",
        schema="analytics",
        table="new_table",
        connection_generation=GENERATION,
    )
    schema = ObjectSchema(locator=locator, columns=(ColumnSchema("id", "Int64"),))

    with pytest.raises(ConnectorError) as failure:
        connector.prepare_destination(
            _credentials(),
            locator,
            schema,
            DatabricksCreatePolicy(),
            run_id="11111111-1111-4111-8111-111111111111",
        )

    assert failure.value.code is TransferErrorCode.PERMISSION_DENIED
    assert sql_calls == []


def test_identifiers_types_and_tags_fail_closed_without_retaining_tag_values() -> None:
    assert _quote("order`detail") == "`order``detail`"
    assert _polars_type("decimal(18, 2)") == "Decimal(precision=18, scale=2)"
    assert _sql_type("Decimal(precision=18, scale=2)") == "DECIMAL(18,2)"
    rules = [{"key": "classification", "value": "sensitive", "marker": "sensitive_data"}]
    assert _tag_marker("classification", "sensitive", rules) == "sensitive_data"
    assert _tag_marker("Classification", "sensitive", rules) is None
    assert _tag_marker("classification", "public", rules) is None
    assert _tag_marker("class.tax_id", "raw-sensitive-value", rules) == "databricks_classification"
    with pytest.raises(ConnectorError) as failure:
        _polars_type("array<string>")
    assert failure.value.code is TransferErrorCode.UNSUPPORTED_TYPE
    assert "raw-sensitive-value" not in str(failure.value)


class _SQLCursor:
    def __init__(self, harness: _SQLHarness) -> None:
        self.harness = harness
        self.rows: list[tuple[Any, ...]] = []
        self.description: list[tuple[str]] = []
        self.fetch_index = 0
        self.closed = False
        self.cancelled = False
        self.arraysize = 1

    def execute(self, statement: str, parameters=None) -> None:
        lowered = " ".join(statement.casefold().split())
        self.harness.statements.append((statement, parameters))
        self.fetch_index = 0
        self.description = []
        self.rows = []
        if self.harness.fail_on and self.harness.fail_on in lowered:
            raise RuntimeError("private provider response")
        if "select catalog_name, schema_name" in lowered:
            self.rows = [("main", "analytics")]
        elif "select table_name, table_type, data_source_format" in lowered:
            self.rows = [
                ("orders", "BASE TABLE", "DELTA", "2026-10-15")
                for _ in range(101 if self.harness.paginate else 1)
            ]
        elif "select table_type, data_source_format" in lowered:
            self.rows = (
                [(self.harness.table_type, self.harness.data_format)] if self.harness.exists else []
            )
        elif "select schema_name from" in lowered:
            self.rows = [("analytics",)]
        elif "information_schema.columns" in lowered:
            self.rows = self.harness.columns
        elif "describe detail" in lowered:
            self.description = [("id",), ("format",)]
            self.rows = [(self.harness.object_id, self.harness.detail_format)]
        elif "describe history" in lowered:
            self.description = [("version",), ("userMetadata",)]
            self.rows = [(1, self.harness.history_marker)]
        elif "information_schema." in lowered and "_tags" in lowered:
            self.rows = self.harness.tags.get(
                lowered.split("information_schema.", 1)[1].split()[0], []
            )
        elif "select count(*)" in lowered:
            self.rows = [(3,)]
        elif lowered.startswith("select * from"):
            self.rows = [(1, "111-22-3333"), (2, "222-33-4444"), (3, "333-44-5555")]
            self.description = [("id",), ("ssn",)]

    def fetchall(self):
        return self.rows

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchmany(self, size: int):
        result = self.rows[self.fetch_index : self.fetch_index + size]
        self.fetch_index += len(result)
        return result

    def executemany(self, statement: str, rows) -> None:
        self.harness.statements.append((statement, list(rows)))

    def cancel(self) -> None:
        self.cancelled = True

    def close(self) -> None:
        self.closed = True


class _SQLConnection:
    def __init__(self, harness: _SQLHarness) -> None:
        self.harness = harness
        self.cursor_instance: _SQLCursor | None = None
        self.closed = False

    def cursor(self) -> _SQLCursor:
        self.cursor_instance = _SQLCursor(self.harness)
        return self.cursor_instance

    def close(self) -> None:
        self.closed = True


class _SQLHarness:
    def __init__(
        self,
        *,
        exists: bool = False,
        paginate: bool = False,
        tags: dict[str, list[tuple[Any, ...]]] | None = None,
        table_type: str = "BASE TABLE",
        data_format: str = "DELTA",
        columns: list[tuple[Any, ...]] | None = None,
        object_id: str = "stable-table-id",
        detail_format: str = "delta",
        history_marker: str = "",
        fail_on: str = "",
    ) -> None:
        self.exists = exists
        self.paginate = paginate
        self.tags = tags or {}
        self.table_type = table_type
        self.data_format = data_format
        self.columns = columns or [("id", "bigint", "NO"), ("ssn", "string", "YES")]
        self.object_id = object_id
        self.detail_format = detail_format
        self.history_marker = history_marker
        self.fail_on = fail_on
        self.statements: list[tuple[str, Any]] = []
        self.connections: list[_SQLConnection] = []

    def connect(self, **kwargs) -> _SQLConnection:
        assert kwargs["server_hostname"] == HOST
        assert kwargs["use_cloud_fetch"] is False
        connection = _SQLConnection(self)
        self.connections.append(connection)
        return connection


def _sql_connector(
    harness: _SQLHarness,
    monkeypatch,
    *,
    startup_policy: str = "manual",
    metadata_verified: bool = True,
) -> tuple[DatabricksConnector, dict[str, Any]]:
    monkeypatch.setattr(
        "app.connectors.databricks.Config",
        lambda **values: SimpleNamespace(**values),
    )
    workspace = _ManagementWorkspace("RUNNING")
    settings = _settings(
        pipeline_enable_databricks_writer=True,
        pipeline_databricks_staging_schema="private.staging",
        pipeline_databricks_startup_contract_verified=True,
        pipeline_databricks_metadata_visibility_verified=metadata_verified,
        pipeline_databricks_sensitive_tag_rules='[{"key":"classification","value":"sensitive","marker":"pii"}]',
    )
    credentials: dict[str, Any] = {**_credentials(), "startup_policy": startup_policy}
    connector = DatabricksConnector(
        settings,
        workspace_client_factory=lambda **_: workspace,
        sql_connect=harness.connect,
    )
    return connector, credentials


def _table_locator(**updates) -> DatabricksTableLocator:
    values = {
        "workspace_id": databricks_workspace_id(WORKSPACE_URL),
        "catalog": "main",
        "schema": "analytics",
        "table": "orders",
        "connection_generation": GENERATION,
        **updates,
    }
    return DatabricksTableLocator(**values)


def test_catalog_discovery_inspection_count_and_bounded_extraction(monkeypatch) -> None:
    harness = _SQLHarness(paginate=True, exists=True)
    connector, credentials = _sql_connector(harness, monkeypatch)

    namespaces = connector.list_namespaces(credentials)
    assert len(namespaces) == 1
    workspace_id, catalog, schema_name = _parse_namespace(namespaces[0].name)
    assert (workspace_id, catalog, schema_name) == (
        databricks_workspace_id(WORKSPACE_URL),
        "main",
        "analytics",
    )

    first_page = connector.list_objects(credentials, namespaces[0].name)
    assert len(first_page.items) == 100
    assert first_page.cursor == "100"
    second_page = connector.list_objects(credentials, namespaces[0].name, first_page.cursor)
    assert len(second_page.items) == 100
    with pytest.raises(ConnectorError) as bad_cursor:
        connector.list_objects(credentials, namespaces[0].name, "-1")
    assert bad_cursor.value.code is TransferErrorCode.INTERNAL_ERROR

    locator = _table_locator()
    inspected = connector.inspect_object(credentials, locator)
    assert inspected.metadata_coverage == "verified"
    assert isinstance(inspected.locator, DatabricksTableLocator)
    assert inspected.locator.object_id == "stable-table-id"
    assert [column.data_type for column in inspected.columns] == ["Int64", "String"]
    assert connector.count_rows(credentials, locator) == 3

    batches = list(connector.extract(credentials, locator, batch_rows=2, batch_bytes=4096))
    assert [batch.row_count for batch in batches] == [2, 1]
    assert batches[0].frame.get_column("id").to_list() == [1, 2]
    assert all(connection.closed for connection in harness.connections)
    assert all(
        connection.cursor_instance is None or connection.cursor_instance.closed
        for connection in harness.connections
    )


@pytest.mark.parametrize(
    ("policy", "exists", "statement_part"),
    [
        (DatabricksCreatePolicy(), False, "CREATE TABLE `main`.`analytics`.`orders`"),
        (DatabricksAppendPolicy(), True, "INSERT INTO `main`.`analytics`.`orders`"),
        (DatabricksReplacePolicy(), True, "CREATE OR REPLACE TABLE `main`.`analytics`.`orders`"),
    ],
)
def test_staged_delta_publication_records_transformed_batches_and_cleans_up(
    policy, exists: bool, statement_part: str, monkeypatch
) -> None:
    harness = _SQLHarness(exists=exists)
    connector, credentials = _sql_connector(harness, monkeypatch)
    events: list[tuple[str, dict[str, Any]]] = []
    credentials["_publication_event"] = lambda event, **details: events.append((event, details))
    locator = _table_locator()
    schema = ObjectSchema(locator=locator, columns=(ColumnSchema("id", "Int64"),))
    if exists:
        target_info = connector.inspect_object(credentials, locator)
        locator = target_info.locator
        schema = ObjectSchema(
            locator=locator,
            columns=(ColumnSchema("id", "Int64"), ColumnSchema("ssn", "String")),
        )

    session = connector.prepare_destination(
        credentials,
        locator,
        schema,
        policy,
        run_id="11111111-1111-4111-8111-111111111111",
    )
    batch = TransferBatch(
        frame=pl.DataFrame({"id": [4, 5]}), row_count=2, byte_count=16, sequence=1
    )
    assert connector.write_batch(session, batch).rows_acknowledged == 2
    manifest = connector.finalize(session)

    statements = [statement for statement, _ in harness.statements]
    assert any(statement_part in statement for statement in statements)
    assert any("CREATE TABLE `private`.`staging`" in statement for statement in statements)
    assert any("DROP TABLE IF EXISTS `private`.`staging`" in statement for statement in statements)
    assert manifest.rows == 2
    assert manifest.details["staging_cleanup_pending"] == "false"
    assert [event for event, _ in events] == [
        "prepare_started",
        "staging_ready",
        "batch_written",
        "publish_submitted",
        "committed",
        "cleanup_confirmed",
    ]
    assert events[2][1]["rows"] == 2


def test_abort_and_recovery_helpers_fail_closed_for_unknown_sessions(monkeypatch) -> None:
    harness = _SQLHarness()
    connector, credentials = _sql_connector(harness, monkeypatch)
    locator = _table_locator()
    session = connector.prepare_destination(
        cast(
            dict[str, str],
            {**credentials, "_publication_event": lambda _event, **_details: None},
        ),
        locator,
        ObjectSchema(locator=locator, columns=(ColumnSchema("id", "Int64"),)),
        DatabricksCreatePolicy(),
        run_id="22222222-2222-4222-8222-222222222222",
    )
    assert connector.abort(session) is AbortResult.ROLLED_BACK
    assert connector.abort(session) is AbortResult.UNCERTAIN
    with pytest.raises(ConnectorError) as invalid_run:
        connector.cleanup_staging(credentials, run_id="bad")
    assert invalid_run.value.code is TransferErrorCode.INTERNAL_ERROR
    assert connector.reconcile_publication(credentials, locator, submission_id="bad") == "unknown"
    assert _parse_cursor("17") == 17
    assert _parse_cursor(None) == 0


def test_incomplete_or_sensitive_metadata_blocks_schema_and_target(monkeypatch) -> None:
    hidden_metadata = _SQLHarness(exists=True)
    connector, credentials = _sql_connector(hidden_metadata, monkeypatch, metadata_verified=False)
    with pytest.raises(ConnectorError) as missing_attestation:
        connector.inspect_object(credentials, _table_locator())
    assert missing_attestation.value.code is TransferErrorCode.METADATA_INCOMPLETE

    sensitive_tags = _SQLHarness(tags={"table_tags": [("classification", "sensitive")]})
    connector, credentials = _sql_connector(sensitive_tags, monkeypatch)
    credentials["_publication_event"] = lambda _event, **_details: None
    with pytest.raises(ConnectorError) as guarded:
        connector.prepare_destination(
            credentials,
            _table_locator(),
            ObjectSchema(locator=_table_locator(), columns=(ColumnSchema("id", "Int64"),)),
            DatabricksCreatePolicy(),
            run_id="33333333-3333-4333-8333-333333333333",
        )
    assert guarded.value.code is TransferErrorCode.SENSITIVE_DATA_GUARDRAIL_BLOCKED


def test_automatic_sql_start_requires_owner_callback_and_defers_retryably() -> None:
    workspace = _ManagementWorkspace("STOPPED")
    connector = DatabricksConnector(
        _settings(pipeline_databricks_startup_contract_verified=True),
        workspace_client_factory=lambda **_: workspace,
        sql_connect=lambda **_: pytest.fail("SQL must wait for observed compute readiness"),
    )
    credentials: dict[str, Any] = {**_credentials(), "startup_policy": "allow_start"}
    with pytest.raises(ConnectorError) as no_owner_context:
        connector.list_namespaces(credentials)
    assert no_owner_context.value.code is TransferErrorCode.PERMISSION_DENIED

    callbacks = []
    credentials["_startup_request"] = lambda state: (
        callbacks.append(state) or {"outcome": "start_submitted"}
    )
    with pytest.raises(ConnectorError) as waiting:
        connector.list_namespaces(credentials)
    assert waiting.value.code is TransferErrorCode.COMPUTE_NOT_READY
    assert waiting.value.retryable
    assert callbacks == ["STOPPED"]


def test_auto_start_state_race_is_rechecked_before_sql(monkeypatch) -> None:
    harness = _SQLHarness()
    workspace = _ManagementWorkspace("STOPPED")
    monkeypatch.setattr(
        "app.connectors.databricks.Config",
        lambda **values: SimpleNamespace(**values),
    )
    connector = DatabricksConnector(
        _settings(pipeline_databricks_startup_contract_verified=True),
        workspace_client_factory=lambda **_: workspace,
        sql_connect=harness.connect,
    )
    credentials: dict[str, Any] = {**_credentials(), "startup_policy": "allow_start"}
    callbacks = []

    def observed_starting(_state):
        callbacks.append(_state)
        workspace.state = "RUNNING"
        return {"outcome": "already_running"}

    credentials["_startup_request"] = observed_starting
    assert len(connector.list_namespaces(credentials)) == 1
    assert callbacks == ["STOPPED"]
    assert len(harness.connections) == 1


def test_workspace_binding_and_provider_error_classes_are_safe() -> None:
    class RateLimitError(Exception):
        status_code = 429

    mapped = _provider_error(RateLimitError("secret-provider-detail"), "listing")
    assert mapped.code is TransferErrorCode.RATE_LIMITED
    assert "secret-provider-detail" not in mapped.summary


def test_workspace_and_connector_identity_mismatches_are_rejected(monkeypatch) -> None:
    connector, credentials = _sql_connector(_SQLHarness(), monkeypatch)
    with pytest.raises(ConnectorError) as wrong_workspace:
        connector.inspect_object(credentials, _table_locator(workspace_id="a" * 64))
    assert wrong_workspace.value.code is TransferErrorCode.SCHEMA_DRIFT
    with pytest.raises(ConnectorError) as wrong_connector:
        connector.inspect_object(credentials, PostgresTableLocator(schema="public", table="users"))
    assert wrong_connector.value.code is TransferErrorCode.INTERNAL_ERROR


def test_classic_cluster_start_uses_cluster_api_and_rejects_incompatible_compute() -> None:
    class ClusterWorkspace:
        starts = 0

        def __init__(self, *, state: str = "TERMINATED", source: str = "UI") -> None:
            self.state = state
            self.source = source
            self.clusters = SimpleNamespace(get=self.get_cluster, start=self.start_cluster)
            self.warehouses = SimpleNamespace()

        def get_cluster(self, *, cluster_id: str):
            assert cluster_id == "cluster-1"
            return SimpleNamespace(
                cluster_source=self.source,
                kind="CLASSIC",
                data_security_mode="SINGLE_USER",
                spark_version="14.3.x-scala2.12",
                state=self.state,
            )

        def start_cluster(self, *, cluster_id: str):
            assert cluster_id == "cluster-1"
            self.starts += 1

    workspace = ClusterWorkspace()
    connector = DatabricksConnector(_settings(), workspace_client_factory=lambda **_: workspace)
    credentials = {
        **_credentials(),
        "compute_kind": "classic_cluster",
        "compute_id": "cluster-1",
        "http_path": "sql/protocolv1/o/dbc-12345678/cluster-1",
    }
    assert connector.compute_status(credentials)["state"] == "TERMINATED"
    assert connector.start_compute(credentials)["outcome"] == "start_submitted"
    assert workspace.starts == 1

    workspace.state = "STARTING"
    with pytest.raises(ConnectorError) as already_changing:
        connector.start_compute(credentials)
    assert already_changing.value.code is TransferErrorCode.COMPUTE_NOT_READY
    workspace.source = "JOB"
    with pytest.raises(ConnectorError) as incompatible:
        connector.compute_status(credentials)
    assert incompatible.value.code is TransferErrorCode.UNSUPPORTED_TYPE


@pytest.mark.parametrize(
    ("table_type", "data_format", "object_id", "expected"),
    [
        ("BASE TABLE", "PARQUET", "stable-table-id", TransferErrorCode.UNSUPPORTED_TYPE),
        ("VIEW", "DELTA", "stable-table-id", TransferErrorCode.UNSUPPORTED_TYPE),
        ("BASE TABLE", "DELTA", "", TransferErrorCode.METADATA_INCOMPLETE),
    ],
)
def test_object_inspection_rejects_unsupported_or_incomplete_tables(
    monkeypatch, table_type: str, data_format: str, object_id: str, expected
) -> None:
    harness = _SQLHarness(
        exists=True,
        table_type=table_type,
        data_format=data_format,
        object_id=object_id,
    )
    connector, credentials = _sql_connector(harness, monkeypatch)
    with pytest.raises(ConnectorError) as failure:
        connector.inspect_object(credentials, _table_locator())
    assert failure.value.code is expected


def test_sensitive_column_tags_are_projected_to_fixed_markers(monkeypatch) -> None:
    harness = _SQLHarness(
        exists=True,
        tags={"column_tags": [("ssn", "classification", "sensitive")]},
    )
    connector, credentials = _sql_connector(harness, monkeypatch)
    schema = connector.inspect_object(credentials, _table_locator())
    assert schema.column_sensitivity_markers == (("ssn", ("pii",)),)
    assert "sensitive" not in schema.sensitivity_fingerprint
    assert "classification" not in str(schema)
    assert "sensitive" not in str(schema)


def test_writer_rejects_missing_coordinator_conflicting_target_and_schema_drift(
    monkeypatch,
) -> None:
    locator = _table_locator()
    schema = ObjectSchema(locator=locator, columns=(ColumnSchema("id", "Int64"),))
    missing, credentials = _sql_connector(_SQLHarness(), monkeypatch)
    with pytest.raises(ConnectorError) as coordinator_required:
        missing.prepare_destination(
            credentials,
            locator,
            schema,
            DatabricksCreatePolicy(),
            run_id="44444444-4444-4444-8444-444444444444",
        )
    assert coordinator_required.value.code is TransferErrorCode.PERMISSION_DENIED

    conflict, credentials = _sql_connector(_SQLHarness(exists=True), monkeypatch)
    credentials["_publication_event"] = lambda _event, **_details: None
    with pytest.raises(ConnectorError) as target_exists:
        conflict.prepare_destination(
            credentials,
            locator,
            schema,
            DatabricksCreatePolicy(),
            run_id="55555555-5555-4555-8555-555555555555",
        )
    assert target_exists.value.code is TransferErrorCode.DESTINATION_CONFLICT

    mismatch, credentials = _sql_connector(_SQLHarness(exists=True), monkeypatch)
    credentials["_publication_event"] = lambda _event, **_details: None
    target = mismatch.inspect_object(credentials, locator).locator
    with pytest.raises(ConnectorError) as append_schema:
        mismatch.prepare_destination(
            credentials,
            target,
            schema,
            DatabricksAppendPolicy(),
            run_id="66666666-6666-4666-8666-666666666666",
        )
    assert append_schema.value.code is TransferErrorCode.SCHEMA_DRIFT


def test_publication_timeout_records_uncertain_result_and_history_reconciliation(
    monkeypatch,
) -> None:
    harness = _SQLHarness(fail_on="create table `main`")
    connector, credentials = _sql_connector(harness, monkeypatch)
    events: list[str] = []
    credentials["_publication_event"] = lambda event, **_details: events.append(event)
    locator = _table_locator()
    schema = ObjectSchema(locator=locator, columns=(ColumnSchema("id", "Int64"),))
    session = connector.prepare_destination(
        credentials,
        locator,
        schema,
        DatabricksCreatePolicy(),
        run_id="77777777-7777-4777-8777-777777777777",
    )
    with pytest.raises(ConnectorError) as uncertain:
        connector.finalize(session)
    assert uncertain.value.code is TransferErrorCode.PUBLISH_UNCERTAIN
    assert "publish_uncertain" in events
    assert connector.abort(session) is AbortResult.UNCERTAIN
    assert (
        connector.reconcile_publication(
            credentials, locator, submission_id="77777777-7777-4777-8777-777777777777"
        )
        == "unknown"
    )

    history = _SQLHarness(history_marker="88888888-8888-4888-8888-888888888888")
    connector, credentials = _sql_connector(history, monkeypatch)
    assert (
        connector.reconcile_publication(
            credentials,
            locator,
            submission_id="88888888-8888-4888-8888-888888888888",
        )
        == "committed"
    )


def test_staging_create_and_cleanup_failures_are_recorded_for_recovery(monkeypatch) -> None:
    locator = _table_locator()
    schema = ObjectSchema(locator=locator, columns=(ColumnSchema("id", "Int64"),))
    failed_create_harness = _SQLHarness(fail_on="create table `private`")
    failed_create, credentials = _sql_connector(failed_create_harness, monkeypatch)
    events: list[str] = []
    credentials["_publication_event"] = lambda event, **_details: events.append(event)
    with pytest.raises(ConnectorError):
        failed_create.prepare_destination(
            credentials,
            locator,
            schema,
            DatabricksCreatePolicy(),
            run_id="99999999-9999-4999-8999-999999999999",
        )
    assert events == ["prepare_started", "abort_uncertain"]

    cleanup_harness = _SQLHarness()
    connector, credentials = _sql_connector(cleanup_harness, monkeypatch)
    events.clear()
    credentials["_publication_event"] = lambda event, **_details: events.append(event)
    session = connector.prepare_destination(
        credentials,
        locator,
        schema,
        DatabricksCreatePolicy(),
        run_id="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
    )
    connector.write_batch(
        session,
        TransferBatch(frame=pl.DataFrame({"id": [1]}), row_count=1, byte_count=8, sequence=1),
    )
    cleanup_harness.fail_on = "drop table if exists"
    result = connector.finalize(session)
    assert result.details["staging_cleanup_pending"] == "true"
    assert events[-1] == "cleanup_pending"
    cleanup_harness.fail_on = ""
    connector.cleanup_staging(credentials, run_id="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
    assert any("DROP TABLE IF EXISTS" in statement for statement, _ in cleanup_harness.statements)


def test_provider_endpoint_type_and_query_failures_are_safe(monkeypatch) -> None:
    connector, credentials = _sql_connector(_SQLHarness(), monkeypatch)
    blocked = {**credentials, "workspace_url": "https://unapproved.example.net"}
    with pytest.raises(ConnectorError) as endpoint:
        connector.list_namespaces(blocked)
    assert endpoint.value.code is TransferErrorCode.ENDPOINT_BLOCKED

    failing, credentials = _sql_connector(
        _SQLHarness(exists=True, fail_on="select count(*)"), monkeypatch
    )
    with pytest.raises(ConnectorError) as row_count:
        failing.count_rows(credentials, _table_locator())
    assert row_count.value.code is TransferErrorCode.INTERNAL_ERROR
    assert "private provider response" not in row_count.value.summary

    source_harness = _SQLHarness(exists=True, fail_on="select * from")
    source_failure, credentials = _sql_connector(source_harness, monkeypatch)
    with pytest.raises(ConnectorError) as extraction:
        list(source_failure.extract(credentials, _table_locator(), batch_rows=2, batch_bytes=4096))
    assert extraction.value.code is TransferErrorCode.INTERNAL_ERROR
    failed_connection = source_harness.connections[-1]
    assert failed_connection.closed
    assert failed_connection.cursor_instance is not None
    assert failed_connection.cursor_instance.cancelled
    assert failed_connection.cursor_instance.closed


def test_provider_flag_and_manual_or_unsafe_compute_states_block_sql(monkeypatch) -> None:
    disabled = DatabricksConnector(
        _settings(pipeline_enable_databricks=False),
        workspace_client_factory=lambda **_: pytest.fail("disabled provider called the API"),
    )
    with pytest.raises(ConnectorError) as provider_off:
        disabled.test_connection(_credentials())
    assert provider_off.value.code is TransferErrorCode.PERMISSION_DENIED

    for state, startup_policy, expected in (
        ("STOPPED", "manual", "Start it explicitly"),
        ("ERROR", "allow_start", "not safe from this state"),
    ):
        workspace = _ManagementWorkspace(state)
        connector = DatabricksConnector(
            _settings(pipeline_databricks_startup_contract_verified=True),
            workspace_client_factory=lambda selected=workspace, **_: selected,
            sql_connect=lambda **_: pytest.fail("non-ready compute must not open SQL"),
        )
        with pytest.raises(ConnectorError) as not_ready:
            connector.list_namespaces({**_credentials(), "startup_policy": startup_policy})
        assert expected in not_ready.value.summary


def test_scalar_type_and_metadata_helpers_cover_invalid_values() -> None:
    assert _polars_type("varchar(100)") == "String"
    assert _sql_type("Date") == "DATE"
    assert _tag_marker("classification", "sensitive", []) is None
    with pytest.raises(ConnectorError) as invalid_decimal:
        _polars_type("decimal(2, 4)")
    assert invalid_decimal.value.code is TransferErrorCode.UNSUPPORTED_TYPE
    with pytest.raises(ConnectorError) as invalid_type:
        _sql_type("Unknown")
    assert invalid_type.value.code is TransferErrorCode.UNSUPPORTED_TYPE
    with pytest.raises(ConnectorError) as invalid_name:
        _quote("unsafe\nidentifier")
    assert invalid_name.value.code is TransferErrorCode.UNSUPPORTED_TYPE
