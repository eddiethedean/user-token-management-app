"""Polars-backed CSV source connector."""

from __future__ import annotations

import csv
import json
from collections.abc import Iterator
from io import BytesIO, StringIO

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
    TransferBatch,
    bounded_frame_batches,
)
from app.connectors.errors import ConnectorError, TransferErrorCode
from app.connectors.locators import CsvUploadLocator, Locator, WritePolicy
from app.connectors.registry import register_connector
from app.domain.csv_inference import CsvColumnProfile


def profiled_polars_type(column: CsvColumnProfile) -> pl.DataType | type[pl.DataType]:
    """Use the reviewed CSV profile when parsing source values."""

    inferred_type = column.inferred_type
    if inferred_type in {"text", "empty"}:
        return pl.String
    if inferred_type == "boolean":
        return pl.Boolean
    if inferred_type == "integer":
        return pl.Int64
    if inferred_type == "date":
        return pl.Date
    if inferred_type == "time":
        return pl.Time
    if inferred_type == "datetime":
        return pl.Datetime("us", time_zone="UTC" if column.timezone_aware else None)
    if inferred_type == "decimal":
        if column.decimal_precision < 1:
            raise ConnectorError(
                TransferErrorCode.UNSUPPORTED_TYPE,
                "The CSV decimal precision could not be determined safely.",
                retryable=False,
            )
        if column.decimal_precision > 38:
            raise ConnectorError(
                TransferErrorCode.UNSUPPORTED_TYPE,
                "A CSV decimal exceeds the supported precision of 38 digits.",
                retryable=False,
            )
        return pl.Decimal(precision=column.decimal_precision, scale=column.decimal_scale)
    raise ConnectorError(
        TransferErrorCode.UNSUPPORTED_TYPE,
        "The CSV profile contains an unsupported inferred type.",
        retryable=False,
    )


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
        yield from bounded_frame_batches(frame, batch_rows=batch_rows, batch_bytes=batch_bytes)

    def prepare_destination(
        self, credentials, locator, schema, write_policy: WritePolicy, *, run_id: str
    ):
        raise ConnectorError(TransferErrorCode.INTERNAL_ERROR, "CSV cannot be a destination.")

    def write_batch(self, load_session: LoadSession, batch: TransferBatch) -> BatchWriteResult:
        raise ConnectorError(TransferErrorCode.INTERNAL_ERROR, "CSV cannot be a destination.")

    def finalize(self, load_session: LoadSession) -> DestinationManifest:
        raise ConnectorError(TransferErrorCode.INTERNAL_ERROR, "CSV cannot be a destination.")

    def abort(self, load_session: LoadSession) -> AbortResult:
        return AbortResult.ROLLED_BACK

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
        separator = str((credentials or {}).get("delimiter") or ",")[:1]
        quote_char = str((credentials or {}).get("quote_char") or '"')[:1]
        columns = _metadata_list((credentials or {}).get("columns"))
        column_types = _metadata_list((credentials or {}).get("column_types"))
        column_timezones = _metadata_bool_list((credentials or {}).get("column_timezones"))
        schema_overrides = None
        if columns and len(columns) == len(column_types):
            decimal_specs = _metadata_decimal_specs(
                (credentials or {}).get("column_decimal_specs"), column_types
            )
            schema_overrides = {}
            for index, (name, inferred_type) in enumerate(zip(columns, column_types, strict=True)):
                precision, scale = decimal_specs[index]
                schema_overrides[name] = profiled_polars_type(
                    CsvColumnProfile(
                        name=name,
                        inferred_type=inferred_type,
                        populated=0,
                        nulls=0,
                        example="",
                        decimal_precision=precision,
                        decimal_scale=scale,
                        timezone_aware=(
                            len(column_timezones) == len(columns) and column_timezones[index]
                        ),
                    )
                )
        try:
            typed_columns = (
                {
                    index: inferred_type
                    for index, inferred_type in enumerate(column_types)
                    if inferred_type not in {"text", "empty"}
                }
                if schema_overrides is not None
                else {}
            )
            if typed_columns:
                payload = _normalize_profiled_cells(payload, separator, quote_char, typed_columns)
            return pl.read_csv(
                BytesIO(payload),
                infer_schema_length=None,
                separator=separator,
                quote_char='"' if typed_columns else quote_char,
                new_columns=columns or None,
                schema_overrides=schema_overrides,
                try_parse_dates=True,
            )
        except Exception as exc:
            raise ConnectorError(
                TransferErrorCode.UNSUPPORTED_TYPE, "The CSV file could not be parsed."
            ) from exc


def _metadata_list(value) -> list[str]:
    if not value:
        return []
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except (TypeError, ValueError):
            return []
    else:
        decoded = value
    if not isinstance(decoded, list) or not all(isinstance(item, str) for item in decoded):
        return []
    return decoded


def _metadata_bool_list(value) -> list[bool]:
    if not value:
        return []
    try:
        decoded = json.loads(value) if isinstance(value, str) else value
    except (TypeError, ValueError):
        return []
    if not isinstance(decoded, list) or not all(isinstance(item, bool) for item in decoded):
        return []
    return decoded


def _normalize_profiled_cells(
    payload: bytes, separator: str, quote_char: str, typed_columns: dict[int, str]
) -> bytes:
    """Keep parser input consistent with the profiler's whitespace rules."""

    text = payload.decode("utf-8-sig")
    reader = csv.reader(StringIO(text, newline=""), delimiter=separator, quotechar=quote_char)
    output = StringIO(newline="")
    writer = csv.writer(output, delimiter=separator, lineterminator="\n")
    for row in reader:
        for index, inferred_type in typed_columns.items():
            if index < len(row):
                value = row[index].strip()
                row[index] = value.casefold() if inferred_type == "boolean" else value
        writer.writerow(row)
    return output.getvalue().encode("utf-8")


def _metadata_decimal_specs(value, column_types: list[str]) -> list[tuple[int, int]]:
    column_count = len(column_types)
    if not value:
        return [(0, 0)] * column_count
    try:
        decoded = json.loads(value) if isinstance(value, str) else value
    except (TypeError, ValueError):
        decoded = None
    if not isinstance(decoded, list) or len(decoded) != column_count:
        return [(0, 0)] * column_count
    specs: list[tuple[int, int]] = []
    for inferred_type, item in zip(column_types, decoded, strict=True):
        if inferred_type != "decimal":
            specs.append((0, 0))
            continue
        if not isinstance(item, dict):
            return [(0, 0)] * column_count
        try:
            precision = int(item.get("precision", 0))
            scale = int(item.get("scale", 0))
        except (TypeError, ValueError):
            return [(0, 0)] * column_count
        if precision < 1 or scale < 0 or scale > precision:
            return [(0, 0)] * column_count
        specs.append((precision, scale))
    return specs


def register() -> None:
    register_connector(CsvSourceConnector)
