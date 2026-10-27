from __future__ import annotations

import json
import time
from datetime import UTC, date, datetime
from decimal import Decimal
from types import SimpleNamespace
from typing import Any, cast

import polars as pl
import pytest
from databricks.sdk.service import compute
from databricks.sql.parameters import (
    TimestampNTZParameter,  # pyright: ignore[reportPrivateImportUsage]
)

from app.config import Settings, get_settings
from app.connectors.base import AbortResult, TransferBatch
from app.connectors.csv_source import CsvSourceConnector
from app.connectors.databricks import (
    DatabricksConnector,
    _parse_cursor,
    _parse_namespace,
    _polars_dtype,
    _polars_type,
    _provider_error,
    _publication_target_marker,
    _quote,
    _sql_type,
    _tag_marker,
)
from app.connectors.errors import ConnectorError, TransferErrorCode
from app.connectors.locators import (
    CsvUploadLocator,
    DatabricksAppendPolicy,
    DatabricksCreatePolicy,
    DatabricksReplacePolicy,
    DatabricksTableLocator,
    PostgresTableLocator,
    databricks_workspace_id,
    postgres_table,
)
from app.connectors.postgres import PostgresConnector
from app.connectors.postgres import connect as connect_postgres
from app.domain.catalogs import ColumnSchema, ObjectSchema
from app.services.csv_uploads import inspect_csv
from tests.postgres_support import connector_settings

HOST = "dbc-12345678.cloud.databricks.com"
WORKSPACE_URL = f"https://{HOST}"
GENERATION = "2026-10-15T12:00:00.000000"


def _credentials() -> dict[str, str]:
    return {
        "workspace_url": WORKSPACE_URL,
        "token": "synthetic-api-token",
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
    def __init__(self, state: str = "STOPPED", *, serverless: bool = False) -> None:
        self.state = state
        self.starts = 0
        self.serverless = serverless
        self.query_history_pages: list[SimpleNamespace] = []
        self.query_history_calls: list[dict[str, Any]] = []
        self.clusters = SimpleNamespace()
        self.warehouses = SimpleNamespace(get=self.get_warehouse, start=self.start_warehouse)
        self.query_history = SimpleNamespace(list=self.list_query_history)

    def get_warehouse(self, *, id: str):
        assert id == "warehouse-1"
        return SimpleNamespace(state=self.state, enable_serverless_compute=self.serverless)

    def start_warehouse(self, *, id: str):
        assert id == "warehouse-1"
        self.starts += 1

    def list_query_history(self, **kwargs):
        self.query_history_calls.append(kwargs)
        if self.query_history_pages:
            return self.query_history_pages.pop(0)
        return SimpleNamespace(res=[], has_next_page=False, next_page_token=None)


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


def test_databricks_inspection_uses_full_decimal_declaration() -> None:
    harness = _SQLHarness(
        exists=True,
        columns=[("amount", "decimal", "DECIMAL(10,2)", "YES")],
    )
    connector, credentials = _sql_connector(harness)

    schema = connector.inspect_object(credentials, _table_locator())

    assert schema.columns[0].data_type == "Decimal(precision=10, scale=2)"


def test_databricks_timestamp_families_have_distinct_schemas_and_fingerprints() -> None:
    timestamp_harness = _SQLHarness(
        exists=True,
        columns=[("occurred", "timestamp", "TIMESTAMP", "YES")],
    )
    ntz_harness = _SQLHarness(
        exists=True,
        columns=[("occurred", "timestamp_ntz", "TIMESTAMP_NTZ", "YES")],
    )
    timestamp_connector, timestamp_credentials = _sql_connector(timestamp_harness)
    ntz_connector, ntz_credentials = _sql_connector(ntz_harness)

    timestamp_schema = timestamp_connector.inspect_object(timestamp_credentials, _table_locator())
    ntz_schema = ntz_connector.inspect_object(ntz_credentials, _table_locator())

    assert timestamp_schema.columns[0].data_type == "Datetime(time_unit='us', time_zone='UTC')"
    assert ntz_schema.columns[0].data_type == "Datetime(time_unit='us', time_zone=None)"
    assert timestamp_schema.schema_fingerprint != ntz_schema.schema_fingerprint


@pytest.mark.parametrize(
    ("native_type", "full_data_type", "value", "expected_type"),
    [
        (
            "timestamp",
            "TIMESTAMP",
            datetime(2026, 10, 27, 12, 0, tzinfo=UTC),
            "Datetime(time_unit='us', time_zone='UTC')",
        ),
        (
            "timestamp_ntz",
            "TIMESTAMP_NTZ",
            datetime(2026, 10, 27, 12, 0),
            "Datetime(time_unit='us', time_zone=None)",
        ),
    ],
    ids=["instant-timestamp", "wall-clock-timestamp"],
)
def test_databricks_extract_preserves_timestamp_timezone_semantics(
    native_type, full_data_type, value, expected_type
) -> None:
    harness = _SQLHarness(
        exists=True,
        columns=[("occurred", native_type, full_data_type, "YES")],
        extract_rows=[(value,)],
    )
    connector, credentials = _sql_connector(harness)

    batches = list(
        connector.extract(credentials, _table_locator(), batch_rows=10, batch_bytes=10_000)
    )

    assert len(batches) == 1
    column = batches[0].frame.get_column("occurred")
    assert column.dtype == _polars_dtype(expected_type)
    assert column.to_list() == [value]


@pytest.mark.parametrize(
    ("data_type", "expected_delta_type"),
    [
        ("Datetime(time_unit='us', time_zone='UTC')", "TIMESTAMP"),
        ("Datetime(time_unit='us', time_zone=None)", "TIMESTAMP_NTZ"),
    ],
    ids=["instant-timestamp", "wall-clock-timestamp"],
)
def test_databricks_writer_creates_matching_timestamp_delta_types(
    data_type, expected_delta_type
) -> None:
    harness = _SQLHarness()
    connector, credentials = _sql_connector(harness)
    credentials["_publication_event"] = lambda *_args, **_details: None
    schema = ObjectSchema(
        locator=_table_locator(),
        columns=(ColumnSchema("occurred", data_type),),
    )

    connector.prepare_destination(
        credentials,
        schema.locator,
        schema,
        DatabricksCreatePolicy(),
        run_id="11111111-1111-4111-8111-111111111111",
    )

    create_statement = next(
        statement
        for statement, _parameters in harness.statements
        if statement.startswith("CREATE TABLE `private`.`staging`")
    )
    assert f"`occurred` {expected_delta_type}" in create_statement


def test_databricks_writer_binds_ntz_values_as_ntz_parameters() -> None:
    harness = _SQLHarness()
    connector, credentials = _sql_connector(harness)
    credentials["_publication_event"] = lambda *_args, **_details: None
    schema = ObjectSchema(
        locator=_table_locator(),
        columns=(ColumnSchema("occurred", "Datetime(time_unit='us', time_zone=None)"),),
    )
    session = connector.prepare_destination(
        credentials,
        schema.locator,
        schema,
        DatabricksCreatePolicy(),
        run_id="33333333-3333-4333-8333-333333333333",
    )
    value = datetime(2026, 10, 27, 12, 0)
    frame = pl.DataFrame({"occurred": [value]}, schema={"occurred": pl.Datetime("us")})

    connector.write_batch(
        session,
        TransferBatch(frame=frame, row_count=1, byte_count=int(frame.estimated_size()), sequence=1),
    )

    parameters = next(
        parameters
        for statement, parameters in harness.statements
        if statement.startswith("INSERT INTO `private`.`staging`")
    )
    bound_value = parameters[0][0]
    assert isinstance(bound_value, TimestampNTZParameter)
    assert bound_value.value == value


@pytest.mark.parametrize(
    ("target_type", "output_type"),
    [
        ("timestamp_ntz", "Datetime(time_unit='us', time_zone='UTC')"),
        ("timestamp", "Datetime(time_unit='us', time_zone=None)"),
    ],
    ids=["ntz-target-vs-instant-output", "instant-target-vs-ntz-output"],
)
def test_databricks_append_rejects_timestamp_semantic_mismatch(target_type, output_type) -> None:
    target_full_type = "TIMESTAMP_NTZ" if target_type == "timestamp_ntz" else "TIMESTAMP"
    harness = _SQLHarness(
        exists=True,
        columns=[("occurred", target_type, target_full_type, "YES")],
    )
    connector, credentials = _sql_connector(harness)
    credentials["_publication_event"] = lambda *_args, **_details: None
    target = connector.inspect_object(credentials, _table_locator())
    output_schema = ObjectSchema(
        locator=target.locator,
        columns=(ColumnSchema("occurred", output_type),),
    )

    with pytest.raises(ConnectorError) as mismatch:
        connector.prepare_destination(
            credentials,
            target.locator,
            output_schema,
            DatabricksAppendPolicy(),
            run_id="22222222-2222-4222-8222-222222222222",
        )

    assert mismatch.value.code is TransferErrorCode.SCHEMA_DRIFT


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
            self.rows = (
                self.harness.extract_rows
                if self.harness.extract_rows is not None
                else [(1, "111-22-3333"), (2, "222-33-4444"), (3, "333-44-5555")]
            )
            self.description = [(str(column[0]),) for column in self.harness.columns]

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
        extract_rows: list[tuple[Any, ...]] | None = None,
    ) -> None:
        self.exists = exists
        self.paginate = paginate
        self.tags = tags or {}
        self.table_type = table_type
        self.data_format = data_format
        self.columns = columns or [
            ("id", "bigint", "BIGINT", "NO"),
            ("ssn", "string", "STRING", "YES"),
        ]
        self.object_id = object_id
        self.detail_format = detail_format
        self.history_marker = history_marker
        self.fail_on = fail_on
        self.extract_rows = extract_rows
        self.statements: list[tuple[str, Any]] = []
        self.connections: list[_SQLConnection] = []
        self.connection_options: list[dict[str, Any]] = []

    def connect(self, **kwargs) -> _SQLConnection:
        assert kwargs["server_hostname"] == HOST
        assert kwargs["access_token"] == "synthetic-api-token"
        assert kwargs["use_cloud_fetch"] is False
        assert kwargs["session_configuration"] == {"TIMEZONE": "UTC"}
        self.connection_options.append(kwargs)
        connection = _SQLConnection(self)
        self.connections.append(connection)
        return connection


def _sql_connector(
    harness: _SQLHarness,
    monkeypatch=None,
    *,
    startup_policy: str = "manual",
    metadata_verified: bool = True,
    serverless: bool = False,
    query_history_pages: list[SimpleNamespace] | None = None,
) -> tuple[DatabricksConnector, dict[str, Any]]:
    workspace = _ManagementWorkspace("RUNNING", serverless=serverless)
    workspace.query_history_pages = list(query_history_pages or [])
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
    connector, credentials = _sql_connector(harness)

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
    frame = pl.DataFrame({"id": [4, 5], "ssn": ["synthetic-4", "synthetic-5"]})
    if not exists:
        frame = frame.select("id")
    batch = TransferBatch(frame=frame, row_count=2, byte_count=16, sequence=1)
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


@pytest.mark.postgres
@pytest.mark.parametrize(
    ("policy", "exists"),
    [
        (DatabricksCreatePolicy(), False),
        (DatabricksAppendPolicy(), True),
        (DatabricksReplacePolicy(), True),
    ],
)
def test_postgres_source_types_publish_to_databricks(postgres_credentials, policy, exists) -> None:
    values = (
        7,
        "example",
        True,
        1.5,
        2.5,
        date(2026, 10, 21),
        datetime(2026, 10, 21, 12, 30),
        b"payload",
        Decimal("123.45"),
    )
    conn = connect_postgres(postgres_credentials, connector_settings())
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                "CREATE TABLE public.databricks_source ("
                "id INTEGER, label TEXT, ready BOOLEAN, score REAL, weight DOUBLE PRECISION, "
                "day DATE, occurred TIMESTAMP WITHOUT TIME ZONE, payload BYTEA, amount NUMERIC(10,2))"
            )
            cursor.execute(
                "INSERT INTO public.databricks_source VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                values,
            )
        conn.commit()
    finally:
        conn.close()

    source = PostgresConnector(connector_settings())
    source_locator = postgres_table("public", "databricks_source")
    schema = source.inspect_object(postgres_credentials, source_locator)
    harness = _SQLHarness(
        exists=exists,
        columns=[
            ("id", "int", "INT", "YES"),
            ("label", "string", "STRING", "YES"),
            ("ready", "boolean", "BOOLEAN", "YES"),
            ("score", "float", "FLOAT", "YES"),
            ("weight", "double", "DOUBLE", "YES"),
            ("day", "date", "DATE", "YES"),
            ("occurred", "timestamp_ntz", "TIMESTAMP_NTZ", "YES"),
            ("payload", "binary", "BINARY", "YES"),
            ("amount", "decimal", "DECIMAL(10,2)", "YES"),
        ],
    )
    destination, credentials = _sql_connector(harness)
    credentials["_publication_event"] = lambda *_args, **_kwargs: None
    session = destination.prepare_destination(
        credentials,
        _table_locator(),
        schema,
        policy,
        run_id="11111111-1111-4111-8111-111111111111",
    )
    for batch in source.extract(
        postgres_credentials, source_locator, batch_rows=100, batch_bytes=10_000
    ):
        destination.write_batch(session, batch)
    manifest = destination.finalize(session)

    declarations = next(
        statement
        for statement, _ in harness.statements
        if statement.startswith("CREATE TABLE `private`.`staging`")
    )
    assert (
        "`id` INT, `label` STRING, `ready` BOOLEAN, `score` FLOAT, `weight` DOUBLE" in declarations
    )
    assert (
        "`day` DATE, `occurred` TIMESTAMP_NTZ, `payload` BINARY, `amount` DECIMAL(10,2)"
        in declarations
    )
    written_rows = [
        row
        for statement, parameters in harness.statements
        if statement.startswith("INSERT INTO `private`.`staging`")
        for row in parameters
    ]
    assert written_rows == [values]
    assert manifest.rows == 1


def test_postgres_type_normalization_retains_databricks_append_schema_check() -> None:
    destination, credentials = _sql_connector(_SQLHarness(exists=True))
    credentials["_publication_event"] = lambda *_args, **_kwargs: None
    schema = ObjectSchema(
        locator=postgres_table("public", "source"),
        columns=(ColumnSchema("id", "integer"), ColumnSchema("ssn", "text")),
    )
    with pytest.raises(ConnectorError) as failure:
        destination.prepare_destination(
            credentials,
            _table_locator(),
            schema,
            DatabricksAppendPolicy(),
            run_id="11111111-1111-4111-8111-111111111111",
        )
    assert failure.value.code is TransferErrorCode.SCHEMA_DRIFT


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
    assert (
        connector.reconcile_publication(
            credentials,
            locator,
            submission_id="bad",
            submitted_at_ms=int(time.time() * 1000),
        )
        == "unknown"
    )
    assert _parse_cursor("17") == 17
    assert _parse_cursor(None) == 0


@pytest.mark.parametrize(
    "tags",
    ({}, {"column_tags": [("ssn", "class.tax_id", "true")]}),
)
def test_default_metadata_never_claims_verified_coverage(monkeypatch, tags) -> None:
    monkeypatch.delenv("PIPELINE_DATABRICKS_METADATA_VISIBILITY_VERIFIED", raising=False)
    monkeypatch.delenv("PIPELINE_ENABLE_DATABRICKS_WRITER", raising=False)
    settings = Settings(_env_file=None, pipeline_allowed_https_hosts=HOST)  # pyright: ignore[reportCallIssue]
    harness = _SQLHarness(exists=True, tags=tags)
    workspace = _ManagementWorkspace("RUNNING")
    connector = DatabricksConnector(
        settings,
        workspace_client_factory=lambda **_: workspace,
        sql_connect=harness.connect,
    )

    with pytest.raises(ConnectorError) as unverified:
        connector.inspect_object(_credentials(), _table_locator())
    assert unverified.value.code is TransferErrorCode.METADATA_INCOMPLETE

    verified_settings = settings.model_copy(
        update={"pipeline_databricks_metadata_visibility_verified": True}
    )
    verified_connector = DatabricksConnector(
        verified_settings,
        workspace_client_factory=lambda **_: workspace,
        sql_connect=harness.connect,
    )
    inspected = verified_connector.inspect_object(_credentials(), _table_locator())
    assert inspected.metadata_coverage == "verified"
    if tags:
        assert inspected.column_sensitivity_markers == (("ssn", ("databricks_classification",)),)


def test_incomplete_or_sensitive_metadata_blocks_schema_and_target(monkeypatch) -> None:
    hidden_metadata = _SQLHarness(exists=True)
    connector, credentials = _sql_connector(hidden_metadata, metadata_verified=False)
    with pytest.raises(ConnectorError) as missing_attestation:
        connector.inspect_object(credentials, _table_locator())
    assert missing_attestation.value.code is TransferErrorCode.METADATA_INCOMPLETE

    credentials["_publication_event"] = lambda _event, **_details: None
    with pytest.raises(ConnectorError) as unverified_target:
        connector.prepare_destination(
            credentials,
            _table_locator(),
            ObjectSchema(locator=_table_locator(), columns=(ColumnSchema("id", "Int64"),)),
            DatabricksReplacePolicy(),
            run_id="33333333-3333-4333-8333-333333333333",
        )
    assert unverified_target.value.code is TransferErrorCode.METADATA_INCOMPLETE
    assert not any(
        statement.startswith("CREATE TABLE") for statement, _ in hidden_metadata.statements
    )

    sensitive_tags = _SQLHarness(tags={"table_tags": [("classification", "sensitive")]})
    connector, credentials = _sql_connector(sensitive_tags)
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


def test_auto_start_state_race_is_rechecked_before_sql() -> None:
    harness = _SQLHarness()
    workspace = _ManagementWorkspace("STOPPED")
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


@pytest.mark.parametrize("source", [compute.ClusterSource.API, compute.ClusterSource.UI])
@pytest.mark.parametrize(
    "access_mode", [compute.DataSecurityMode.SINGLE_USER, compute.DataSecurityMode.USER_ISOLATION]
)
def test_classic_cluster_compatibility_uses_generated_sdk_enums(source, access_mode) -> None:
    details = compute.ClusterDetails(
        cluster_id="cluster-1",
        cluster_source=source,
        kind=compute.Kind.CLASSIC_PREVIEW,
        data_security_mode=access_mode,
        spark_version="14.3.x-scala2.12",
        state=compute.State.TERMINATED,
    )
    starts: list[str] = []
    workspace = SimpleNamespace(
        clusters=SimpleNamespace(
            get=lambda **_kwargs: details,
            start=lambda *, cluster_id: starts.append(cluster_id),
        )
    )
    connector = DatabricksConnector(_settings(), workspace_client_factory=lambda **_: workspace)
    credentials = {**_credentials(), "compute_kind": "classic_cluster", "compute_id": "cluster-1"}
    assert connector.test_connection(credentials).runtime_status == "TERMINATED"
    assert connector.start_compute(credentials)["outcome"] == "start_submitted"
    assert starts == ["cluster-1"]
    details.state = compute.State.RUNNING
    assert connector.test_connection(credentials).runtime_status == "RUNNING"
    assert connector.start_compute(credentials)["outcome"] == "already_running"
    assert starts == ["cluster-1"]


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
    connector, credentials = _sql_connector(harness, monkeypatch, serverless=True)
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
            credentials,
            locator,
            submission_id="77777777-7777-4777-8777-777777777777",
            submitted_at_ms=int(time.time() * 1000),
        )
        == "unknown"
    )

    marker_id = "88888888-8888-4888-8888-888888888888"
    target_marker = _publication_target_marker(locator)
    history_page = SimpleNamespace(
        res=[
            SimpleNamespace(
                status="FINISHED",
                query_tags=[
                    SimpleNamespace(key="data_mover_run_id", value=marker_id),
                    SimpleNamespace(key="data_mover_target", value=target_marker),
                ],
                query_text="INSERT INTO target",
            )
        ],
        has_next_page=False,
        next_page_token=None,
    )
    history = _SQLHarness()
    connector, credentials = _sql_connector(
        history, monkeypatch, serverless=True, query_history_pages=[history_page]
    )
    submitted_at_ms = int(time.time() * 1000)
    assert (
        connector.reconcile_publication(
            credentials,
            locator,
            submission_id=marker_id,
            submitted_at_ms=submitted_at_ms,
        )
        == "committed"
    )
    workspace = connector._workspace_client_factory(host=WORKSPACE_URL, token="test")
    call = workspace.query_history_calls[0]
    assert call["filter_by"].warehouse_ids == ["warehouse-1"]
    assert call["filter_by"].query_start_time_range.start_time_ms <= submitted_at_ms

    classic_history = _SQLHarness(history_marker=marker_id)
    classic_connector, classic_credentials = _sql_connector(classic_history, monkeypatch)
    assert (
        classic_connector.reconcile_publication(
            classic_credentials,
            locator,
            submission_id=marker_id,
            submitted_at_ms=submitted_at_ms,
        )
        == "committed"
    )
    assert any(
        statement.startswith("DESCRIBE HISTORY") for statement, _ in classic_history.statements
    )


def test_publication_uses_serverless_compatible_exact_query_marker(monkeypatch) -> None:
    harness = _SQLHarness()
    connector, credentials = _sql_connector(harness, monkeypatch, serverless=True)
    credentials["_publication_event"] = lambda _event, **_details: None
    locator = _table_locator()
    session = connector.prepare_destination(
        credentials,
        locator,
        ObjectSchema(locator=locator, columns=(ColumnSchema("id", "Int64"),)),
        DatabricksCreatePolicy(),
        run_id="99999999-9999-4999-8999-999999999998",
    )
    connector.finalize(session)

    publication_options = next(
        options for options in harness.connection_options if options.get("query_tags")
    )
    target_marker = _publication_target_marker(locator)
    assert publication_options["query_tags"] == {
        "data_mover_run_id": "99999999-9999-4999-8999-999999999998",
        "data_mover_target": target_marker,
    }
    publish_sql = next(
        statement
        for statement, _ in harness.statements
        if "CREATE TABLE `main`.`analytics`.`orders` USING DELTA" in statement
    )
    assert "datamover-run:99999999-9999-4999-8999-999999999998" in publish_sql
    assert not any(
        statement.startswith("SET spark.databricks.delta") for statement, _ in harness.statements
    )


def test_publication_keeps_delta_history_marker_for_classic_compute(monkeypatch) -> None:
    harness = _SQLHarness()
    connector, credentials = _sql_connector(harness, monkeypatch, serverless=False)
    credentials["_publication_event"] = lambda _event, **_details: None
    locator = _table_locator()
    session = connector.prepare_destination(
        credentials,
        locator,
        ObjectSchema(locator=locator, columns=(ColumnSchema("id", "Int64"),)),
        DatabricksCreatePolicy(),
        run_id="99999999-9999-4999-8999-999999999997",
    )
    connector.finalize(session)

    assert any(
        statement.startswith("SET spark.databricks.delta.commitInfo.userMetadata")
        for statement, _ in harness.statements
    )
    assert not any(options.get("query_tags") for options in harness.connection_options)


def test_publication_reconciliation_requires_success_and_exact_target(monkeypatch) -> None:
    locator = _table_locator()
    marker_id = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    target_marker = _publication_target_marker(locator)
    pages = [
        SimpleNamespace(
            res=[
                SimpleNamespace(
                    status="FAILED",
                    query_tags=[
                        SimpleNamespace(key="data_mover_run_id", value=marker_id),
                        SimpleNamespace(key="data_mover_target", value=target_marker),
                    ],
                )
            ],
            has_next_page=True,
            next_page_token="next-page",
        ),
        SimpleNamespace(
            res=[
                SimpleNamespace(
                    status="FINISHED",
                    query_tags=[
                        SimpleNamespace(key="data_mover_run_id", value=marker_id),
                        SimpleNamespace(key="data_mover_target", value="wrong-target"),
                    ],
                    query_text="INSERT INTO another_target",
                )
            ],
            has_next_page=False,
            next_page_token=None,
        ),
    ]
    connector, credentials = _sql_connector(
        _SQLHarness(), monkeypatch, serverless=True, query_history_pages=pages
    )
    assert (
        connector.reconcile_publication(
            credentials,
            locator,
            submission_id=marker_id,
            submitted_at_ms=int(time.time() * 1000),
        )
        == "unknown"
    )
    workspace = connector._workspace_client_factory(host=WORKSPACE_URL, token="test")
    assert [call["page_token"] for call in workspace.query_history_calls] == [None, "next-page"]


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
    assert _polars_type("TIMESTAMP") == "Datetime(time_unit='us', time_zone='UTC')"
    assert _polars_type("TIMESTAMP_NTZ") == "Datetime(time_unit='us', time_zone=None)"
    assert _sql_type("Date") == "DATE"
    assert _polars_dtype("Datetime(time_unit='us', time_zone=None)") == pl.Datetime("us")
    assert _polars_dtype("Datetime(time_unit='us', time_zone='UTC')") == pl.Datetime(
        "us", time_zone="UTC"
    )
    assert _sql_type("Datetime(time_unit='us', time_zone=None)") == "TIMESTAMP_NTZ"
    assert _sql_type("Datetime(time_unit='us', time_zone='UTC')") == "TIMESTAMP"
    assert _sql_type("timestamp without time zone") == "TIMESTAMP_NTZ"
    assert _sql_type("timestamp with time zone") == "TIMESTAMP"
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


def test_csv_datetime_schema_creates_databricks_staging_table(monkeypatch) -> None:
    content = b"occurred\n2026-10-22T12:30:45\n"
    inspection = inspect_csv("timestamps.csv", content)
    assert inspection.columns[0].inferred_type == "datetime"
    upload_locator = CsvUploadLocator(
        upload_id="11111111-1111-4111-8111-111111111111",
        checksum_sha256="a" * 64,
    )
    schema = CsvSourceConnector().inspect_object(
        {
            "content": content,
            "columns": json.dumps([column.name for column in inspection.columns]),
            "column_types": json.dumps([column.inferred_type for column in inspection.columns]),
            "column_timezones": json.dumps(
                [column.timezone_aware for column in inspection.columns]
            ),
        },
        upload_locator,
    )
    assert schema.columns[0].data_type == "Datetime(time_unit='us', time_zone=None)"

    harness = _SQLHarness(exists=False)
    connector, credentials = _sql_connector(harness, monkeypatch)
    credentials["_publication_event"] = lambda *_args, **_kwargs: None
    connector.prepare_destination(
        credentials,
        _table_locator(),
        schema,
        DatabricksCreatePolicy(),
        run_id="11111111-1111-4111-8111-111111111111",
    )

    statements = [statement for statement, _ in harness.statements]
    assert any(
        "CREATE TABLE `private`.`staging`" in statement and "`occurred` TIMESTAMP_NTZ" in statement
        for statement in statements
    )


def _prepare_two_column_load(monkeypatch, harness: _SQLHarness, policy=None):
    connector, credentials = _sql_connector(harness, monkeypatch)
    events: list[str] = []
    credentials["_publication_event"] = lambda event, **_details: events.append(event)
    locator = _table_locator()
    if harness.exists:
        locator = connector.inspect_object(credentials, locator).locator
    schema = ObjectSchema(
        locator=locator, columns=(ColumnSchema("id", "Int64"), ColumnSchema("ssn", "String"))
    )
    session = connector.prepare_destination(
        credentials,
        locator,
        schema,
        policy or DatabricksCreatePolicy(),
        run_id="bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
    )
    return connector, credentials, session, events


@pytest.mark.parametrize("reordered", [False, True], ids=["schema-order", "reordered-frame"])
def test_staging_batches_bind_values_by_column_name_and_split_large_payloads(
    monkeypatch, reordered: bool
) -> None:
    harness = _SQLHarness()
    connector, _credentials, session, events = _prepare_two_column_load(monkeypatch, harness)
    values = [None, "雪", "'); DROP TABLE orders; --", *[f"row-{i}" for i in range(498)]]
    frame = pl.DataFrame({"id": list(range(501)), "ssn": values})
    if reordered:
        frame = frame.select("ssn", "id")
    boundary = len(harness.statements)

    result = connector.write_batch(
        session,
        TransferBatch(
            frame=frame, row_count=frame.height, byte_count=int(frame.estimated_size()), sequence=1
        ),
    )

    inserts = harness.statements[boundary:]
    assert len(inserts) == 2
    assert [len(parameters) for _statement, parameters in inserts] == [500, 1]
    assert [row for _statement, parameters in inserts for row in parameters] == list(
        zip(range(501), values, strict=True)
    )
    assert all("(`id`, `ssn`) VALUES (?, ?)" in statement for statement, _ in inserts)
    assert all("DROP TABLE orders" not in statement for statement, _ in inserts)
    assert result.rows_acknowledged == 501
    assert result.bytes_acknowledged == frame.estimated_size()
    assert events.count("batch_written") == 1
    assert connector.abort(session) is AbortResult.ROLLED_BACK


@pytest.mark.parametrize("extra_column", [False, True], ids=["missing-column", "extra-column"])
def test_staging_rejects_batch_schema_changes_before_sending_rows(
    monkeypatch, extra_column: bool
) -> None:
    harness = _SQLHarness()
    connector, _credentials, session, events = _prepare_two_column_load(monkeypatch, harness)
    frame = (
        pl.DataFrame({"id": [1], "ssn": ["canary"], "extra": ["unexpected"]})
        if extra_column
        else pl.DataFrame({"id": [1]})
    )
    boundary = len(harness.statements)
    with pytest.raises(ConnectorError) as drift:
        connector.write_batch(
            session, TransferBatch(frame=frame, row_count=1, byte_count=8, sequence=1)
        )
    assert drift.value.code is TransferErrorCode.SCHEMA_DRIFT
    assert harness.statements[boundary:] == []
    assert "batch_written" not in events
    assert connector.abort(session) is AbortResult.ROLLED_BACK


@pytest.mark.parametrize("policy", [DatabricksAppendPolicy(), DatabricksReplacePolicy()])
@pytest.mark.parametrize(
    ("change", "expected"),
    [
        ("identity", TransferErrorCode.SCHEMA_DRIFT),
        ("columns", TransferErrorCode.SCHEMA_DRIFT),
        ("sensitivity", TransferErrorCode.SENSITIVE_DATA_GUARDRAIL_BLOCKED),
        ("deleted", TransferErrorCode.DESTINATION_NOT_FOUND),
    ],
)
def test_destination_changes_after_staging_prevent_publication(
    monkeypatch, policy, change: str, expected: TransferErrorCode
) -> None:
    harness = _SQLHarness(exists=True)
    connector, _credentials, session, events = _prepare_two_column_load(
        monkeypatch, harness, policy
    )
    connector.write_batch(
        session,
        TransferBatch(
            frame=pl.DataFrame({"id": [1], "ssn": ["synthetic"]}),
            row_count=1,
            byte_count=16,
            sequence=1,
        ),
    )
    if change == "identity":
        harness.object_id = "recreated-table"
    elif change == "columns":
        harness.columns = [*harness.columns, ("new_column", "string", "STRING", "YES")]
    elif change == "sensitivity":
        harness.tags["table_tags"] = [("classification", "sensitive")]
    else:
        harness.exists = False

    with pytest.raises(ConnectorError) as changed:
        connector.finalize(session)
    assert changed.value.code is expected
    assert "publish_submitted" not in events
    assert "committed" not in events
    assert connector.abort(session) is AbortResult.ROLLED_BACK
    assert events[-1] == "abort_confirmed"


def test_create_target_appearing_after_staging_is_not_overwritten(monkeypatch) -> None:
    harness = _SQLHarness()
    connector, _credentials, session, events = _prepare_two_column_load(monkeypatch, harness)
    harness.exists = True
    with pytest.raises(ConnectorError) as appeared:
        connector.finalize(session)
    assert appeared.value.code is TransferErrorCode.DESTINATION_CONFLICT
    assert "publish_submitted" not in events
    assert connector.abort(session) is AbortResult.ROLLED_BACK
