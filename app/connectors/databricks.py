"""Databricks Unity Catalog and Delta connector.

All Databricks-specific SQL, API-token authentication, compute, and identifier handling stays in
this adapter. The provider is omitted from the registry unless the operator
explicitly enables it. Its destination writer has an independent flag.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any
from urllib.parse import urlsplit

import polars as pl
from databricks import sql
from databricks.sdk import WorkspaceClient
from databricks.sdk.errors import NotFound
from databricks.sdk.service.sql import QueryFilter, TimeRange

from app.config import Settings
from app.connectors.base import (
    AbortResult,
    BatchWriteResult,
    ConnectionHealth,
    DestinationManifest,
    LoadSession,
    ObjectSchema,
    ProviderCapabilities,
    RemoteNamespace,
    RemoteObject,
    TransferBatch,
    bounded_frame_batches,
)
from app.connectors.errors import ConnectorError, TransferErrorCode
from app.connectors.locators import (
    DatabricksAppendPolicy,
    DatabricksCreatePolicy,
    DatabricksReplacePolicy,
    DatabricksTableLocator,
    Locator,
    WritePolicy,
    databricks_workspace_id,
    validate_locator,
)
from app.connectors.registry import connector_settings, register_connector
from app.domain.catalogs import CatalogPage, ColumnSchema

log = logging.getLogger(__name__)

_SAFE_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
_SAFE_MARKER = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_BATCH_INSERT_ROWS = 500
_MAX_CATALOG_ENTRIES = 10_000
_SQL_TYPES: dict[str, str] = {
    "string": "String",
    "varchar": "String",
    "char": "String",
    "boolean": "Boolean",
    "tinyint": "Int8",
    "byte": "Int8",
    "smallint": "Int16",
    "short": "Int16",
    "int": "Int32",
    "integer": "Int32",
    "long": "Int64",
    "bigint": "Int64",
    "float": "Float32",
    "real": "Float32",
    "double": "Float64",
    "date": "Date",
    "binary": "Binary",
    "timestamp": "Datetime(time_unit='us')",
    "timestamp_ntz": "Datetime(time_unit='us')",
}
_POLARS_TYPES: dict[str, Any] = {
    "String": pl.String,
    "Boolean": pl.Boolean,
    "Int8": pl.Int8,
    "Int16": pl.Int16,
    "Int32": pl.Int32,
    "Int64": pl.Int64,
    "Float32": pl.Float32,
    "Float64": pl.Float64,
    "Date": pl.Date,
    "Datetime(time_unit='us')": pl.Datetime("us"),
    "Binary": pl.Binary,
}


class DatabricksConnector:
    """First-class source and managed Delta destination connector."""

    capabilities = ProviderCapabilities(
        provider="databricks",
        label="Databricks",
        technology="Unity Catalog · Delta Lake",
        mark="DBX",
        source=True,
        destination=True,
        object_model="catalog → schema → Delta table",
        write_modes=("append", "replace", "create"),
        namespaces_label="Catalog · schema",
        objects_label="Delta table",
        writer_enabled=False,
        writer_setting="pipeline_enable_databricks_writer",
        schema_inspection=True,
        exact_row_counts=True,
        verification_level="exact",
        limitations=(
            "Unity Catalog base Delta tables and scalar types only.",
            "SQL use requires an approved compute startup policy.",
            "Destination writes require an operator-approved private staging schema.",
        ),
    )

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        workspace_client_factory: Callable[..., Any] = WorkspaceClient,
        sql_connect: Callable[..., Any] = sql.connect,
        clock: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self.settings = settings or connector_settings()
        self._workspace_client_factory = workspace_client_factory
        self._sql_connect = sql_connect
        self._clock = clock
        self._sleep = sleeper
        self._loads: dict[str, _LoadState] = {}

    def test_connection(self, credentials: Mapping[str, str]) -> ConnectionHealth:
        """Validate API-token authentication and compute visibility through management APIs only."""

        started = self._clock()
        try:
            workspace = self._workspace_client(credentials)
            state, compute_kind = self._compute_state(workspace, credentials)
        except ConnectorError:
            raise
        except Exception as exc:
            raise _provider_error(exc, "connection validation") from None
        sql_ready = state in {"RUNNING", "RESIZING"}
        return ConnectionHealth(
            status="connected",
            message=(
                f"API token authenticated; {compute_kind.replace('_', ' ')} state: {state}. "
                f"SQL readiness: {'ready' if sql_ready else 'not ready'}."
            ),
            latency_ms=max(0, int((self._clock() - started) * 1000)),
            server_identity=databricks_workspace_id(str(credentials["workspace_url"])),
            runtime_status=state,
        )

    def compute_status(self, credentials: Mapping[str, str]) -> dict[str, str]:
        """Return a safe snapshot; this method never submits SQL or starts compute."""

        try:
            state, compute_kind = self._compute_state(
                self._workspace_client(credentials), credentials
            )
        except ConnectorError:
            raise
        except Exception as exc:
            raise _provider_error(exc, "compute status check") from None
        return {
            "kind": compute_kind,
            "compute_id": str(credentials["compute_id"]),
            "state": state,
            "checked_at": datetime.now().astimezone().isoformat(),
        }

    def start_compute(self, credentials: Mapping[str, str]) -> dict[str, str]:
        """Submit one explicit start request for the selected terminated compute."""

        try:
            workspace = self._workspace_client(credentials)
            state, compute_kind = self._compute_state(workspace, credentials)
            if state == "RUNNING":
                return {"state": state, "outcome": "already_running", "kind": compute_kind}
            if state not in {"TERMINATED", "STOPPED"}:
                raise ConnectorError(
                    TransferErrorCode.COMPUTE_NOT_READY,
                    "Compute is already changing state. Check readiness again before starting it.",
                    retryable=True,
                )
            if compute_kind == "classic_cluster":
                workspace.clusters.start(cluster_id=str(credentials["compute_id"]))
            else:
                workspace.warehouses.start(id=str(credentials["compute_id"]))
            return {"state": state, "outcome": "start_submitted", "kind": compute_kind}
        except ConnectorError:
            raise
        except Exception as exc:
            raise _provider_error(exc, "compute start") from None

    def list_namespaces(self, credentials: Mapping[str, str]) -> list[RemoteNamespace]:
        """List accessible Unity Catalog catalog/schema pairs with a hard cap."""

        with self._sql_cursor(credentials) as cursor:
            cursor.execute(
                "SELECT catalog_name, schema_name "
                "FROM system.information_schema.schemata "
                "WHERE catalog_name <> 'system' "
                "ORDER BY catalog_name, schema_name LIMIT ?",
                [_MAX_CATALOG_ENTRIES + 1],
            )
            rows = cursor.fetchall()
        if len(rows) > _MAX_CATALOG_ENTRIES:
            raise ConnectorError(
                TransferErrorCode.SOURCE_LIMIT_EXCEEDED,
                "This workspace has too many visible schemas for one catalog request.",
                retryable=False,
            )
        return [
            RemoteNamespace(
                name=json.dumps(
                    [
                        databricks_workspace_id(str(credentials["workspace_url"])),
                        str(catalog),
                        str(schema),
                    ],
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
                display_name=f"{catalog} · {schema}",
                kind="databricks_schema",
            )
            for catalog, schema in rows
            if catalog and schema
        ]

    def list_objects(
        self,
        credentials: Mapping[str, str],
        namespace: str,
        cursor: str | None = None,
    ) -> CatalogPage:
        workspace_id, catalog, schema = _parse_namespace(namespace)
        if workspace_id != databricks_workspace_id(str(credentials["workspace_url"])):
            raise ConnectorError(
                TransferErrorCode.SCHEMA_DRIFT,
                "This Databricks schema belongs to a different workspace; select it again.",
                retryable=False,
            )
        offset = _parse_cursor(cursor)
        query = (
            f"SELECT table_name, table_type, data_source_format, last_altered "
            f"FROM {_quote(catalog)}.information_schema.tables "
            "WHERE table_schema = ? ORDER BY table_name LIMIT ? OFFSET ?"
        )
        with self._sql_cursor(credentials) as sql_cursor:
            sql_cursor.execute(query, [schema, 101, offset])
            rows = sql_cursor.fetchall()
        page_rows = rows[:100]
        items: list[RemoteObject] = []
        workspace_id = databricks_workspace_id(str(credentials["workspace_url"]))
        for table, object_type, data_format, last_altered in page_rows:
            if str(object_type or "").upper() not in {"BASE TABLE", "MANAGED", "EXTERNAL"}:
                continue
            if str(data_format or "").casefold() != "delta":
                continue
            locator = DatabricksTableLocator(
                workspace_id=workspace_id,
                catalog=str(catalog),
                schema=str(schema),
                table=str(table),
            )
            items.append(
                RemoteObject(
                    name=str(table),
                    display_name=str(table),
                    locator=locator,
                    updated_at=str(last_altered or ""),
                    format="Delta",
                )
            )
        next_offset = offset + 100
        next_cursor = str(next_offset) if len(rows) > 100 else None
        return CatalogPage(items=tuple(items), cursor=next_cursor)

    def inspect_object(self, credentials: Mapping[str, str], locator: Locator) -> ObjectSchema:
        selected = _databricks_locator(locator)
        _require_workspace_identity(credentials, selected)
        schema_rows = self._table_rows(credentials, selected)
        if not schema_rows:
            raise ConnectorError(
                TransferErrorCode.SOURCE_NOT_FOUND,
                "The selected Unity Catalog table is not visible to this connection.",
                retryable=False,
            )
        object_type, data_format = schema_rows[0]
        if str(object_type or "").upper() not in {"BASE TABLE", "MANAGED", "EXTERNAL"}:
            raise ConnectorError(
                TransferErrorCode.UNSUPPORTED_TYPE,
                "Only Unity Catalog base Delta tables are supported.",
                retryable=False,
            )
        if str(data_format or "").casefold() != "delta":
            raise ConnectorError(
                TransferErrorCode.UNSUPPORTED_TYPE,
                "Only Unity Catalog Delta tables are supported.",
                retryable=False,
            )
        quoted_table = _qualified(selected)
        try:
            with self._sql_cursor(credentials) as cursor:
                cursor.execute(
                    f"SELECT column_name, data_type, is_nullable "
                    f"FROM {_quote(selected.catalog)}.information_schema.columns "
                    "WHERE table_schema = ? AND table_name = ? ORDER BY ordinal_position",
                    [selected.schema_name, selected.table],
                )
                raw_columns = cursor.fetchall()
                cursor.execute(f"DESCRIBE DETAIL {quoted_table}")
                detail_rows = cursor.fetchall()
                detail_names = [item[0] for item in cursor.description or ()]
                tags = self._read_tags(cursor, selected)
        except ConnectorError:
            raise
        except Exception as exc:
            raise _provider_error(exc, "schema and sensitivity inspection") from None
        if not raw_columns or not detail_rows:
            raise _incomplete_metadata()
        detail = dict(zip(detail_names, detail_rows[0], strict=False))
        object_id = str(detail.get("id") or "")
        if not object_id or str(detail.get("format") or "").casefold() != "delta":
            raise _incomplete_metadata()
        columns = tuple(
            ColumnSchema(
                name=str(name),
                data_type=_polars_type(str(data_type)),
                nullable=str(is_nullable).upper() == "YES",
                sensitivity_markers=tuple(tags.column_markers.get(str(name), ())),
            )
            for name, data_type, is_nullable in raw_columns
        )
        selected = selected.model_copy(update={"object_id": object_id})
        schema_facts = [(column.name, column.data_type, column.nullable) for column in columns]
        sensitivity_facts = sorted(
            [("parent", marker) for marker in tags.parent_markers]
            + [
                (column, marker)
                for column, markers in tags.column_markers.items()
                for marker in markers
            ]
        )
        return ObjectSchema(
            locator=selected,
            columns=columns,
            sensitivity_markers=tuple(tags.parent_markers),
            column_sensitivity_markers=tuple(
                sorted((name, tuple(markers)) for name, markers in tags.column_markers.items())
            ),
            metadata_coverage="verified",
            schema_fingerprint=_fingerprint(schema_facts),
            sensitivity_fingerprint=_fingerprint(
                [self.settings.pipeline_databricks_metadata_policy_version, sensitivity_facts]
            ),
        )

    def count_rows(self, credentials: Mapping[str, str], locator: Locator) -> int:
        selected = _databricks_locator(locator)
        _require_workspace_identity(credentials, selected)
        self.inspect_object(credentials, selected)
        try:
            with self._sql_cursor(credentials) as cursor:
                cursor.execute(f"SELECT COUNT(*) FROM {_qualified(selected)}")
                row = cursor.fetchone()
        except Exception as exc:
            raise _provider_error(exc, "row count") from None
        return int(row[0]) if row else 0

    def extract(
        self,
        credentials: Mapping[str, str],
        locator: Locator,
        *,
        batch_rows: int,
        batch_bytes: int,
    ) -> Iterator[TransferBatch]:
        selected = _databricks_locator(locator)
        _require_workspace_identity(credentials, selected)
        schema = self.inspect_object(credentials, selected)
        selected_with_id = schema.locator
        statement = f"SELECT * FROM {_qualified(_databricks_locator(selected_with_id))}"
        sequence = 1
        connection = None
        sql_cursor = None
        try:
            connection = self._open_sql(credentials)
            sql_cursor = connection.cursor()
            sql_cursor.arraysize = min(max(1, batch_rows), 10_000)
            sql_cursor.execute(statement)
            frame_schema = [
                (column.name, _polars_dtype(column.data_type)) for column in schema.columns
            ]
            while True:
                rows = sql_cursor.fetchmany(min(max(1, batch_rows), 10_000))
                if not rows:
                    break
                frame = pl.DataFrame(rows, schema=frame_schema, orient="row", strict=True)
                for batch in bounded_frame_batches(
                    frame,
                    batch_rows=batch_rows,
                    batch_bytes=batch_bytes,
                    sequence_start=sequence,
                ):
                    sequence = batch.sequence + 1
                    yield batch
        except ConnectorError:
            raise
        except Exception as exc:
            raise _provider_error(exc, "source extraction") from None
        finally:
            if sql_cursor is not None:
                try:
                    sql_cursor.cancel()
                except Exception:
                    pass
                try:
                    sql_cursor.close()
                except Exception:
                    pass
            if connection is not None:
                try:
                    connection.close()
                except Exception:
                    pass

    def prepare_destination(
        self,
        credentials: Mapping[str, str],
        locator: Locator,
        schema: ObjectSchema,
        write_policy: WritePolicy,
        *,
        run_id: str,
    ) -> LoadSession:
        if not self.settings.pipeline_enable_databricks_writer:
            raise ConnectorError(
                TransferErrorCode.PERMISSION_DENIED,
                "The Databricks destination writer is not enabled by the operator.",
                retryable=False,
            )
        target = _databricks_locator(locator)
        _require_workspace_identity(credentials, target)
        if not re.fullmatch(r"[0-9a-fA-F-]{20,40}", run_id):
            raise ConnectorError(
                TransferErrorCode.INTERNAL_ERROR,
                "The transfer run identity is invalid.",
                retryable=False,
            )
        staging_catalog, staging_schema = _staging_schema(self.settings)
        table_name = f"data_mover_{run_id.replace('-', '')[:32]}"
        staging = f"{_quote(staging_catalog)}.{_quote(staging_schema)}.{_quote(table_name)}"
        _record_publication_event(
            credentials,
            "prepare_started",
            target=target,
            run_id=run_id,
            staging_name=f"{staging_catalog}.{staging_schema}.{table_name}",
            write_mode=write_policy.kind,
        )
        if not schema.columns:
            raise ConnectorError(
                TransferErrorCode.UNSUPPORTED_TYPE,
                "The transformed output schema has no supported columns.",
                retryable=False,
            )
        target_existed, target_schema = self._validate_target(
            credentials, target, schema, write_policy
        )
        declarations = ", ".join(
            f"{_quote(column.name)} {_sql_type(column.data_type)}" for column in schema.columns
        )
        try:
            with self._sql_cursor(credentials) as cursor:
                cursor.execute(f"CREATE TABLE {staging} ({declarations}) USING DELTA")
        except Exception as exc:
            safe_error = (
                exc
                if isinstance(exc, ConnectorError)
                else _provider_error(exc, "private staging creation", destination=True)
            )
            try:
                _record_publication_event(
                    credentials,
                    "abort_uncertain",
                    target=target,
                    run_id=run_id,
                    error=safe_error,
                )
            except Exception:
                pass
            raise _provider_error(exc, "private staging creation", destination=True) from None
        _record_publication_event(
            credentials,
            "staging_ready",
            target=target,
            run_id=run_id,
        )
        state = _LoadState(
            credentials=dict(credentials),
            locator=target,
            policy=write_policy,
            staging_name=staging,
            columns=tuple(column.name for column in schema.columns),
            rows=0,
            bytes=0,
            target_existed=target_existed,
            target_object_id=(
                target_schema.locator.object_id
                if target_schema is not None
                and isinstance(target_schema.locator, DatabricksTableLocator)
                else ""
            ),
            target_schema_fingerprint=(
                target_schema.schema_fingerprint if target_schema is not None else ""
            ),
        )
        self._loads[run_id] = state
        return LoadSession(
            locator=target,
            write_policy=write_policy,
            staging_name=table_name,
            columns=state.columns,
            metadata={"run_id": run_id},
        )

    def write_batch(self, load_session: LoadSession, batch: TransferBatch) -> BatchWriteResult:
        state = self._load_state(load_session)
        if set(batch.frame.columns) != set(state.columns):
            raise ConnectorError(
                TransferErrorCode.SCHEMA_DRIFT,
                "The Databricks staging batch columns no longer match the reviewed output schema.",
                retryable=False,
            )
        columns = ", ".join(_quote(name) for name in state.columns)
        placeholders = ", ".join("?" for _ in state.columns)
        statement = f"INSERT INTO {state.staging_name} ({columns}) VALUES ({placeholders})"
        rows = list(batch.frame.select(list(state.columns)).iter_rows())
        try:
            with self._sql_cursor(state.credentials) as cursor:
                for offset in range(0, len(rows), _BATCH_INSERT_ROWS):
                    cursor.executemany(statement, rows[offset : offset + _BATCH_INSERT_ROWS])
        except Exception as exc:
            raise _provider_error(exc, "staging batch write", destination=True) from None
        state.rows += batch.row_count
        state.bytes += batch.byte_count
        _record_publication_event(
            state.credentials,
            "batch_written",
            target=state.locator,
            run_id=str(load_session.metadata.get("run_id", "")),
            rows=state.rows,
            bytes_written=state.bytes,
        )
        return BatchWriteResult(
            rows_acknowledged=batch.row_count, bytes_acknowledged=batch.byte_count
        )

    def finalize(self, load_session: LoadSession) -> DestinationManifest:
        state = self._load_state(load_session)
        self._validate_target(
            state.credentials,
            state.locator,
            ObjectSchema(locator=state.locator, columns=tuple()),
            state.policy,
            expected_object_id=state.target_object_id,
            expected_schema_fingerprint=state.target_schema_fingerprint,
        )
        target = _qualified(state.locator)
        columns = ", ".join(_quote(name) for name in state.columns)
        operation_id = str(load_session.metadata.get("run_id", ""))
        if isinstance(state.policy, DatabricksCreatePolicy):
            statement = (
                f"CREATE TABLE {target} USING DELTA AS SELECT {columns} FROM {state.staging_name}"
            )
        elif isinstance(state.policy, DatabricksAppendPolicy):
            statement = (
                f"INSERT INTO {target} ({columns}) SELECT {columns} FROM {state.staging_name}"
            )
        elif isinstance(state.policy, DatabricksReplacePolicy):
            statement = (
                f"CREATE OR REPLACE TABLE {target} USING DELTA AS "
                f"SELECT {columns} FROM {state.staging_name}"
            )
        else:
            raise ConnectorError(
                TransferErrorCode.INTERNAL_ERROR,
                "The selected Databricks write policy is unsupported.",
                retryable=False,
            )
        submit_started = False
        target_marker = _publication_target_marker(state.locator)
        serverless = self._is_serverless_warehouse(state.credentials)
        try:
            with self._sql_cursor(
                state.credentials,
                query_tags=(
                    {
                        "data_mover_run_id": operation_id,
                        "data_mover_target": target_marker,
                    }
                    if serverless
                    else None
                ),
            ) as cursor:
                if not serverless:
                    cursor.execute(
                        "SET spark.databricks.delta.commitInfo.userMetadata = ?", [operation_id]
                    )
                _record_publication_event(
                    state.credentials,
                    "publish_submitted",
                    target=state.locator,
                    run_id=operation_id,
                )
                submit_started = True
                state.publish_submitted = True
                if serverless:
                    statement = (
                        f"/* datamover-run:{operation_id};target:{target_marker} */ {statement}"
                    )
                cursor.execute(statement)
        except Exception as exc:
            if submit_started:
                uncertain = ConnectorError(
                    TransferErrorCode.PUBLISH_UNCERTAIN,
                    "Databricks publication was submitted but its outcome could not be confirmed. Review the destination before retrying.",
                    retryable=False,
                )
                try:
                    _record_publication_event(
                        state.credentials,
                        "publish_uncertain",
                        target=state.locator,
                        run_id=operation_id,
                        error=uncertain,
                    )
                except Exception:
                    pass
                raise ConnectorError(
                    TransferErrorCode.PUBLISH_UNCERTAIN,
                    "Databricks publication was submitted but its outcome could not be confirmed. Review the destination before retrying.",
                    retryable=False,
                ) from None
            raise _provider_error(exc, "pre-publication validation", destination=True) from None
        _record_publication_event(
            state.credentials,
            "committed",
            target=state.locator,
            run_id=operation_id,
        )
        cleanup_pending = False
        try:
            with self._sql_cursor(state.credentials) as cursor:
                cursor.execute(f"DROP TABLE IF EXISTS {state.staging_name}")
        except Exception:
            cleanup_pending = True
        try:
            _record_publication_event(
                state.credentials,
                "cleanup_pending" if cleanup_pending else "cleanup_confirmed",
                target=state.locator,
                run_id=operation_id,
            )
        except Exception:
            cleanup_pending = True
        self._loads.pop(operation_id, None)
        return DestinationManifest(
            locator=state.locator,
            rows=state.rows,
            bytes=state.bytes,
            remote_id=operation_id,
            details={
                "write_mode": state.policy.kind.removeprefix("databricks_"),
                "staging_cleanup_pending": str(cleanup_pending).lower(),
            },
        )

    def _validate_target(
        self,
        credentials: Mapping[str, str],
        target: DatabricksTableLocator,
        output_schema: ObjectSchema,
        policy: WritePolicy,
        *,
        expected_object_id: str = "",
        expected_schema_fingerprint: str = "",
    ) -> tuple[bool, ObjectSchema | None]:
        """Verify target existence, identity, schema, and metadata fail-closed."""

        _require_workspace_identity(credentials, target)
        creating = isinstance(policy, DatabricksCreatePolicy)
        expected_object_id = expected_object_id or target.object_id
        expected_schema_fingerprint = expected_schema_fingerprint or target.schema_fingerprint
        table_rows = self._table_rows(credentials, target)
        if creating and table_rows:
            raise ConnectorError(
                TransferErrorCode.DESTINATION_CONFLICT,
                "A Databricks table with that name already exists. Choose another table name.",
                retryable=False,
            )
        if not creating and not table_rows:
            raise ConnectorError(
                TransferErrorCode.DESTINATION_NOT_FOUND,
                "The selected Databricks destination table is no longer available.",
                retryable=False,
            )
        try:
            with self._sql_cursor(credentials) as cursor:
                cursor.execute(
                    f"SELECT schema_name FROM {_quote(target.catalog)}.information_schema.schemata "
                    "WHERE catalog_name = ? AND schema_name = ?",
                    [target.catalog, target.schema_name],
                )
                schema_rows = cursor.fetchall()
                if len(schema_rows) != 1:
                    raise ConnectorError(
                        TransferErrorCode.DESTINATION_NOT_FOUND,
                        "The Databricks destination schema is not visible to this connection.",
                        retryable=False,
                    )
                tags = self._read_tags(cursor, target)
        except ConnectorError:
            raise
        except Exception as exc:
            raise _provider_error(exc, "destination policy inspection", destination=True) from None
        if tags.parent_markers:
            raise _sensitive_destination()
        if tags.column_markers:
            raise _sensitive_destination()
        if creating:
            return False, None
        current = self.inspect_object(credentials, target)
        if not isinstance(current.locator, DatabricksTableLocator):
            raise ConnectorError(
                TransferErrorCode.SCHEMA_DRIFT,
                "The Databricks destination identity could not be verified.",
                retryable=False,
            )
        if expected_object_id and current.locator.object_id != expected_object_id:
            raise ConnectorError(
                TransferErrorCode.SCHEMA_DRIFT,
                "The Databricks destination table identity changed after review.",
                retryable=False,
            )
        if (
            expected_schema_fingerprint
            and current.schema_fingerprint != expected_schema_fingerprint
        ):
            raise ConnectorError(
                TransferErrorCode.SCHEMA_DRIFT,
                "The Databricks destination schema or sensitivity policy changed after review.",
                retryable=False,
            )
        if (
            target.sensitivity_fingerprint
            and current.sensitivity_fingerprint != target.sensitivity_fingerprint
        ):
            raise ConnectorError(
                TransferErrorCode.SCHEMA_DRIFT,
                "The Databricks destination sensitivity policy changed after review.",
                retryable=False,
            )
        if isinstance(policy, DatabricksAppendPolicy) and output_schema.columns:
            current_columns = tuple((item.name, item.data_type) for item in current.columns)
            output_columns = tuple((item.name, item.data_type) for item in output_schema.columns)
            if current_columns != output_columns:
                raise ConnectorError(
                    TransferErrorCode.SCHEMA_DRIFT,
                    "Append requires the transformed columns and types to match the current Databricks target.",
                    retryable=False,
                )
        return True, current

    def abort(self, load_session: LoadSession) -> AbortResult:
        run_id = str(load_session.metadata.get("run_id", ""))
        state = self._loads.get(run_id)
        if state is None:
            return AbortResult.UNCERTAIN
        if state.publish_submitted:
            error = ConnectorError(
                TransferErrorCode.PUBLISH_UNCERTAIN,
                "Databricks publication was already submitted; its outcome requires reconciliation.",
                retryable=False,
            )
            try:
                _record_publication_event(
                    state.credentials,
                    "publish_uncertain",
                    target=state.locator,
                    run_id=run_id,
                    error=error,
                )
            except Exception:
                pass
            return AbortResult.UNCERTAIN
        try:
            with self._sql_cursor(state.credentials) as cursor:
                cursor.execute(f"DROP TABLE IF EXISTS {state.staging_name}")
        except Exception as exc:
            safe_error = (
                exc
                if isinstance(exc, ConnectorError)
                else _provider_error(exc, "staging cleanup", destination=True)
            )
            try:
                _record_publication_event(
                    state.credentials,
                    "abort_uncertain",
                    target=state.locator,
                    run_id=run_id,
                    error=safe_error,
                )
            except Exception:
                pass
            return AbortResult.UNCERTAIN
        _record_publication_event(
            state.credentials,
            "abort_confirmed",
            target=state.locator,
            run_id=run_id,
        )
        self._loads.pop(run_id, None)
        return AbortResult.ROLLED_BACK

    def cleanup_staging(self, credentials: Mapping[str, str], *, run_id: str) -> None:
        """Idempotently drop only the deterministic staging table for a run."""

        if not re.fullmatch(r"[0-9a-fA-F-]{20,40}", run_id):
            raise ConnectorError(
                TransferErrorCode.INTERNAL_ERROR,
                "The Databricks staging run identity is invalid.",
                retryable=False,
            )
        catalog, schema = _staging_schema(self.settings)
        table = f"data_mover_{run_id.replace('-', '')[:32]}"
        staging = f"{_quote(catalog)}.{_quote(schema)}.{_quote(table)}"
        with self._sql_cursor(credentials) as cursor:
            cursor.execute(f"DROP TABLE IF EXISTS {staging}")

    def reconcile_publication(
        self,
        credentials: Mapping[str, str],
        locator: Locator,
        *,
        submission_id: str,
        submitted_at_ms: int,
    ) -> str:
        """Confirm publication from the exact provider marker for its compute family."""

        target = _databricks_locator(locator)
        _require_workspace_identity(credentials, target)
        if not re.fullmatch(r"[0-9a-fA-F-]{20,40}", submission_id) or submitted_at_ms <= 0:
            return "unknown"
        now_ms = int(time.time() * 1000)
        lookback_start_ms = submitted_at_ms - 60_000
        if lookback_start_ms < now_ms - 30 * 24 * 60 * 60 * 1000:
            return "unknown"
        if self._is_serverless_warehouse(credentials):
            target_marker = _publication_target_marker(target)
            workspace = self._workspace_client(credentials)
            page_token = None
            try:
                for _ in range(100):
                    page = workspace.query_history.list(
                        filter_by=QueryFilter(
                            warehouse_ids=[str(credentials["compute_id"])],
                            query_start_time_range=TimeRange(
                                start_time_ms=lookback_start_ms,
                                end_time_ms=now_ms,
                            ),
                        ),
                        max_results=999,
                        page_token=page_token,
                    )
                    for query in page.res or ():
                        if _sdk_value(getattr(query, "status", None), "UNKNOWN").upper() != (
                            "FINISHED"
                        ):
                            continue
                        tags = getattr(query, "query_tags", None) or ()
                        tag_values = {
                            str(getattr(tag, "key", "")): str(getattr(tag, "value", ""))
                            for tag in tags
                        }
                        query_text = str(getattr(query, "query_text", "") or "")
                        exact_tags = (
                            tag_values.get("data_mover_run_id") == submission_id
                            and tag_values.get("data_mover_target") == target_marker
                        )
                        exact_comment = (
                            f"datamover-run:{submission_id};target:{target_marker}" in query_text
                        )
                        if exact_tags or exact_comment:
                            return "committed"
                    if not page.has_next_page or not page.next_page_token:
                        return "unknown"
                    page_token = page.next_page_token
            except Exception:
                return "unknown"
            return "unknown"

        try:
            with self._sql_cursor(credentials) as cursor:
                cursor.execute(f"DESCRIBE HISTORY {_qualified(target)} LIMIT 1000")
                names = [str(item[0]).casefold() for item in cursor.description or ()]
                rows = cursor.fetchall()
        except ConnectorError:
            raise
        except Exception:
            return "unknown"
        metadata_index = next(
            (index for index, name in enumerate(names) if name == "usermetadata"), None
        )
        if metadata_index is None:
            return "unknown"
        return (
            "committed"
            if any(str(row[metadata_index] or "") == submission_id for row in rows)
            else "unknown"
        )

    @contextmanager
    def _sql_cursor(
        self,
        credentials: Mapping[str, str],
        *,
        query_tags: Mapping[str, str] | None = None,
    ):
        connection = None
        cursor = None
        try:
            connection = self._open_sql(credentials, query_tags=query_tags)
            cursor = connection.cursor()
            yield cursor
        except ConnectorError:
            raise
        except Exception as exc:
            raise _provider_error(exc, "SQL operation") from None
        finally:
            if cursor is not None:
                try:
                    cursor.close()
                except Exception:
                    pass
            if connection is not None:
                try:
                    connection.close()
                except Exception:
                    pass

    def _open_sql(
        self,
        credentials: Mapping[str, str],
        *,
        query_tags: Mapping[str, str] | None = None,
    ) -> Any:
        self._require_sql_ready(credentials)
        workspace_url = str(credentials["workspace_url"])
        try:
            options: dict[str, Any] = {
                "server_hostname": urlsplit(workspace_url).hostname,
                "http_path": str(credentials["http_path"]),
                "access_token": str(credentials["token"]),
                "use_cloud_fetch": False,
                "use_inline_params": False,
                "_use_arrow_native_complex_types": False,
                "user_agent_entry": "DataMover",
            }
            if query_tags:
                options["query_tags"] = dict(query_tags)
            return self._sql_connect(**options)
        except Exception as exc:
            raise _provider_error(exc, "SQL connection") from None

    def _workspace_client(self, credentials: Mapping[str, str]) -> Any:
        if not self.settings.pipeline_enable_databricks:
            raise ConnectorError(
                TransferErrorCode.PERMISSION_DENIED,
                "The Databricks provider is disabled by the operator.",
                retryable=False,
            )
        workspace_url = str(credentials["workspace_url"])
        parsed = urlsplit(workspace_url)
        host = (parsed.hostname or "").casefold().rstrip(".")
        try:
            port = parsed.port
        except ValueError:
            port = 0
        if (
            parsed.scheme != "https"
            or not host
            or host not in self.settings.allowed_https_hosts
            or parsed.username
            or parsed.password
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
            or port not in {None, 443}
        ):
            raise ConnectorError(
                TransferErrorCode.ENDPOINT_BLOCKED,
                "The saved Databricks workspace is not approved by the current operator policy.",
                retryable=False,
            )
        return self._workspace_client_factory(
            host=workspace_url,
            token=str(credentials["token"]),
            auth_type="pat",
            product="DataMover",
        )

    def _is_serverless_warehouse(self, credentials: Mapping[str, str]) -> bool:
        if str(credentials.get("compute_kind", "")) != "warehouse":
            return False
        try:
            details = self._workspace_client(credentials).warehouses.get(
                id=str(credentials["compute_id"])
            )
        except ConnectorError:
            raise
        except Exception as exc:
            raise _provider_error(exc, "warehouse compute-mode check") from None
        serverless = getattr(details, "enable_serverless_compute", None)
        if not isinstance(serverless, bool):
            raise ConnectorError(
                TransferErrorCode.UNSUPPORTED_TYPE,
                "The selected Databricks warehouse does not report whether it uses serverless compute.",
                retryable=False,
            )
        return serverless

    @staticmethod
    def _compute_state(workspace: Any, credentials: Mapping[str, str]) -> tuple[str, str]:
        compute_kind = str(credentials["compute_kind"])
        compute_id = str(credentials["compute_id"])
        if compute_kind == "classic_cluster":
            details = workspace.clusters.get(cluster_id=compute_id)
            source = _sdk_value(getattr(details, "cluster_source", None)).upper()
            kind = _sdk_value(getattr(details, "kind", None)).upper()
            access_mode = _sdk_value(getattr(details, "data_security_mode", None)).upper()
            runtime = str(getattr(details, "spark_version", "") or "")
            if source not in {"UI", "API"} or kind not in {"CLASSIC", "CLASSIC_PREVIEW", ""}:
                raise ConnectorError(
                    TransferErrorCode.UNSUPPORTED_TYPE,
                    "Select an existing all-purpose classic cluster that is compatible with Unity Catalog.",
                    retryable=False,
                )
            version = re.match(r"^(\d+)\.(\d+)", runtime)
            runtime_supported = bool(
                version and (int(version.group(1)), int(version.group(2))) >= (13, 3)
            )
            if access_mode not in {"USER_ISOLATION", "SINGLE_USER"} or not runtime_supported:
                raise ConnectorError(
                    TransferErrorCode.UNSUPPORTED_TYPE,
                    "Choose a Unity Catalog-compatible classic cluster using standard or single-user access mode and Databricks Runtime 13.3 LTS or later.",
                    retryable=False,
                )
            return _sdk_value(getattr(details, "state", None), "UNKNOWN").upper(), compute_kind
        if compute_kind == "warehouse":
            details = workspace.warehouses.get(id=compute_id)
            return _sdk_value(getattr(details, "state", None), "UNKNOWN").upper(), compute_kind
        raise ConnectorError(
            TransferErrorCode.UNSUPPORTED_TYPE,
            "Choose an existing SQL warehouse or all-purpose classic cluster.",
            retryable=False,
        )

    def _require_sql_ready(self, credentials: Mapping[str, str]) -> None:
        authorization_check = credentials.get("_authorization_check")
        if callable(authorization_check):
            authorization_check()
        if not self.settings.pipeline_databricks_startup_contract_verified:
            raise ConnectorError(
                TransferErrorCode.COMPUTE_NOT_READY,
                "Databricks SQL startup behavior has not passed isolated acceptance; SQL-backed actions are disabled.",
                retryable=False,
            )
        try:
            state, compute_kind = self._compute_state(
                self._workspace_client(credentials), credentials
            )
        except ConnectorError:
            raise
        except Exception as exc:
            raise _provider_error(exc, "compute readiness check") from None
        if state in {"RUNNING", "RESIZING"}:
            return
        if str(credentials.get("startup_policy", "manual")) != "allow_start":
            raise ConnectorError(
                TransferErrorCode.COMPUTE_NOT_READY,
                f"The selected Databricks {compute_kind.replace('_', ' ')} is {state}. Start it explicitly, then retry.",
                retryable=False,
            )
        if state not in {
            "TERMINATED",
            "STOPPED",
            "PENDING",
            "RESTARTING",
            "STARTING",
        }:
            raise ConnectorError(
                TransferErrorCode.COMPUTE_NOT_READY,
                f"The selected Databricks {compute_kind.replace('_', ' ')} is {state}; automatic startup is not safe from this state.",
                retryable=False,
            )
        startup_request = credentials.get("_startup_request")
        if not callable(startup_request):
            raise ConnectorError(
                TransferErrorCode.PERMISSION_DENIED,
                "Automatic Databricks startup has no owner-scoped authorization context.",
                retryable=False,
            )
        outcome = startup_request(state)
        if isinstance(outcome, Mapping) and outcome.get("outcome") == "already_running":
            confirmed_state, _ = self._compute_state(
                self._workspace_client(credentials), credentials
            )
            if confirmed_state in {"RUNNING", "RESIZING"}:
                return
        raise ConnectorError(
            TransferErrorCode.COMPUTE_NOT_READY,
            "Databricks startup is authorized and being observed in the background. This run will resume when compute is ready.",
            retryable=True,
        )

    def _table_rows(
        self, credentials: Mapping[str, str], locator: DatabricksTableLocator
    ) -> list[tuple[Any, ...]]:
        try:
            with self._sql_cursor(credentials) as cursor:
                cursor.execute(
                    f"SELECT table_type, data_source_format "
                    f"FROM {_quote(locator.catalog)}.information_schema.tables "
                    "WHERE table_schema = ? AND table_name = ?",
                    [locator.schema_name, locator.table],
                )
                rows = cursor.fetchall()
        except ConnectorError:
            raise
        except Exception as exc:
            raise _provider_error(exc, "table metadata lookup") from None
        if len(rows) > 1:
            raise _incomplete_metadata()
        return rows

    def _read_tags(self, cursor: Any, locator: DatabricksTableLocator) -> _TagSummary:
        tag_rules = json.loads(self.settings.pipeline_databricks_sensitive_tag_rules)
        parent_markers: set[str] = set()
        column_markers: dict[str, set[str]] = {}
        statements = (
            (
                "catalog_tags",
                "SELECT tag_name, tag_value FROM "
                f"{_quote(locator.catalog)}.information_schema.catalog_tags "
                "WHERE catalog_name = ?",
                [locator.catalog],
            ),
            (
                "schema_tags",
                "SELECT tag_name, tag_value FROM "
                f"{_quote(locator.catalog)}.information_schema.schema_tags "
                "WHERE catalog_name = ? AND schema_name = ?",
                [locator.catalog, locator.schema_name],
            ),
            (
                "table_tags",
                "SELECT tag_name, tag_value FROM "
                f"{_quote(locator.catalog)}.information_schema.table_tags "
                "WHERE catalog_name = ? AND schema_name = ? AND table_name = ?",
                [locator.catalog, locator.schema_name, locator.table],
            ),
            (
                "column_tags",
                "SELECT column_name, tag_name, tag_value FROM "
                f"{_quote(locator.catalog)}.information_schema.column_tags "
                "WHERE catalog_name = ? AND schema_name = ? AND table_name = ?",
                [locator.catalog, locator.schema_name, locator.table],
            ),
        )
        try:
            for scope, statement, parameters in statements:
                cursor.execute(statement, parameters)
                rows = cursor.fetchall()
                if len(rows) > _MAX_CATALOG_ENTRIES:
                    raise _incomplete_metadata()
                for row in rows:
                    column_name, key, value = row if scope == "column_tags" else ("", *row)
                    marker_id = _tag_marker(str(key), str(value or ""), tag_rules)
                    if marker_id is None:
                        continue
                    if column_name:
                        column_markers.setdefault(str(column_name), set()).add(marker_id)
                    else:
                        parent_markers.add(marker_id)
        except ConnectorError:
            raise
        except Exception:
            raise _incomplete_metadata() from None
        if not self.settings.pipeline_databricks_metadata_visibility_verified:
            raise _incomplete_metadata()
        return _TagSummary(
            parent_markers=tuple(sorted(parent_markers)),
            column_markers={
                name: tuple(sorted(markers)) for name, markers in column_markers.items()
            },
        )

    def _load_state(self, load_session: LoadSession) -> _LoadState:
        run_id = str(load_session.metadata.get("run_id", ""))
        state = self._loads.get(run_id)
        if state is None:
            raise ConnectorError(
                TransferErrorCode.WORKER_LOST,
                "The Databricks staging session is no longer available; reconcile this run before retrying.",
                retryable=False,
            )
        return state


@dataclass
class _LoadState:
    credentials: dict[str, str]
    locator: DatabricksTableLocator
    policy: WritePolicy
    staging_name: str
    columns: tuple[str, ...]
    rows: int
    bytes: int
    target_existed: bool = False
    target_object_id: str = ""
    target_schema_fingerprint: str = ""
    publish_submitted: bool = False


@dataclass(frozen=True)
class _TagSummary:
    parent_markers: tuple[str, ...]
    column_markers: Mapping[str, tuple[str, ...]]


def _tag_marker(key: str, value: str, configured_rules: list[dict[str, str]]) -> str | None:
    if key.startswith("class."):
        return "databricks_classification"
    for rule in configured_rules:
        if key == rule["key"] and ("value" not in rule or value == rule["value"]):
            return rule["marker"]
    return None


def _polars_type(databricks_type: str) -> str:
    normalized = " ".join(databricks_type.casefold().split())
    if re.fullmatch(r"(?:varchar|char)\s*\(\s*\d+\s*\)", normalized):
        normalized = normalized.split("(", 1)[0].strip()
    decimal_match = re.fullmatch(r"decimal\s*\(\s*(\d+)\s*,\s*(\d+)\s*\)", normalized)
    if decimal_match:
        precision, scale = map(int, decimal_match.groups())
        if not 1 <= precision <= 38 or not 0 <= scale <= precision:
            raise _unsupported_type(databricks_type)
        return f"Decimal(precision={precision}, scale={scale})"
    result = _SQL_TYPES.get(normalized)
    if result is None:
        raise _unsupported_type(databricks_type)
    return result


def _polars_dtype(data_type: str) -> pl.DataType:
    if data_type.startswith("Decimal("):
        match = re.fullmatch(r"Decimal\(precision=(\d+), scale=(\d+)\)", data_type)
        if not match:
            raise _unsupported_type(data_type)
        return pl.Decimal(precision=int(match.group(1)), scale=int(match.group(2)))
    try:
        return _POLARS_TYPES[data_type]
    except KeyError:
        raise _unsupported_type(data_type) from None


def _sql_type(data_type: str) -> str:
    if data_type.startswith("Decimal("):
        match = re.fullmatch(r"Decimal\(precision=(\d+), scale=(\d+)\)", data_type)
        if not match:
            raise _unsupported_type(data_type)
        precision, scale = map(int, match.groups())
        return f"DECIMAL({precision},{scale})"
    types = {
        "String": "STRING",
        "Boolean": "BOOLEAN",
        "Int8": "TINYINT",
        "Int16": "SMALLINT",
        "Int32": "INT",
        "Int64": "BIGINT",
        "Float32": "FLOAT",
        "Float64": "DOUBLE",
        "Date": "DATE",
        "Datetime(time_unit='us')": "TIMESTAMP",
        "Binary": "BINARY",
    }
    try:
        return types[data_type]
    except KeyError:
        raise _unsupported_type(data_type) from None


def _quote(identifier: str) -> str:
    if not identifier or any(
        ord(character) < 32 or ord(character) == 127 for character in identifier
    ):
        raise ConnectorError(
            TransferErrorCode.UNSUPPORTED_TYPE,
            "A Databricks object identifier is invalid.",
            retryable=False,
        )
    return f"`{identifier.replace('`', '``')}`"


def _qualified(locator: DatabricksTableLocator) -> str:
    return ".".join((_quote(locator.catalog), _quote(locator.schema_name), _quote(locator.table)))


def _publication_target_marker(locator: DatabricksTableLocator) -> str:
    identity = [
        locator.workspace_id,
        locator.catalog.casefold(),
        locator.schema_name.casefold(),
        locator.table.casefold(),
    ]
    return hashlib.sha256(
        json.dumps(identity, ensure_ascii=True, separators=(",", ":")).encode("ascii")
    ).hexdigest()


def _parse_namespace(namespace: str) -> tuple[str, str, str]:
    try:
        parts = json.loads(namespace)
    except (TypeError, ValueError):
        parts = []
    if (
        not isinstance(parts, list)
        or len(parts) != 3
        or not all(isinstance(item, str) and item for item in parts)
        or not re.fullmatch(r"[0-9a-f]{64}", parts[0])
    ):
        raise ConnectorError(
            TransferErrorCode.INTERNAL_ERROR,
            "Select a valid Unity Catalog schema.",
            retryable=False,
        )
    return parts[0], parts[1], parts[2]


def _parse_cursor(value: str | None) -> int:
    if value is None:
        return 0
    if not value.isdigit() or int(value) > 2**31:
        raise ConnectorError(
            TransferErrorCode.INTERNAL_ERROR,
            "The Unity Catalog page cursor is invalid.",
            retryable=False,
        )
    return int(value)


def _databricks_locator(locator: Locator) -> DatabricksTableLocator:
    try:
        parsed = validate_locator(locator)
    except ValueError as exc:
        raise ConnectorError(
            TransferErrorCode.INTERNAL_ERROR,
            "The selected Databricks table identity is invalid.",
            retryable=False,
        ) from exc
    if not isinstance(parsed, DatabricksTableLocator):
        raise ConnectorError(
            TransferErrorCode.INTERNAL_ERROR,
            "Select a supported Unity Catalog Delta table.",
            retryable=False,
        )
    return parsed


def _require_workspace_identity(
    credentials: Mapping[str, str], locator: DatabricksTableLocator
) -> None:
    if locator.workspace_id != databricks_workspace_id(str(credentials.get("workspace_url", ""))):
        raise ConnectorError(
            TransferErrorCode.SCHEMA_DRIFT,
            "This Databricks table belongs to a different workspace; review the selection again.",
            retryable=False,
        )
    selected_generation = locator.connection_generation
    active_generation = str(credentials.get("connection_generation", ""))
    if not selected_generation or not active_generation or selected_generation != active_generation:
        raise ConnectorError(
            TransferErrorCode.CREDENTIALS_STALE,
            "The Databricks connection changed after this table was reviewed. Select and review it again.",
            retryable=False,
        )


def _staging_schema(settings: Settings) -> tuple[str, str]:
    parts = settings.pipeline_databricks_staging_schema.split(".")
    if len(parts) != 2 or not all(
        re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,127}", part) for part in parts
    ):
        raise ConnectorError(
            TransferErrorCode.PERMISSION_DENIED,
            "The operator has not configured an approved private Databricks staging schema.",
            retryable=False,
        )
    return parts[0], parts[1]


def _record_publication_event(
    credentials: Mapping[str, str],
    event: str,
    *,
    target: DatabricksTableLocator,
    run_id: str,
    staging_name: str = "",
    write_mode: str = "",
    rows: int = 0,
    bytes_written: int = 0,
    error: ConnectorError | None = None,
) -> None:
    recorder = credentials.get("_publication_event")
    if not callable(recorder):
        raise ConnectorError(
            TransferErrorCode.PERMISSION_DENIED,
            "Databricks writes require durable publication coordination from the Data Mover worker.",
            retryable=False,
        )
    try:
        recorder(
            event,
            target=target,
            run_id=run_id,
            staging_name=staging_name,
            write_mode=write_mode,
            rows=rows,
            bytes_written=bytes_written,
            error=error,
        )
    except ConnectorError:
        raise
    except Exception as exc:
        raise ConnectorError(
            TransferErrorCode.WORKER_LOST,
            "Databricks publication state could not be saved. The operation will not continue until recovery is safe.",
            retryable=False,
        ) from exc


def _fingerprint(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _unsupported_type(value: str) -> ConnectorError:
    return ConnectorError(
        TransferErrorCode.UNSUPPORTED_TYPE,
        "The selected table contains a nested, complex, or unsupported scalar type.",
        retryable=False,
        field_errors={"type": value[:120]},
    )


def _incomplete_metadata() -> ConnectorError:
    return ConnectorError(
        TransferErrorCode.METADATA_INCOMPLETE,
        "Databricks metadata coverage could not be verified; review permissions and supported runtime before continuing.",
        retryable=False,
    )


def _sensitive_destination() -> ConnectorError:
    return ConnectorError(
        TransferErrorCode.SENSITIVE_DATA_GUARDRAIL_BLOCKED,
        "Databricks destination sensitivity metadata blocks this write. Review the target policy and choose another destination.",
        retryable=False,
    )


def _sdk_value(value: object, default: str = "") -> str:
    """Normalize generated SDK enums while retaining plain-string test doubles."""

    return str((value.value if isinstance(value, Enum) else value) or default)


def _provider_error(
    exception: Exception, operation: str, *, destination: bool = False
) -> ConnectorError:
    status = getattr(exception, "status_code", None) or getattr(exception, "http_status", None)
    name = type(exception).__name__.casefold()
    # The SDK exposes some errors as subclasses (e.g. ResourceDoesNotExist)
    # without carrying an HTTP status.
    missing = isinstance(exception, NotFound) or status == 404 or "notfound" in name
    code = (
        TransferErrorCode.RATE_LIMITED
        if status == 429 or "toomanyrequests" in name or "ratelimit" in name
        else TransferErrorCode.AUTHENTICATION_FAILED
        if status == 401 or "unauthenticated" in name
        else TransferErrorCode.PERMISSION_DENIED
        if status == 403 or "permissiondenied" in name
        else TransferErrorCode.DESTINATION_NOT_FOUND
        if destination and missing
        else TransferErrorCode.SOURCE_NOT_FOUND
        if missing
        else TransferErrorCode.DESTINATION_CONFLICT
        if destination and (status == 409 or "alreadyexists" in name or "conflict" in name)
        else TransferErrorCode.CONNECTION_TIMEOUT
        if "timeout" in name
        else TransferErrorCode.PROVIDER_UNAVAILABLE
        if (status and status >= 500) or name in {"connectionerror", "chunkedencodingerror"}
        else TransferErrorCode.INTERNAL_ERROR
    )
    return ConnectorError(
        code,
        f"Databricks {operation} could not be completed.",
        retryable=code
        in {
            TransferErrorCode.RATE_LIMITED,
            TransferErrorCode.PROVIDER_UNAVAILABLE,
            TransferErrorCode.CONNECTION_TIMEOUT,
        },
        http_status=int(status) if isinstance(status, int) else None,
    )


def register() -> None:
    register_connector(DatabricksConnector)
