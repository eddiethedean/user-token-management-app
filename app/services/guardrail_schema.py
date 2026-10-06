"""Schema projection shared by run readiness and guarded transfer execution."""

from __future__ import annotations

from collections.abc import Mapping

from app.connectors.base import ColumnSchema, ObjectSchema
from app.connectors.errors import ConnectorError, TransferErrorCode


def project_schema_for_guardrails(schema: ObjectSchema, actions: Mapping[str, str]) -> ObjectSchema:
    """Return the schema that remains after the selected guardrail actions."""

    removed = {name for name, action in actions.items() if action == "remove"}
    transformed_columns = tuple(
        ColumnSchema(
            name=column.name,
            data_type="String" if actions.get(column.name) == "hash" else column.data_type,
            nullable=column.nullable,
            sensitivity_markers=column.sensitivity_markers,
        )
        for column in schema.columns
        if column.name not in removed
    )
    if schema.columns and not transformed_columns:
        raise ConnectorError(
            TransferErrorCode.SCHEMA_DRIFT,
            "Sensitive-data actions would remove every source column.",
            retryable=False,
        )
    return ObjectSchema(
        locator=schema.locator,
        columns=transformed_columns,
        primary_key=tuple(name for name in schema.primary_key if name not in removed),
        unique_constraints=tuple(
            constraint
            for constraint in schema.unique_constraints
            if not removed.intersection(constraint)
        ),
        estimated_rows=schema.estimated_rows,
        sensitivity_markers=schema.sensitivity_markers,
        column_sensitivity_markers=schema.column_sensitivity_markers,
        removed_columns=tuple(sorted(set(schema.removed_columns) | removed)),
    )
