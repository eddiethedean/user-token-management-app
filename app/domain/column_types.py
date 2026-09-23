"""Stable column-cast choices shared by pipeline authoring and execution."""

from __future__ import annotations

import json
from collections.abc import Sequence

COLUMN_TYPE_CHOICES = (
    ("auto", "Use detected type"),
    ("text", "Text"),
    ("boolean", "Boolean"),
    ("smallint", "Small integer"),
    ("integer", "Integer"),
    ("bigint", "Big integer"),
    ("real", "Float (32-bit)"),
    ("double", "Float (64-bit)"),
    ("decimal", "Decimal"),
    ("date", "Date"),
    ("timestamp", "Timestamp"),
    ("time", "Time"),
    ("interval", "Interval / duration"),
    ("binary", "Binary"),
)

COLUMN_TYPE_OVERRIDE_DATA_TYPES = {
    "text": "String",
    "boolean": "Boolean",
    "smallint": "Int16",
    "integer": "Int32",
    "bigint": "Int64",
    "real": "Float32",
    "double": "Float64",
    "decimal": "Decimal(precision=None, scale=None)",
    "date": "Date",
    "timestamp": "Datetime(time_unit='us')",
    "time": "Time",
    "interval": "Duration(time_unit='us')",
    "binary": "Binary",
}

_ALLOWED_FORM_TYPES = frozenset({"auto", *COLUMN_TYPE_OVERRIDE_DATA_TYPES})


def parse_column_type_override_values(values: Sequence[str]) -> dict[str, str]:
    """Decode repeated schema-select values into a validated override map."""

    overrides: dict[str, str] = {}
    seen: set[str] = set()
    for raw_value in values:
        try:
            payload = json.loads(raw_value)
        except (TypeError, ValueError) as exc:
            raise ValueError("A column type selection is invalid.") from exc
        if (
            not isinstance(payload, list)
            or len(payload) != 2
            or not all(isinstance(item, str) for item in payload)
        ):
            raise ValueError("A column type selection is invalid.")
        column, selected_type = payload
        if not column or selected_type not in _ALLOWED_FORM_TYPES:
            raise ValueError("A column type selection is invalid.")
        if column in seen:
            raise ValueError("A column can have only one cast type.")
        seen.add(column)
        if selected_type != "auto":
            if len(column) > 256:
                raise ValueError("Cast column names must be 256 characters or fewer.")
            overrides[column] = selected_type
    return overrides
