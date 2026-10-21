"""Offline Unity Catalog and Delta emulator used only by demo registries."""

from __future__ import annotations

import hashlib
import json
import threading
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from uuid import NAMESPACE_URL, uuid4, uuid5

import polars as pl

from app.connectors.base import (
    AbortResult,
    BatchWriteResult,
    CatalogPage,
    ColumnSchema,
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
from app.connectors.fake import (
    _demo_frame,
    _empty_frame,
    _require_credentials,
    _schema,
    _validate_batch,
)
from app.connectors.locators import (
    DatabricksAppendPolicy,
    DatabricksCreatePolicy,
    DatabricksReplacePolicy,
    DatabricksTableLocator,
    Locator,
    WritePolicy,
    databricks_workspace_id,
)


@dataclass
class DemoDeltaTable:
    frame: pl.DataFrame
    object_id: str = field(default_factory=lambda: str(uuid4()))
    columns: tuple[ColumnSchema, ...] = ()


class DemoDatabricksBackend:
    """Share committed tables between connector instances in one app generation."""

    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.workspaces: dict[str, dict[tuple[str, str, str], DemoDeltaTable]] = {}

    def tables(self, workspace: str) -> dict[tuple[str, str, str], DemoDeltaTable]:
        if workspace not in self.workspaces:
            self.workspaces[workspace] = {
                ("demo", schema, table): DemoDeltaTable(
                    _demo_frame(),
                    # Saved locators must survive reconstruction of unchanged
                    # seed tables. Newly created tables still receive random IDs.
                    object_id=str(
                        uuid5(
                            NAMESPACE_URL,
                            json.dumps(
                                ["data-mover-demo-delta-v1", workspace, "demo", schema, table]
                            ),
                        )
                    ),
                )
                for schema, names in {
                    "analytics": ("asset_inventory", "readiness_events"),
                    "staging": ("incoming_orders",),
                }.items()
                for table in names
            }
        return self.workspaces[workspace]


@dataclass
class _PendingDeltaLoad:
    workspace: str
    locator: DatabricksTableLocator
    schema: ObjectSchema
    policy: WritePolicy
    expected_id: str
    frames: list[pl.DataFrame] = field(default_factory=list)
    sequence: int = 0
    bytes: int = 0


class FakeDatabricksConnector:
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
        writer_enabled=True,
        writer_setting="pipeline_enable_databricks_writer",
        limitations=(
            "Emulated Unity Catalog and Delta tables; no network requests or compute costs.",
        ),
    )

    def __init__(self, backend: DemoDatabricksBackend | None = None) -> None:
        self._backend = backend or DemoDatabricksBackend()
        self._pending: dict[str, _PendingDeltaLoad] = {}

    @staticmethod
    def _workspace(credentials: Mapping[str, str]) -> str:
        _require_credentials(
            credentials,
            ("workspace_url", "token", "compute_kind", "compute_id", "http_path"),
            "Databricks",
        )
        return databricks_workspace_id(credentials["workspace_url"])

    def _locator(self, credentials: Mapping[str, str], locator: Locator) -> DatabricksTableLocator:
        if not isinstance(
            locator, DatabricksTableLocator
        ) or locator.workspace_id != self._workspace(credentials):
            raise ConnectorError(
                TransferErrorCode.SCHEMA_DRIFT,
                "Select a table in this Databricks workspace.",
                retryable=False,
            )
        return locator

    def test_connection(self, credentials: Mapping[str, str]) -> ConnectionHealth:
        self._workspace(credentials)
        return ConnectionHealth(
            status="connected",
            message="Emulated connection only · no network request was made. SQL readiness: ready.",
            latency_ms=0,
            server_identity="Data Mover Databricks emulator",
            runtime_status="RUNNING",
        )

    def compute_status(self, credentials: Mapping[str, str]) -> dict[str, str]:
        self._workspace(credentials)
        return {
            "kind": credentials["compute_kind"],
            "compute_id": credentials["compute_id"],
            "state": "RUNNING",
        }

    def start_compute(self, credentials: Mapping[str, str]) -> dict[str, str]:
        return {**self.compute_status(credentials), "outcome": "already_running"}

    def list_namespaces(self, credentials: Mapping[str, str]) -> list[RemoteNamespace]:
        workspace = self._workspace(credentials)
        with self._backend.lock:
            namespaces = sorted(
                {(catalog, schema) for catalog, schema, _ in self._backend.tables(workspace)}
            )
        return [
            RemoteNamespace(
                json.dumps([workspace, catalog, schema], separators=(",", ":")),
                f"{catalog} · {schema}",
                "databricks_schema",
            )
            for catalog, schema in namespaces
        ]

    def list_objects(
        self, credentials: Mapping[str, str], namespace: str, cursor: str | None = None
    ) -> CatalogPage:
        workspace = self._workspace(credentials)
        allowed = {item.name for item in self.list_namespaces(credentials)}
        if namespace not in allowed:
            raise ConnectorError(
                TransferErrorCode.SOURCE_NOT_FOUND,
                "Select an available Databricks catalog and schema.",
            )
        _, catalog, schema = json.loads(namespace)
        with self._backend.lock:
            items = tuple(
                RemoteObject(
                    table,
                    table,
                    DatabricksTableLocator(
                        workspace_id=workspace,
                        catalog=catalog,
                        schema=schema,
                        table=table,
                        object_id=value.object_id,
                    ),
                    estimated_rows=value.frame.height,
                    format="Delta",
                )
                for (table_catalog, table_schema, table), value in sorted(
                    self._backend.tables(workspace).items()
                )
                if (table_catalog, table_schema) == (catalog, schema)
            )
        return CatalogPage(items=items)

    def _table(
        self, credentials: Mapping[str, str], locator: Locator
    ) -> tuple[DatabricksTableLocator, DemoDeltaTable]:
        selected = self._locator(credentials, locator)
        with self._backend.lock:
            table = self._backend.tables(selected.workspace_id).get(
                (selected.catalog, selected.schema_name, selected.table)
            )
            if table is None:
                raise ConnectorError(
                    TransferErrorCode.SOURCE_NOT_FOUND,
                    "That Delta table was not found.",
                    retryable=False,
                )
            if selected.object_id and selected.object_id != table.object_id:
                raise ConnectorError(
                    TransferErrorCode.SCHEMA_DRIFT,
                    "The Delta table identity changed. Select it again.",
                    retryable=False,
                )
            return selected, DemoDeltaTable(table.frame.clone(), table.object_id, table.columns)

    def inspect_object(self, credentials: Mapping[str, str], locator: Locator) -> ObjectSchema:
        selected, table = self._table(credentials, locator)
        selected = selected.model_copy(update={"object_id": table.object_id})
        schema = _schema(table.frame, selected)
        if table.columns:
            schema = replace(schema, columns=table.columns)
        fingerprint = hashlib.sha256(
            json.dumps(
                [(column.name, column.data_type, column.nullable) for column in schema.columns]
            ).encode()
        ).hexdigest()
        return replace(
            schema,
            metadata_coverage="verified",
            schema_fingerprint=fingerprint,
            sensitivity_fingerprint=hashlib.sha256(b"demo-no-sensitive-markings-v1").hexdigest(),
        )

    def count_rows(self, credentials: Mapping[str, str], locator: Locator) -> int:
        return self._table(credentials, locator)[1].frame.height

    def extract(
        self, credentials: Mapping[str, str], locator: Locator, *, batch_rows: int, batch_bytes: int
    ):
        yield from bounded_frame_batches(
            self._table(credentials, locator)[1].frame,
            batch_rows=batch_rows,
            batch_bytes=batch_bytes,
        )

    def prepare_destination(
        self,
        credentials: Mapping[str, str],
        locator: Locator,
        schema: ObjectSchema,
        write_policy: WritePolicy,
        *,
        run_id: str,
    ) -> LoadSession:
        selected = self._locator(credentials, locator)
        if not isinstance(
            write_policy, (DatabricksCreatePolicy, DatabricksAppendPolicy, DatabricksReplacePolicy)
        ):
            raise ConnectorError(
                TransferErrorCode.DESTINATION_CONFLICT,
                "Choose a supported Delta write mode.",
                retryable=False,
            )
        if not schema.columns:
            raise ConnectorError(
                TransferErrorCode.UNSUPPORTED_TYPE,
                "Delta destinations require at least one column.",
                retryable=False,
            )
        with self._backend.lock:
            tables = self._backend.tables(selected.workspace_id)
            if (selected.catalog, selected.schema_name) not in {
                (catalog, namespace) for catalog, namespace, _ in tables
            }:
                raise ConnectorError(
                    TransferErrorCode.DESTINATION_NOT_FOUND,
                    "Select an available Databricks catalog and schema.",
                )
            current = tables.get((selected.catalog, selected.schema_name, selected.table))
            if isinstance(write_policy, DatabricksCreatePolicy) and current is not None:
                raise ConnectorError(
                    TransferErrorCode.DESTINATION_CONFLICT,
                    "That Delta table already exists.",
                    retryable=False,
                )
            if not isinstance(write_policy, DatabricksCreatePolicy):
                self._table(credentials, selected)
            expected_id = current.object_id if current else ""
        session_id = f"demo_delta_{run_id}"
        self._pending[session_id] = _PendingDeltaLoad(
            selected.workspace_id, selected, schema, write_policy, expected_id
        )
        return LoadSession(
            locator=selected,
            write_policy=write_policy,
            staging_name=session_id,
            columns=tuple(column.name for column in schema.columns),
        )

    def _load(self, session: LoadSession) -> _PendingDeltaLoad:
        pending = self._pending.get(session.staging_name)
        if pending is None:
            raise ConnectorError(
                TransferErrorCode.INTERNAL_ERROR, "Destination session is not open."
            )
        return pending

    def write_batch(self, load_session: LoadSession, batch: TransferBatch) -> BatchWriteResult:
        pending = self._load(load_session)
        frame = _validate_batch(batch)
        expected = tuple(column.name for column in pending.schema.columns)
        if batch.sequence <= pending.sequence or set(frame.columns) != set(expected):
            raise ConnectorError(
                TransferErrorCode.SCHEMA_DRIFT,
                "The source schema or batch sequence changed during extraction.",
                retryable=False,
            )
        pending.frames.append(frame.select(expected).clone())
        pending.sequence = batch.sequence
        pending.bytes += batch.byte_count
        return BatchWriteResult(frame.height, batch.byte_count)

    def finalize(self, load_session: LoadSession) -> DestinationManifest:
        pending = self._load(load_session)
        incoming = (
            pl.concat(pending.frames, how="vertical")
            if pending.frames
            else _empty_frame(pending.schema)
        )
        key = (pending.locator.catalog, pending.locator.schema_name, pending.locator.table)
        with self._backend.lock:
            tables = self._backend.tables(pending.workspace)
            current = tables.get(key)
            if (current.object_id if current else "") != pending.expected_id:
                raise ConnectorError(
                    TransferErrorCode.DESTINATION_CONFLICT,
                    "The Delta destination changed during this transfer.",
                    retryable=False,
                )
            frame = incoming
            if isinstance(pending.policy, DatabricksAppendPolicy) and current is not None:
                if current.frame.schema != incoming.schema:
                    raise ConnectorError(
                        TransferErrorCode.SCHEMA_DRIFT,
                        "The source schema does not match the existing Delta table.",
                        retryable=False,
                    )
                frame = pl.concat([current.frame, incoming], how="vertical")
            columns = (
                current.columns
                if isinstance(pending.policy, DatabricksAppendPolicy) and current is not None
                else pending.schema.columns
            )
            tables[key] = DemoDeltaTable(
                frame, current.object_id if current else str(uuid4()), columns
            )
        self._pending.pop(load_session.staging_name)
        return DestinationManifest(load_session.locator, incoming.height, pending.bytes)

    def abort(self, load_session: LoadSession) -> AbortResult:
        self._pending.pop(load_session.staging_name, None)
        return AbortResult.ROLLED_BACK
