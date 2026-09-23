"""Apply saved, user-selected Polars casts to bounded pipeline batches."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

import polars as pl

from app.connectors.base import ColumnSchema, ObjectSchema, TransferBatch
from app.connectors.errors import ConnectorError, TransferErrorCode
from app.domain.column_types import COLUMN_TYPE_OVERRIDE_DATA_TYPES

_POLARS_CAST_TYPES = {
    "text": pl.String,
    "boolean": pl.Boolean,
    "smallint": pl.Int16,
    "integer": pl.Int32,
    "bigint": pl.Int64,
    "real": pl.Float32,
    "double": pl.Float64,
    "date": pl.Date,
    "timestamp": pl.Datetime("us"),
    "time": pl.Time,
    "interval": pl.Duration("us"),
    "binary": pl.Binary,
}


def apply_column_type_overrides_to_schema(
    schema: ObjectSchema, overrides: dict[str, str]
) -> ObjectSchema:
    if not overrides:
        return schema
    columns_by_name = {column.name: column for column in schema.columns}
    unknown_columns = set(overrides) - columns_by_name.keys()
    if unknown_columns:
        raise ConnectorError(
            TransferErrorCode.SCHEMA_DRIFT,
            "A saved cast refers to a source column that is no longer available.",
            retryable=False,
        )
    columns = tuple(
        ColumnSchema(
            name=column.name,
            data_type=COLUMN_TYPE_OVERRIDE_DATA_TYPES[overrides[column.name]],
            nullable=column.nullable,
            example=column.example,
        )
        if column.name in overrides
        else column
        for column in schema.columns
    )
    return ObjectSchema(
        locator=schema.locator,
        columns=columns,
        primary_key=schema.primary_key,
        unique_constraints=schema.unique_constraints,
        estimated_rows=schema.estimated_rows,
    )


def cast_batch_columns(batch: TransferBatch, overrides: dict[str, str]) -> TransferBatch:
    if not overrides:
        return batch
    try:
        frame = batch.frame.with_columns(
            [
                pl.col(column)
                .cast(
                    _decimal_dtype(batch.frame[column])
                    if selected_type == "decimal"
                    else _POLARS_CAST_TYPES[selected_type],
                    strict=True,
                )
                .alias(column)
                for column, selected_type in overrides.items()
            ]
        )
    except pl.exceptions.PolarsError as exc:
        failing_column = next(
            (
                column
                for column, selected_type in overrides.items()
                if _column_cast_fails(batch.frame, column, selected_type)
            ),
            None,
        )
        if failing_column is None:
            raise ConnectorError(
                TransferErrorCode.SCHEMA_DRIFT,
                "The source batch could not be cast to the selected column types.",
                retryable=False,
            ) from exc
        raise ConnectorError(
            TransferErrorCode.SCHEMA_DRIFT,
            f"Source column '{failing_column}' contains values that cannot be cast to the selected type.",
            retryable=False,
        ) from exc
    for column, selected_type in overrides.items():
        if selected_type in {"smallint", "integer", "bigint", "decimal"} and not all(
            _same_numeric_value(before, after)
            for before, after in zip(batch.frame[column], frame[column], strict=True)
        ):
            raise ConnectorError(
                TransferErrorCode.SCHEMA_DRIFT,
                f"Source column '{column}' would lose numeric precision with the selected cast.",
                retryable=False,
            )
    return TransferBatch(
        frame=frame,
        row_count=batch.row_count,
        byte_count=int(frame.estimated_size()),
        sequence=batch.sequence,
    )


def _column_cast_fails(frame: pl.DataFrame, column: str, selected_type: str) -> bool:
    try:
        dtype = (
            _decimal_dtype(frame[column])
            if selected_type == "decimal"
            else _POLARS_CAST_TYPES[selected_type]
        )
        frame.select(pl.col(column).cast(dtype, strict=True))
    except pl.exceptions.PolarsError:
        return True
    return False


def _decimal_dtype(series: pl.Series) -> pl.Decimal:
    scale = 0
    try:
        for value in series:
            if value is None:
                continue
            number = Decimal(int(value)) if isinstance(value, bool) else Decimal(str(value))
            if not number.is_finite():
                raise ValueError
            scale = max(scale, max(-int(number.as_tuple().exponent), 0))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ConnectorError(
            TransferErrorCode.SCHEMA_DRIFT,
            f"Source column '{series.name}' contains values that cannot be cast to Decimal.",
            retryable=False,
        ) from exc
    if scale > 38:
        raise ConnectorError(
            TransferErrorCode.SCHEMA_DRIFT,
            f"Source column '{series.name}' exceeds Polars' supported Decimal scale.",
            retryable=False,
        )
    return pl.Decimal(precision=38, scale=scale)


def _same_numeric_value(before: object, after: object) -> bool:
    if before is None:
        return after is None
    if after is None:
        return False
    try:
        before_decimal = Decimal(int(before)) if isinstance(before, bool) else Decimal(str(before))
        after_decimal = Decimal(str(after))
    except (InvalidOperation, TypeError, ValueError):
        return False
    return (
        before_decimal.is_finite() and after_decimal.is_finite() and before_decimal == after_decimal
    )
