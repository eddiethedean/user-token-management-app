"""Provider catalogs sourced from the connector registry."""

from __future__ import annotations

from dataclasses import dataclass

from app.config import get_settings
from app.connectors.base import ProviderCapabilities
from app.connectors.registry import capabilities_for, listed_capabilities, load_builtin_connectors

CREATE_TABLE_VALUE = "__new__"
NEW_TABLE_VALUE_PREFIX = f"{CREATE_TABLE_VALUE}:"


@dataclass(frozen=True)
class ProviderCatalog:
    name: str
    label: str
    technology: str
    mark: str
    region: str
    namespaces_label: str
    objects_label: str
    source: bool
    destination: bool
    write_modes: tuple[str, ...]
    supports_runtime_wake: bool = False
    schema_inspection: bool = True
    exact_row_counts: bool = True
    verification_level: str = "exact"
    limitations: tuple[str, ...] = ()


def _ensure_registry() -> None:
    from app.connectors.registry import listed_capabilities as listed

    if listed():
        return
    load_builtin_connectors(demo=get_settings().is_demo_mode)


def provider_catalog(capabilities: ProviderCapabilities) -> ProviderCatalog:
    return ProviderCatalog(
        name=capabilities.provider,
        label=capabilities.label,
        technology=capabilities.technology,
        mark=capabilities.mark,
        region="",
        namespaces_label=capabilities.namespaces_label,
        objects_label=capabilities.objects_label,
        source=capabilities.source,
        destination=capabilities.destination,
        write_modes=capabilities.write_modes,
        schema_inspection=capabilities.schema_inspection,
        exact_row_counts=capabilities.exact_row_counts,
        verification_level=capabilities.verification_level,
        limitations=capabilities.limitations,
    )


def all_provider_catalogs() -> tuple[ProviderCatalog, ...]:
    _ensure_registry()
    return tuple(provider_catalog(item) for item in listed_capabilities() if item.provider != "csv")


PROVIDER_CATALOGS = ()  # populated after registry load; prefer all_provider_catalogs()
CSV_SOURCE_CATALOG = ProviderCatalog(
    name="csv",
    label="CSV file",
    technology="Delimited file",
    mark="CSV",
    region="Browser upload",
    namespaces_label="Upload",
    objects_label="File",
    source=True,
    destination=False,
    write_modes=(),
    schema_inspection=True,
    exact_row_counts=False,
    verification_level="local_manifest",
    limitations=("Scan the upload to inspect schema and exact row counts.",),
)
PROVIDER_CATALOG_MAP = {"csv": CSV_SOURCE_CATALOG}


def require_catalog_provider(provider: str) -> ProviderCatalog:
    if provider.casefold() == "csv":
        return CSV_SOURCE_CATALOG
    _ensure_registry()
    try:
        return provider_catalog(capabilities_for(provider))
    except Exception as exc:
        raise ValueError("Select a supported source and destination.") from exc
