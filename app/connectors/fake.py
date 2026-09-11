"""Deterministic, stateful demo connectors matching the live connector contracts.

The emulator never performs network I/O. It models catalog discovery, bounded
extraction, transactional destination writes, and persistence across connector
instances so a demo run exercises the same application behavior as a live run.
It deliberately does not claim to validate reachability or authentication.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from io import BytesIO

import polars as pl

from app.config import Settings, get_settings
from app.connectors.base import (
    BatchWriteResult,
    CatalogPage,
    ColumnSchema,
    ConnectionHealth,
    DestinationManifest,
    LoadSession,
    ObjectSchema,
    ProviderCapabilities,
    ProvisionedDataset,
    RemoteNamespace,
    RemoteObject,
    TransferBatch,
    bounded_frame_batches,
)
from app.connectors.csv_source import CsvSourceConnector
from app.connectors.errors import ConnectorError, TransferErrorCode
from app.connectors.locators import (
    FoundryDatasetFilesLocator,
    FoundryReplaceFilePolicy,
    FoundryUploadLocator,
    Locator,
    PostgresAppendPolicy,
    PostgresReplacePolicy,
    PostgresTableLocator,
    PostgresUpsertPolicy,
    WritePolicy,
    postgres_table,
)
from app.connectors.registry import register_connector

DEMO_DATASET = "ri.foundry.main.dataset.demo-operations"
DEMO_RAW_DATASET = "ri.foundry.main.dataset.demo-raw"
DEMO_DEST_DATASET = "ri.foundry.main.dataset.demo-destination"

POSTGRES_SCHEMAS = {
    "public": ("readiness_events", "asset_inventory", "mission_orders"),
    "staging": ("readiness_events_stage", "raw_events", "ingest_failures"),
    "reporting": ("daily_readiness", "asset_utilization", "mission_throughput"),
}

MSS_FILES = {
    DEMO_DATASET: (
        "mission_orders.parquet",
        "readiness_rollup.parquet",
        "asset_assignments.parquet",
    ),
    DEMO_RAW_DATASET: ("source_events.csv", "incoming_orders.csv"),
}

MCSCOP_FILES = {
    DEMO_DEST_DATASET: ("readiness.snappy.parquet",),
}

_FOUNDRY_LIMITATIONS = ("Foundry file metadata does not expose portable schema or row counts.",)


def _demo_frame() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "event_id": [1, 2, 3],
            "unit_name": ["Unit-1", "Unit-2", "Unit-3"],
            "ready": [True, False, True],
            "score": [98.4, 82.0, 91.2],
        }
    )


def _clone(frame: pl.DataFrame) -> pl.DataFrame:
    return frame.clone()


def _schema(
    frame: pl.DataFrame,
    locator: Locator,
    *,
    primary_key: tuple[str, ...] = (),
    unique_constraints: tuple[tuple[str, ...], ...] = (),
) -> ObjectSchema:
    return ObjectSchema(
        locator=locator,
        columns=tuple(
            ColumnSchema(
                name=name,
                data_type=str(dtype),
                nullable=name not in primary_key,
            )
            for name, dtype in frame.schema.items()
        ),
        primary_key=primary_key,
        unique_constraints=unique_constraints,
        estimated_rows=frame.height,
    )


def _require_credentials(
    credentials: Mapping[str, str], fields: tuple[str, ...], label: str
) -> None:
    if any(not str(credentials.get(name) or "").strip() for name in fields):
        raise ConnectorError(
            TransferErrorCode.CREDENTIALS_MISSING,
            f"Complete the required {label} connection fields first.",
            retryable=False,
        )


def _postgres_connection_id(credentials: Mapping[str, str]) -> str:
    return "|".join(
        (
            str(credentials.get("host") or "").casefold(),
            str(credentials.get("port") or "5432"),
            str(credentials.get("database") or "").casefold(),
        )
    )


def _foundry_connection_id(credentials: Mapping[str, str]) -> str:
    return str(credentials.get("endpoint") or "").strip().rstrip("/")


def _empty_frame(schema: ObjectSchema) -> pl.DataFrame:
    def dtype_for(value: str) -> type[pl.DataType]:
        folded = value.casefold()
        if "int" in folded:
            return pl.Int64
        if "float" in folded or "double" in folded or "decimal" in folded:
            return pl.Float64
        if "bool" in folded:
            return pl.Boolean
        if folded == "date":
            return pl.Date
        if "datetime" in folded or "timestamp" in folded:
            return pl.Datetime
        if folded == "time":
            return pl.Time
        if "binary" in folded or "byte" in folded:
            return pl.Binary
        return pl.String

    return pl.DataFrame(
        schema={column.name: dtype_for(column.data_type) for column in schema.columns}
    )


def _parquet_size(frame: pl.DataFrame) -> int:
    buffer = BytesIO()
    frame.write_parquet(buffer, compression="snappy")
    return len(buffer.getvalue())


def _file_size(path: str, frame: pl.DataFrame) -> int:
    if path.casefold().endswith(".csv"):
        return len(frame.write_csv().encode("utf-8"))
    return _parquet_size(frame)


def _validate_batch(batch: TransferBatch) -> pl.DataFrame:
    if not isinstance(batch.frame, pl.DataFrame) or batch.frame.height != batch.row_count:
        raise ConnectorError(
            TransferErrorCode.PARTIAL_WRITE,
            "The source batch row acknowledgement does not match its data.",
            retryable=False,
        )
    if batch.byte_count != int(batch.frame.estimated_size()):
        raise ConnectorError(
            TransferErrorCode.PARTIAL_WRITE,
            "The source batch byte acknowledgement does not match its data.",
            retryable=False,
        )
    return batch.frame


@dataclass
class _DemoTable:
    frame: pl.DataFrame
    primary_key: tuple[str, ...] = ()
    unique_constraints: tuple[tuple[str, ...], ...] = ()

    def copy(self) -> _DemoTable:
        return _DemoTable(_clone(self.frame), self.primary_key, self.unique_constraints)


@dataclass
class _PendingLoad:
    connection_id: str
    locator: Locator
    schema: ObjectSchema
    write_policy: WritePolicy
    frames: list[pl.DataFrame] = field(default_factory=list)
    rows: int = 0
    bytes: int = 0
    last_sequence: int = 0


class _DemoBackend:
    """Process-local remote state shared by connector instances in one registry load."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._postgres: dict[str, dict[tuple[str, str], _DemoTable]] = {}
        self._foundry: dict[tuple[str, str], dict[tuple[str, str], dict[str, pl.DataFrame]]] = {}
        self._foundry_template_sets: dict[str, dict[str, tuple[str, ...]]] = {}

    def register_foundry_templates(
        self, provider: str, templates: Mapping[str, tuple[str, ...]]
    ) -> None:
        with self._lock:
            self._foundry_template_sets.setdefault(provider, dict(templates))

    def _postgres_for(self, connection_id: str) -> dict[tuple[str, str], _DemoTable]:
        tables = self._postgres.get(connection_id)
        if tables is None:
            populated = _demo_frame()
            tables = {
                (schema_name, table): _DemoTable(
                    populated.clear()
                    if (schema_name, table) == ("public", "mission_orders")
                    else _clone(populated),
                    primary_key=("event_id",),
                    unique_constraints=(("event_id",),),
                )
                for schema_name, names in POSTGRES_SCHEMAS.items()
                for table in names
            }
            self._postgres[connection_id] = tables
        return tables

    def postgres_namespaces(self, connection_id: str) -> list[str]:
        with self._lock:
            return sorted({schema for schema, _ in self._postgres_for(connection_id)})

    def postgres_objects(self, connection_id: str, namespace: str) -> list[str] | None:
        with self._lock:
            tables = self._postgres_for(connection_id)
            if namespace not in {schema for schema, _ in tables}:
                return None
            return sorted(table for schema, table in tables if schema == namespace)

    def postgres_table(
        self, connection_id: str, locator: PostgresTableLocator
    ) -> _DemoTable | None:
        with self._lock:
            table = self._postgres_for(connection_id).get((locator.schema_name, locator.table))
            return table.copy() if table is not None else None

    def commit_postgres(self, pending: _PendingLoad) -> tuple[int, _DemoTable]:
        assert isinstance(pending.locator, PostgresTableLocator)
        incoming = (
            pl.concat(pending.frames, how="vertical_relaxed")
            if pending.frames
            else _empty_frame(pending.schema)
        )
        key = (pending.locator.schema_name, pending.locator.table)
        with self._lock:
            tables = self._postgres_for(pending.connection_id)
            current = tables.get(key)
            target = current.copy() if current is not None else None
            if target is not None:
                incoming = self._coerce_for_table(incoming, target)

            policy = pending.write_policy
            if isinstance(policy, PostgresAppendPolicy):
                result = (
                    _DemoTable(
                        pl.concat([target.frame, incoming], how="vertical_relaxed"),
                        target.primary_key,
                        target.unique_constraints,
                    )
                    if target is not None
                    else _DemoTable(_clone(incoming))
                )
                self._enforce_constraints(result)
                loaded = incoming.height
            elif isinstance(policy, PostgresReplacePolicy):
                if policy.schema_policy == "recreate" or target is None:
                    result = _DemoTable(_clone(incoming))
                else:
                    result = _DemoTable(
                        _clone(incoming), target.primary_key, target.unique_constraints
                    )
                    self._enforce_constraints(result)
                loaded = incoming.height
            elif isinstance(policy, PostgresUpsertPolicy):
                if target is None:
                    raise ConnectorError(
                        TransferErrorCode.DESTINATION_CONFLICT,
                        "PostgreSQL upsert requires an existing unique destination key.",
                        retryable=False,
                    )
                allowed = {target.primary_key, *target.unique_constraints} - {()}
                conflict = tuple(policy.conflict_columns)
                if conflict not in allowed:
                    raise ConnectorError(
                        TransferErrorCode.SCHEMA_DRIFT,
                        "The selected PostgreSQL upsert key is no longer unique.",
                        retryable=False,
                    )
                self._enforce_constraints(
                    _DemoTable(incoming, target.primary_key, target.unique_constraints)
                )
                frame, loaded = self._upsert(target.frame, incoming, conflict, policy.action)
                result = _DemoTable(frame, target.primary_key, target.unique_constraints)
                self._enforce_constraints(result)
            else:
                raise ConnectorError(
                    TransferErrorCode.UNSUPPORTED_TYPE,
                    "The PostgreSQL write policy is unsupported.",
                    retryable=False,
                )
            tables[key] = result
            return loaded, result.copy()

    @staticmethod
    def _enforce_constraints(table: _DemoTable) -> None:
        constraints = {table.primary_key, *table.unique_constraints} - {()}
        for constraint in constraints:
            seen: set[tuple[object, ...]] = set()
            for row in table.frame.select(constraint).iter_rows():
                if any(value is None for value in row):
                    if constraint == table.primary_key:
                        raise ConnectorError(
                            TransferErrorCode.DESTINATION_CONFLICT,
                            "A PostgreSQL primary-key value cannot be null.",
                            retryable=False,
                        )
                    continue
                if row in seen:
                    raise ConnectorError(
                        TransferErrorCode.DESTINATION_CONFLICT,
                        "The destination write violates a PostgreSQL unique constraint.",
                        retryable=False,
                    )
                seen.add(row)

    @staticmethod
    def _coerce_for_table(incoming: pl.DataFrame, target: _DemoTable) -> pl.DataFrame:
        target_columns = tuple(target.frame.columns)
        extra = set(incoming.columns) - set(target_columns)
        if extra:
            raise ConnectorError(
                TransferErrorCode.SCHEMA_DRIFT,
                "The source contains columns that are not present in the destination table.",
                retryable=False,
            )
        frame = incoming
        required = set(target.primary_key)
        for name in target_columns:
            if name not in frame.columns:
                if name in required:
                    raise ConnectorError(
                        TransferErrorCode.SCHEMA_DRIFT,
                        "The source omits a required destination column.",
                        retryable=False,
                    )
                frame = frame.with_columns(pl.lit(None).alias(name))
        try:
            frame = frame.select(target_columns).cast(target.frame.schema, strict=True)
        except Exception as exc:
            raise ConnectorError(
                TransferErrorCode.UNSUPPORTED_TYPE,
                "The source values are incompatible with the destination schema.",
                retryable=False,
            ) from exc
        if any(frame[name].null_count() for name in required):
            raise ConnectorError(
                TransferErrorCode.SCHEMA_DRIFT,
                "The source contains null values for a required destination key.",
                retryable=False,
            )
        return frame

    @staticmethod
    def _upsert(
        existing: pl.DataFrame,
        incoming: pl.DataFrame,
        conflict: tuple[str, ...],
        action: str,
    ) -> tuple[pl.DataFrame, int]:
        rows = existing.to_dicts()
        index = {
            tuple(row[name] for name in conflict): position
            for position, row in enumerate(rows)
            if all(row[name] is not None for name in conflict)
        }
        seen_updates: set[tuple[object, ...]] = set()
        loaded = 0
        for incoming_row in incoming.to_dicts():
            key = tuple(incoming_row[name] for name in conflict)
            position = index.get(key) if all(value is not None for value in key) else None
            if position is None:
                index[key] = len(rows)
                rows.append(incoming_row)
                loaded += 1
            elif action == "update":
                if key in seen_updates:
                    raise ConnectorError(
                        TransferErrorCode.DESTINATION_CONFLICT,
                        "A PostgreSQL upsert batch contains the same key more than once.",
                        retryable=False,
                    )
                seen_updates.add(key)
                rows[position] = incoming_row
                loaded += 1
        if not rows:
            return existing.clear(), loaded
        return pl.DataFrame(rows).select(existing.columns).cast(existing.schema), loaded

    def _foundry_for(
        self, provider: str, connection_id: str
    ) -> dict[tuple[str, str], dict[str, pl.DataFrame]]:
        connection_key = (provider, connection_id)
        datasets = self._foundry.get(connection_key)
        if datasets is None:
            datasets = {}
            self._foundry[connection_key] = datasets
        return datasets

    def foundry_files(
        self,
        provider: str,
        connection_id: str,
        dataset_rid: str,
        branch: str,
    ) -> dict[str, pl.DataFrame] | None:
        with self._lock:
            templates = self._foundry_template_sets.get(provider, {})
            if dataset_rid not in templates:
                return None
            datasets = self._foundry_for(provider, connection_id)
            key = (dataset_rid, branch)
            if key not in datasets:
                datasets[key] = {path: _demo_frame() for path in templates[dataset_rid]}
            return {path: _clone(frame) for path, frame in datasets[key].items()}

    def create_foundry_dataset(
        self,
        provider: str,
        connection_id: str,
        dataset_rid: str,
        branch: str,
    ) -> None:
        with self._lock:
            templates = self._foundry_template_sets.setdefault(provider, {})
            templates[dataset_rid] = ()
            datasets = self._foundry_for(provider, connection_id)
            datasets[(dataset_rid, branch)] = {}

    def put_foundry(
        self,
        provider: str,
        connection_id: str,
        locator: FoundryUploadLocator,
        frame: pl.DataFrame,
    ) -> None:
        with self._lock:
            files = self.foundry_files(provider, connection_id, locator.dataset_rid, locator.branch)
            if files is None:
                raise ConnectorError(
                    TransferErrorCode.DESTINATION_NOT_FOUND,
                    "The emulated Foundry dataset does not exist.",
                    retryable=False,
                )
            datasets = self._foundry_for(provider, connection_id)
            datasets[(locator.dataset_rid, locator.branch)][locator.file_name] = _clone(frame)


class FakePostgresConnector:
    capabilities = ProviderCapabilities(
        provider="postgres",
        label="PostgreSQL",
        technology="PostgreSQL 16",
        mark="PG",
        source=True,
        destination=True,
        object_model="database → schema → table",
        write_modes=("append", "upsert", "replace"),
        namespaces_label="Schema",
        objects_label="Table",
        writer_enabled=True,
    )

    def __init__(self, backend: _DemoBackend | None = None) -> None:
        self._backend = backend or _DemoBackend()
        self._pending: dict[str, _PendingLoad] = {}

    @staticmethod
    def _validate(credentials: Mapping[str, str]) -> str:
        _require_credentials(
            credentials, ("host", "database", "username", "password"), "PostgreSQL"
        )
        try:
            port = int(credentials.get("port") or "5432")
        except ValueError as exc:
            raise ConnectorError(
                TransferErrorCode.CREDENTIALS_MISSING,
                "The PostgreSQL port must be a valid number.",
                retryable=False,
            ) from exc
        if not 1 <= port <= 65535:
            raise ConnectorError(
                TransferErrorCode.CREDENTIALS_MISSING,
                "The PostgreSQL port must be between 1 and 65535.",
                retryable=False,
            )
        return _postgres_connection_id(credentials)

    def test_connection(self, credentials) -> ConnectionHealth:
        self._validate(credentials)
        return ConnectionHealth(
            status="connected",
            message="Emulated connection only · no network request was made.",
            latency_ms=0,
            server_identity="Data Mover PostgreSQL emulator",
        )

    def list_namespaces(self, credentials) -> list[RemoteNamespace]:
        connection_id = self._validate(credentials)
        return [
            RemoteNamespace(name=name, display_name=name, kind="schema")
            for name in self._backend.postgres_namespaces(connection_id)
        ]

    def list_objects(self, credentials, namespace: str, cursor: str | None = None) -> CatalogPage:
        connection_id = self._validate(credentials)
        tables = self._backend.postgres_objects(connection_id, namespace)
        if tables is None:
            raise ConnectorError(TransferErrorCode.SOURCE_NOT_FOUND, "Select an available schema.")
        return CatalogPage(
            items=tuple(
                RemoteObject(
                    name=table, display_name=table, locator=postgres_table(namespace, table)
                )
                for table in tables
            )
        )

    def inspect_object(self, credentials, locator: Locator) -> ObjectSchema:
        connection_id = self._validate(credentials)
        if not isinstance(locator, PostgresTableLocator):
            raise ConnectorError(
                TransferErrorCode.SOURCE_NOT_FOUND, "PostgreSQL locator is invalid."
            )
        table = self._backend.postgres_table(connection_id, locator)
        if table is None:
            raise ConnectorError(TransferErrorCode.SOURCE_NOT_FOUND, "That table was not found.")
        return _schema(
            table.frame,
            locator,
            primary_key=table.primary_key,
            unique_constraints=table.unique_constraints,
        )

    def count_rows(self, credentials, locator: Locator) -> int | None:
        connection_id = self._validate(credentials)
        if not isinstance(locator, PostgresTableLocator):
            raise ConnectorError(
                TransferErrorCode.DESTINATION_NOT_FOUND, "PostgreSQL locator is invalid."
            )
        table = self._backend.postgres_table(connection_id, locator)
        return table.frame.height if table is not None else 0

    def extract(
        self, credentials, locator: Locator, *, batch_rows: int, batch_bytes: int
    ) -> Iterator[TransferBatch]:
        connection_id = self._validate(credentials)
        if not isinstance(locator, PostgresTableLocator):
            raise ConnectorError(
                TransferErrorCode.SOURCE_NOT_FOUND, "PostgreSQL locator is invalid."
            )
        table = self._backend.postgres_table(connection_id, locator)
        if table is None:
            raise ConnectorError(TransferErrorCode.SOURCE_NOT_FOUND, "That table was not found.")
        yield from bounded_frame_batches(
            table.frame, batch_rows=batch_rows, batch_bytes=batch_bytes
        )

    def prepare_destination(
        self,
        credentials,
        locator: Locator,
        schema: ObjectSchema,
        write_policy: WritePolicy,
        *,
        run_id: str,
    ) -> LoadSession:
        connection_id = self._validate(credentials)
        if not isinstance(locator, PostgresTableLocator):
            raise ConnectorError(
                TransferErrorCode.DESTINATION_NOT_FOUND, "PostgreSQL locator is invalid."
            )
        if not schema.columns:
            raise ConnectorError(
                TransferErrorCode.UNSUPPORTED_TYPE,
                "PostgreSQL destinations require at least one source column.",
                retryable=False,
            )
        session_id = f"dm_stage_{run_id.replace('-', '')[:12]}"
        self._pending[session_id] = _PendingLoad(
            connection_id=connection_id,
            locator=locator,
            schema=schema,
            write_policy=write_policy,
        )
        return LoadSession(
            locator=locator,
            write_policy=write_policy,
            staging_name=session_id,
            columns=tuple(column.name for column in schema.columns),
        )

    def write_batch(self, load_session: LoadSession, batch: TransferBatch) -> BatchWriteResult:
        pending = self._pending.get(load_session.staging_name)
        if pending is None:
            raise ConnectorError(
                TransferErrorCode.INTERNAL_ERROR, "Destination session is not open."
            )
        frame = _validate_batch(batch)
        if batch.sequence <= pending.last_sequence:
            raise ConnectorError(
                TransferErrorCode.PARTIAL_WRITE,
                "Destination batches must have strictly increasing sequence numbers.",
                retryable=False,
            )
        expected = tuple(column.name for column in pending.schema.columns)
        if set(frame.columns) != set(expected):
            raise ConnectorError(
                TransferErrorCode.SCHEMA_DRIFT,
                "The source schema changed during extraction.",
                retryable=False,
            )
        pending.frames.append(_clone(frame.select(expected)))
        pending.rows += frame.height
        pending.bytes += batch.byte_count
        pending.last_sequence = batch.sequence
        return BatchWriteResult(rows_acknowledged=frame.height, bytes_acknowledged=batch.byte_count)

    def finalize(self, load_session: LoadSession) -> DestinationManifest:
        pending = self._pending.pop(load_session.staging_name, None)
        if pending is None:
            raise ConnectorError(
                TransferErrorCode.INTERNAL_ERROR, "Destination session is not open."
            )
        loaded, _ = self._backend.commit_postgres(pending)
        return DestinationManifest(locator=load_session.locator, rows=loaded, bytes=0)

    def abort(self, load_session: LoadSession) -> None:
        self._pending.pop(load_session.staging_name, None)


class FakeFoundryConnector:
    def __init__(
        self,
        *,
        provider: str,
        label: str,
        mark: str,
        source: bool,
        files: dict[str, tuple[str, ...]],
        backend: _DemoBackend | None = None,
        settings: Settings | None = None,
    ) -> None:
        self.capabilities = ProviderCapabilities(
            provider=provider,
            label=label,
            technology="Palantir Foundry",
            mark=mark,
            source=source,
            destination=True,
            object_model="dataset RID → branch → file",
            write_modes=("replace",),
            namespaces_label="Dataset",
            objects_label="File",
            writer_enabled=True,
            schema_inspection=False,
            exact_row_counts=False,
            verification_level="local_manifest",
            limitations=_FOUNDRY_LIMITATIONS,
            dataset_creation=True,
        )
        self.settings = settings or get_settings()
        self._backend = backend or _DemoBackend()
        self._backend.register_foundry_templates(provider, files)
        self._pending: dict[str, _PendingLoad] = {}

    @staticmethod
    def _validate(credentials: Mapping[str, str]) -> str:
        _require_credentials(credentials, ("endpoint", "token"), "Foundry")
        return _foundry_connection_id(credentials)

    def test_connection(self, credentials) -> ConnectionHealth:
        connection_id = self._validate(credentials)
        rid = str(credentials.get("dataset_rid") or "")
        if not rid:
            return ConnectionHealth(
                status="untested",
                message="Provide a default dataset RID to exercise the emulator.",
                latency_ms=0,
                server_identity="Data Mover Foundry emulator",
            )
        branch = str(credentials.get("branch") or "master")
        files = self._backend.foundry_files(self.capabilities.provider, connection_id, rid, branch)
        detail = (
            f"branch {branch} · {len(files)} files" if files is not None else "dataset not found"
        )
        return ConnectionHealth(
            status="connected",
            message=f"Emulated connection only · no network request · {detail}.",
            latency_ms=0,
            server_identity="Data Mover Foundry emulator",
        )

    def list_namespaces(self, credentials) -> list[RemoteNamespace]:
        self._validate(credentials)
        rid = str(credentials.get("dataset_rid") or "")
        return [RemoteNamespace(name=rid, display_name=rid, kind="dataset")] if rid else []

    def create_dataset(
        self, credentials, *, parent_folder_rid: str, name: str
    ) -> ProvisionedDataset:
        connection_id = self._validate(credentials)
        branch = "master"
        slug = "-".join(name.casefold().split())[:40] or "dataset"
        dataset_rid = f"ri.foundry.main.dataset.demo-{slug}"
        self._backend.create_foundry_dataset(
            self.capabilities.provider,
            connection_id,
            dataset_rid,
            branch,
        )
        return ProvisionedDataset(
            dataset_rid=dataset_rid,
            name=name,
            parent_folder_rid=parent_folder_rid,
            branch=branch,
        )

    def list_objects(self, credentials, namespace: str, cursor: str | None = None) -> CatalogPage:
        connection_id = self._validate(credentials)
        branch = str(credentials.get("branch") or "master")
        files = self._backend.foundry_files(
            self.capabilities.provider, connection_id, namespace, branch
        )
        if files is None:
            raise ConnectorError(TransferErrorCode.SOURCE_NOT_FOUND, "Select an available dataset.")
        items = []
        for path, frame in sorted(files.items()):
            locator: Locator
            if self.capabilities.source:
                locator = FoundryDatasetFilesLocator(
                    dataset_rid=namespace, branch=branch, file_paths=[path]
                )
            else:
                locator = FoundryUploadLocator(
                    dataset_rid=namespace, branch=branch, file_name=path.rsplit("/", 1)[-1]
                )
            items.append(
                RemoteObject(
                    name=path,
                    display_name=path,
                    locator=locator,
                    size_bytes=_file_size(path, frame),
                    format="parquet" if path.casefold().endswith(".parquet") else "csv",
                )
            )
        return CatalogPage(items=tuple(items))

    def inspect_object(self, credentials, locator: Locator) -> ObjectSchema:
        self._validate(credentials)
        return ObjectSchema(locator=locator, columns=(), estimated_rows=None)

    def count_rows(self, credentials, locator: Locator) -> int | None:
        self._validate(credentials)
        return None

    def extract(
        self, credentials, locator: Locator, *, batch_rows: int, batch_bytes: int
    ) -> Iterator[TransferBatch]:
        connection_id = self._validate(credentials)
        if not isinstance(locator, FoundryDatasetFilesLocator):
            raise ConnectorError(
                TransferErrorCode.SOURCE_NOT_FOUND, "Foundry source locator is invalid."
            )
        files = self._backend.foundry_files(
            self.capabilities.provider,
            connection_id,
            locator.dataset_rid,
            locator.branch,
        )
        if files is None:
            raise ConnectorError(TransferErrorCode.SOURCE_NOT_FOUND, "The dataset was not found.")
        paths = sorted(files) if locator.file_paths == "all_supported" else list(locator.file_paths)
        missing = [path for path in paths if path not in files]
        if missing:
            raise ConnectorError(
                TransferErrorCode.SOURCE_NOT_FOUND,
                "A selected dataset file is no longer available.",
            )
        sequence = 1
        for path in paths:
            frame = files[path]
            if _file_size(path, frame) > self.settings.pipeline_max_source_bytes:
                raise ConnectorError(
                    TransferErrorCode.SOURCE_LIMIT_EXCEEDED,
                    "The dataset file exceeds the configured source size limit.",
                )
            batches = tuple(
                bounded_frame_batches(
                    frame,
                    batch_rows=batch_rows,
                    batch_bytes=batch_bytes,
                    sequence_start=sequence,
                )
            )
            yield from batches
            sequence += len(batches)
        if not paths:
            raise ConnectorError(
                TransferErrorCode.SOURCE_NOT_FOUND,
                "The dataset has no CSV or Parquet files.",
            )

    def prepare_destination(
        self,
        credentials,
        locator: Locator,
        schema: ObjectSchema,
        write_policy: WritePolicy,
        *,
        run_id: str,
    ) -> LoadSession:
        connection_id = self._validate(credentials)
        if not isinstance(locator, FoundryUploadLocator):
            raise ConnectorError(
                TransferErrorCode.DESTINATION_NOT_FOUND,
                "Foundry destination locator is invalid.",
                retryable=False,
            )
        if not isinstance(write_policy, FoundryReplaceFilePolicy):
            raise ConnectorError(
                TransferErrorCode.UNSUPPORTED_TYPE,
                "Foundry destinations support replace-file writes only.",
                retryable=False,
            )
        session_id = f"demo-foundry-{run_id}"
        self._pending[session_id] = _PendingLoad(
            connection_id=connection_id,
            locator=locator,
            schema=schema,
            write_policy=write_policy,
        )
        return LoadSession(
            locator=locator,
            write_policy=write_policy,
            staging_name=session_id,
            columns=tuple(column.name for column in schema.columns),
        )

    def write_batch(self, load_session: LoadSession, batch: TransferBatch) -> BatchWriteResult:
        pending = self._pending.get(load_session.staging_name)
        if pending is None:
            raise ConnectorError(
                TransferErrorCode.INTERNAL_ERROR, "Destination session is not open."
            )
        frame = _validate_batch(batch)
        if batch.sequence <= pending.last_sequence:
            raise ConnectorError(
                TransferErrorCode.PARTIAL_WRITE,
                "Destination batches must have strictly increasing sequence numbers.",
                retryable=False,
            )
        expected = tuple(column.name for column in pending.schema.columns)
        if expected and set(frame.columns) != set(expected):
            raise ConnectorError(
                TransferErrorCode.SCHEMA_DRIFT,
                "The source schema changed during extraction.",
                retryable=False,
            )
        pending.frames.append(_clone(frame.select(expected) if expected else frame))
        pending.rows += frame.height
        pending.bytes += batch.byte_count
        previous_sequence = pending.last_sequence
        pending.last_sequence = batch.sequence
        staged = pl.concat(pending.frames, how="vertical_relaxed")
        if _parquet_size(staged) > self.settings.pipeline_max_spool_bytes:
            pending.frames.pop()
            pending.rows -= frame.height
            pending.bytes -= batch.byte_count
            pending.last_sequence = previous_sequence
            raise ConnectorError(
                TransferErrorCode.SPOOL_LIMIT_EXCEEDED,
                "The destination staging data exceeds the configured spool size limit.",
                retryable=False,
            )
        return BatchWriteResult(rows_acknowledged=frame.height, bytes_acknowledged=batch.byte_count)

    def finalize(self, load_session: LoadSession) -> DestinationManifest:
        pending = self._pending.pop(load_session.staging_name, None)
        if pending is None:
            raise ConnectorError(
                TransferErrorCode.INTERNAL_ERROR, "Destination session is not open."
            )
        if not pending.frames:
            raise ConnectorError(TransferErrorCode.PARTIAL_WRITE, "No Parquet spool was produced.")
        assert isinstance(pending.locator, FoundryUploadLocator)
        frame = pl.concat(pending.frames, how="vertical_relaxed")
        size = _parquet_size(frame)
        self._backend.put_foundry(
            self.capabilities.provider, pending.connection_id, pending.locator, frame
        )
        return DestinationManifest(
            locator=pending.locator,
            rows=frame.height,
            bytes=size,
            remote_id=pending.locator.file_name,
            details={"publication": pending.locator.publication},
        )

    def abort(self, load_session: LoadSession) -> None:
        self._pending.pop(load_session.staging_name, None)


class FakeCsvConnector(CsvSourceConnector):
    """Demo alias for the real local CSV adapter; no synthetic fallback data."""


def register() -> None:
    backend = _DemoBackend()
    register_connector(lambda: FakePostgresConnector(backend))
    register_connector(
        lambda: FakeFoundryConnector(
            provider="mss",
            label="MSS",
            mark="MSS",
            source=True,
            files=MSS_FILES,
            backend=backend,
        )
    )
    register_connector(
        lambda: FakeFoundryConnector(
            provider="mcscop",
            label="MCS-COP",
            mark="MCS",
            source=False,
            files=MCSCOP_FILES,
            backend=backend,
        )
    )
    register_connector(FakeCsvConnector)
