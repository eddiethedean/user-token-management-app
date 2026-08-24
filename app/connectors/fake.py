"""Demo/test connectors that implement the same ports as real adapters."""

from __future__ import annotations

from collections.abc import Iterator

import polars as pl

from app.connectors.base import (
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
)
from app.connectors.errors import ConnectorError, TransferErrorCode
from app.connectors.locators import (
    CsvUploadLocator,
    FoundryDatasetFilesLocator,
    FoundryUploadLocator,
    Locator,
    PostgresTableLocator,
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


def _demo_frame(rows: int = 3) -> pl.DataFrame:
    return pl.DataFrame(
        {
            "event_id": list(range(1, rows + 1)),
            "unit_name": [f"Unit-{index}" for index in range(1, rows + 1)],
            "ready": [True, False, True][:rows],
            "score": [98.4, 82.0, 91.2][:rows],
        }
    )


class _BaseFake:
    def test_connection(self, credentials) -> ConnectionHealth:
        if not credentials:
            raise ConnectorError(
                TransferErrorCode.CREDENTIALS_MISSING, "Configure the connection first."
            )
        return ConnectionHealth(
            status="connected", message="Demo handshake succeeded.", latency_ms=12
        )

    def abort(self, load_session: LoadSession) -> None:
        return None


class FakePostgresConnector(_BaseFake):
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

    def __init__(self) -> None:
        self._row_count = 0

    def list_namespaces(self, credentials) -> list[RemoteNamespace]:
        return [
            RemoteNamespace(name=name, display_name=name, kind="schema")
            for name in POSTGRES_SCHEMAS
        ]

    def list_objects(self, credentials, namespace: str, cursor: str | None = None) -> CatalogPage:
        tables = POSTGRES_SCHEMAS.get(namespace)
        if tables is None:
            raise ConnectorError(TransferErrorCode.SOURCE_NOT_FOUND, "Select an available schema.")
        items = tuple(
            RemoteObject(
                name=table,
                display_name=table,
                locator=postgres_table(namespace, table),
                estimated_rows=1200,
            )
            for table in tables
        )
        return CatalogPage(items=items)

    def inspect_object(self, credentials, locator: Locator) -> ObjectSchema:
        if not isinstance(locator, PostgresTableLocator):
            raise ConnectorError(
                TransferErrorCode.SOURCE_NOT_FOUND, "PostgreSQL locator is invalid."
            )
        frame = _demo_frame()
        return ObjectSchema(
            locator=locator,
            columns=tuple(
                ColumnSchema(name=name, data_type=str(dtype), nullable=True)
                for name, dtype in frame.schema.items()
            ),
            primary_key=("event_id",),
            unique_constraints=(("event_id",),),
            estimated_rows=3,
        )

    def count_rows(self, credentials, locator: Locator) -> int | None:
        return self._row_count

    def extract(self, credentials, locator: Locator, *, batch_rows: int, batch_bytes: int):
        frame = _demo_frame()
        yield TransferBatch(
            frame=frame, row_count=frame.height, byte_count=int(frame.estimated_size()), sequence=1
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
        return LoadSession(
            locator=locator,
            write_policy=write_policy,
            staging_name=f"dm_stage_{run_id[:8]}",
            columns=tuple(column.name for column in schema.columns),
        )

    def write_batch(self, load_session: LoadSession, batch: TransferBatch) -> BatchWriteResult:
        return BatchWriteResult(
            rows_acknowledged=batch.row_count, bytes_acknowledged=batch.byte_count
        )

    def finalize(self, load_session: LoadSession) -> DestinationManifest:
        self._row_count = 3
        return DestinationManifest(
            locator=load_session.locator, rows=3, bytes=128, remote_id="demo-pg"
        )


class FakeFoundryConnector(_BaseFake):
    def __init__(
        self,
        *,
        provider: str,
        label: str,
        mark: str,
        source: bool,
        files: dict[str, tuple[str, ...]],
    ):
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
        )
        self._files = files
        self._row_count = 0

    def list_namespaces(self, credentials) -> list[RemoteNamespace]:
        return [RemoteNamespace(name=rid, display_name=rid, kind="dataset") for rid in self._files]

    def list_objects(self, credentials, namespace: str, cursor: str | None = None) -> CatalogPage:
        files = self._files.get(namespace)
        if files is None:
            raise ConnectorError(TransferErrorCode.SOURCE_NOT_FOUND, "Select an available dataset.")
        items = []
        for path in files:
            if self.capabilities.source:
                locator: Locator = FoundryDatasetFilesLocator(
                    dataset_rid=namespace, branch="master", file_paths=[path]
                )
            else:
                locator = FoundryUploadLocator(
                    dataset_rid=namespace, branch="master", file_name=path
                )
            items.append(
                RemoteObject(
                    name=path,
                    display_name=path,
                    locator=locator,
                    size_bytes=2048,
                    format="parquet" if path.endswith(".parquet") else "csv",
                )
            )
        return CatalogPage(items=tuple(items))

    def inspect_object(self, credentials, locator: Locator) -> ObjectSchema:
        frame = _demo_frame()
        return ObjectSchema(
            locator=locator,
            columns=tuple(
                ColumnSchema(name=name, data_type=str(dtype), nullable=True)
                for name, dtype in frame.schema.items()
            ),
            estimated_rows=3,
        )

    def count_rows(self, credentials, locator: Locator) -> int | None:
        return self._row_count

    def extract(self, credentials, locator: Locator, *, batch_rows: int, batch_bytes: int):
        frame = _demo_frame()
        yield TransferBatch(
            frame=frame, row_count=frame.height, byte_count=int(frame.estimated_size()), sequence=1
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
        return LoadSession(locator=locator, write_policy=write_policy, staging_name=run_id[:8])

    def write_batch(self, load_session: LoadSession, batch: TransferBatch) -> BatchWriteResult:
        return BatchWriteResult(
            rows_acknowledged=batch.row_count, bytes_acknowledged=batch.byte_count
        )

    def finalize(self, load_session: LoadSession) -> DestinationManifest:
        self._row_count = 3
        name = getattr(load_session.locator, "file_name", "demo.parquet")
        return DestinationManifest(
            locator=load_session.locator, rows=3, bytes=128, remote_id=str(name), checksum="demo"
        )


class FakeCsvConnector(_BaseFake):
    capabilities = ProviderCapabilities(
        provider="csv",
        label="CSV file",
        technology="Delimited file",
        mark="CSV",
        source=True,
        destination=False,
        object_model="uploaded file",
        write_modes=(),
        namespaces_label="Upload",
        objects_label="File",
        writer_enabled=False,
    )
    content_lookup = None

    def count_rows(self, credentials, locator: Locator) -> int | None:
        return None

    def list_namespaces(self, credentials) -> list[RemoteNamespace]:
        return [RemoteNamespace(name="uploaded", display_name="Uploaded files", kind="upload")]

    def list_objects(self, credentials, namespace: str, cursor: str | None = None) -> CatalogPage:
        return CatalogPage(items=())

    def inspect_object(self, credentials, locator: Locator) -> ObjectSchema:
        frame = self._frame(locator, credentials)
        return ObjectSchema(
            locator=locator,
            columns=tuple(
                ColumnSchema(name=name, data_type=str(dtype), nullable=True)
                for name, dtype in frame.schema.items()
            ),
            estimated_rows=frame.height,
        )

    def extract(
        self, credentials, locator: Locator, *, batch_rows: int, batch_bytes: int
    ) -> Iterator[TransferBatch]:
        frame = self._frame(locator, credentials)
        yield TransferBatch(
            frame=frame, row_count=frame.height, byte_count=int(frame.estimated_size()), sequence=1
        )

    def prepare_destination(
        self, credentials, locator, schema, write_policy, *, run_id: str
    ) -> LoadSession:
        raise ConnectorError(TransferErrorCode.INTERNAL_ERROR, "CSV cannot be a destination.")

    def write_batch(self, load_session: LoadSession, batch: TransferBatch) -> BatchWriteResult:
        raise ConnectorError(TransferErrorCode.INTERNAL_ERROR, "CSV cannot be a destination.")

    def finalize(self, load_session: LoadSession) -> DestinationManifest:
        raise ConnectorError(TransferErrorCode.INTERNAL_ERROR, "CSV cannot be a destination.")

    def _frame(self, locator: Locator, credentials):
        from io import BytesIO

        import polars as pl

        if isinstance(locator, CsvUploadLocator) and self.content_lookup is not None:
            content = self.content_lookup(locator.upload_id, locator.checksum_sha256)
            if content:
                payload = content.encode("utf-8") if isinstance(content, str) else content
                return pl.read_csv(BytesIO(payload), infer_schema_length=10_000)
        return _demo_frame()


def register() -> None:
    register_connector(FakePostgresConnector)
    register_connector(
        lambda: FakeFoundryConnector(
            provider="mss", label="MSS", mark="MSS", source=True, files=MSS_FILES
        )
    )
    register_connector(
        lambda: FakeFoundryConnector(
            provider="mcscop", label="MCS-COP", mark="MCS", source=False, files=MCSCOP_FILES
        )
    )
    register_connector(FakeCsvConnector)
