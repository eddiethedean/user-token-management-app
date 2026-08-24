"""Pure helpers for pipeline metadata provenance and schema comparisons."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def provenance_label(value: str | None) -> str:
    return {
        "exact": "Exact",
        "estimated": "Estimated",
        "captured": "Captured during run",
        "catalog": "Catalog metadata",
        "unavailable": "Unavailable",
        "provider_unavailable": "Provider does not expose this fact",
        "local_manifest": "Local manifest",
    }.get(value or "unavailable", "Unavailable")


def schema_diff(
    source_manifest: Mapping[str, Any] | None,
    destination_manifest: Mapping[str, Any] | None,
) -> list[dict[str, str]]:
    """Return a deterministic, display-ready comparison of two schema manifests."""

    source_schema = source_manifest.get("schema", {}) if source_manifest else {}
    destination_schema = destination_manifest.get("schema", {}) if destination_manifest else {}
    source_columns = {
        str(item.get("name")): item
        for item in source_schema.get("columns", [])
        if isinstance(item, Mapping) and item.get("name")
    }
    destination_columns = {
        str(item.get("name")): item
        for item in destination_schema.get("columns", [])
        if isinstance(item, Mapping) and item.get("name")
    }
    rows: list[dict[str, str]] = []
    for name in sorted(source_columns.keys() | destination_columns.keys()):
        source = source_columns.get(name)
        destination = destination_columns.get(name)
        if source is None:
            status = "extra_destination"
        elif destination is None:
            status = "missing_destination"
        elif str(source.get("data_type", "")) != str(destination.get("data_type", "")) or bool(
            source.get("nullable", True)
        ) != bool(destination.get("nullable", True)):
            status = "changed"
        else:
            status = "match"
        rows.append(
            {
                "name": name,
                "status": status,
                "source_type": str(source.get("data_type", "—")) if source else "—",
                "destination_type": str(destination.get("data_type", "—")) if destination else "—",
                "source_nullable": "Nullable"
                if source and source.get("nullable", True)
                else "Required",
                "destination_nullable": (
                    "Nullable" if destination and destination.get("nullable", True) else "Required"
                ),
            }
        )
    return rows


def manifest_metadata(
    *,
    rows: int | None,
    schema_available: bool,
    row_provenance: str,
    schema_provenance: str,
) -> dict[str, Any]:
    """Build a redaction-safe metadata summary persisted with a run manifest."""

    return {
        "rows": {"value": rows, "provenance": row_provenance},
        "schema": {
            "available": schema_available,
            "provenance": schema_provenance,
        },
    }


__all__ = ["manifest_metadata", "provenance_label", "schema_diff"]
