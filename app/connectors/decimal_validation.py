"""Shared checks for lossless decimal transfers into existing destinations."""

from __future__ import annotations

import re
from collections.abc import Iterable

from app.connectors.base import ColumnSchema
from app.connectors.errors import ConnectorError, TransferErrorCode

_DECIMAL = re.compile(
    r"decimal\(precision=(?P<precision>\d+|None),\s*scale=(?P<scale>\d+|None)\)",
    re.IGNORECASE,
)


def decimal_shape(data_type: str) -> tuple[int, int] | None:
    """Return precision and scale for a bounded Polars Decimal type."""

    match = _DECIMAL.fullmatch(data_type.casefold())
    if (
        match is None
        or match.group("precision").casefold() == "none"
        or match.group("scale").casefold() == "none"
    ):
        return None
    return int(match.group("precision")), int(match.group("scale"))


def _has_unbounded_decimal_shape(data_type: str) -> bool:
    match = _DECIMAL.fullmatch(data_type.casefold())
    return match is not None and (
        match.group("precision").casefold() == "none" or match.group("scale").casefold() == "none"
    )


def validate_decimal_destination_schema(
    source_columns: Iterable[ColumnSchema], destination_columns: Iterable[ColumnSchema]
) -> None:
    """Reject existing destination columns that cannot store source decimals exactly."""

    source_columns = tuple(source_columns)
    source_decimals = {
        column.name: shape
        for column in source_columns
        if (shape := decimal_shape(column.data_type)) is not None
    }
    source_unbounded_decimals = {
        column.name for column in source_columns if _has_unbounded_decimal_shape(column.data_type)
    }
    if not source_decimals and not source_unbounded_decimals:
        return

    destinations = {column.name: column.data_type.casefold() for column in destination_columns}
    for name in source_unbounded_decimals:
        destination_type = destinations.get(name)
        if destination_type is None:
            continue
        destination_shape = decimal_shape(destination_type)
        if destination_type in {"numeric", "decimal"} and destination_shape is None:
            continue
        if destination_type in {"text", "character varying", "character"}:
            continue
        raise ConnectorError(
            TransferErrorCode.SCHEMA_DRIFT,
            f"Destination column '{name}' cannot safely store the selected decimal cast without precision loss.",
            retryable=False,
        )

    for name, (source_precision, source_scale) in source_decimals.items():
        destination_type = destinations.get(name)
        if destination_type is None:
            continue
        destination_shape = decimal_shape(destination_type)
        if destination_type in {"numeric", "decimal"}:
            continue
        if destination_shape is not None:
            destination_precision, destination_scale = destination_shape
            if (
                destination_scale < source_scale
                or destination_precision - destination_scale < source_precision - source_scale
            ):
                raise ConnectorError(
                    TransferErrorCode.SCHEMA_DRIFT,
                    f"Destination column '{name}' cannot store the source decimal without rounding.",
                    retryable=False,
                )
            continue
        if destination_type in {"text", "character varying", "character"}:
            continue
        if (
            destination_type in {"smallint", "integer", "bigint"}
            and source_scale == 0
            and source_precision <= {"smallint": 4, "integer": 9, "bigint": 18}[destination_type]
        ):
            continue
        raise ConnectorError(
            TransferErrorCode.SCHEMA_DRIFT,
            f"Destination column '{name}' cannot store the source decimal without loss.",
            retryable=False,
        )
