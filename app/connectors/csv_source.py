"""Polars-backed CSV source connector."""

from __future__ import annotations

from collections.abc import Iterator
from io import BytesIO

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
    TransferBatch,
)
from app.connectors.errors import ConnectorError, TransferErrorCode
from app.connectors.locators import CsvUploadLocator, Locator, WritePolicy
from app.connectors.registry import register_connector


class CsvSourceConnector:
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
        exact_row_counts=False,
        verification_level="local_manifest",
        limitations=("CSV metadata is available only after the upload is scanned.",),
    )
    content_lookup = None

    def test_connection(self, credentials) -> ConnectionHealth:
        return ConnectionHealth(
            status="connected", message="Local CSV source is ready.", latency_ms=0
        )

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

    def count_rows(self, credentials, locator: Locator) -> int | None:
        return None

    def extract(
        self, credentials, locator: Locator, *, batch_rows: int, batch_bytes: int
    ) -> Iterator[TransferBatch]:
        frame = self._frame(locator, credentials)
        start = 0
        sequence = 1
        while start < frame.height:
            slc = frame.slice(start, batch_rows)
            yield TransferBatch(
                frame=slc,
                row_count=slc.height,
                byte_count=int(slc.estimated_size()),
                sequence=sequence,
            )
            start += batch_rows
            sequence += 1

    def prepare_destination(
        self, credentials, locator, schema, write_policy: WritePolicy, *, run_id: str
    ):
        raise ConnectorError(TransferErrorCode.INTERNAL_ERROR, "CSV cannot be a destination.")

    def write_batch(self, load_session: LoadSession, batch: TransferBatch) -> BatchWriteResult:
        raise ConnectorError(TransferErrorCode.INTERNAL_ERROR, "CSV cannot be a destination.")

    def finalize(self, load_session: LoadSession) -> DestinationManifest:
        raise ConnectorError(TransferErrorCode.INTERNAL_ERROR, "CSV cannot be a destination.")

    def abort(self, load_session: LoadSession) -> None:
        return None

    def _frame(self, locator: Locator, credentials) -> pl.DataFrame:
        if not isinstance(locator, CsvUploadLocator):
            raise ConnectorError(TransferErrorCode.SOURCE_NOT_FOUND, "CSV locator is invalid.")
        content = (credentials or {}).get("content")
        if not content and self.content_lookup is not None:
            content = self.content_lookup(locator.upload_id, locator.checksum_sha256)
        if not content:
            raise ConnectorError(
                TransferErrorCode.SOURCE_NOT_FOUND, "The CSV upload is no longer available."
            )
        payload = content.encode("utf-8") if isinstance(content, str) else content
        try:
            return pl.read_csv(BytesIO(payload), infer_schema_length=10_000)
        except Exception as exc:
            raise ConnectorError(
                TransferErrorCode.UNSUPPORTED_TYPE, "The CSV file could not be parsed."
            ) from exc


def register() -> None:
    register_connector(CsvSourceConnector)
