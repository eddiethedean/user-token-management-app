"""Synthetic remote catalogs used by the Data Mover data-movement demo."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SchemaCatalog:
    name: str
    tables: tuple[str, ...]


@dataclass(frozen=True)
class ProviderCatalog:
    name: str
    label: str
    technology: str
    mark: str
    region: str
    schemas: tuple[SchemaCatalog, ...]
    validation_latency_ms: int
    supports_runtime_wake: bool = False


PROVIDER_CATALOGS = (
    ProviderCatalog(
        name="advana",
        label="Advana",
        technology="Databricks",
        mark="AV",
        region="us-gov-west-1",
        schemas=(
            SchemaCatalog(
                "operations",
                ("readiness_events", "mission_orders", "unit_locations"),
            ),
            SchemaCatalog(
                "logistics",
                ("asset_inventory", "maintenance_events", "supply_positions"),
            ),
            SchemaCatalog(
                "readiness",
                ("mission_readiness", "personnel_status", "equipment_status"),
            ),
        ),
        validation_latency_ms=118,
        supports_runtime_wake=True,
    ),
    ProviderCatalog(
        name="mss",
        label="MSS",
        technology="Palantir Foundry",
        mark="MSS",
        region="us-gov-central-1",
        schemas=(
            SchemaCatalog(
                "ontology",
                ("mission_objects", "asset_objects", "unit_objects"),
            ),
            SchemaCatalog(
                "operational_data",
                ("mission_orders_curated", "readiness_rollup", "asset_assignments"),
            ),
            SchemaCatalog(
                "raw",
                ("source_events", "incoming_orders", "inventory_snapshots"),
            ),
        ),
        validation_latency_ms=143,
    ),
    ProviderCatalog(
        name="postgres",
        label="PostgreSQL",
        technology="PostgreSQL 16",
        mark="PG",
        region="private-vpc",
        schemas=(
            SchemaCatalog(
                "public",
                ("readiness_events", "asset_inventory", "mission_orders"),
            ),
            SchemaCatalog(
                "staging",
                ("readiness_events_stage", "raw_events", "ingest_failures"),
            ),
            SchemaCatalog(
                "reporting",
                ("daily_readiness", "asset_utilization", "mission_throughput"),
            ),
        ),
        validation_latency_ms=39,
    ),
    ProviderCatalog(
        name="mongodb",
        label="MongoDB",
        technology="MongoDB 8",
        mark="MDB",
        region="document-cluster",
        schemas=(
            SchemaCatalog(
                "operations",
                ("readiness_events", "mission_orders", "unit_locations"),
            ),
            SchemaCatalog(
                "telemetry",
                ("sensor_events", "asset_positions", "system_health"),
            ),
            SchemaCatalog(
                "archive",
                ("historical_orders", "readiness_snapshots", "audit_documents"),
            ),
        ),
        validation_latency_ms=52,
    ),
)

CSV_SOURCE_CATALOG = ProviderCatalog(
    name="csv",
    label="CSV file",
    technology="Delimited file",
    mark="CSV",
    region="Browser upload",
    schemas=(),
    validation_latency_ms=0,
)

PROVIDER_CATALOG_MAP = {
    **{catalog.name: catalog for catalog in PROVIDER_CATALOGS},
    CSV_SOURCE_CATALOG.name: CSV_SOURCE_CATALOG,
}
CREATE_TABLE_VALUE = "__new__"
NEW_TABLE_VALUE_PREFIX = f"{CREATE_TABLE_VALUE}:"


def require_catalog_provider(provider: str) -> ProviderCatalog:
    catalog = PROVIDER_CATALOG_MAP.get(provider.casefold())
    if catalog is None:
        raise ValueError("Select a supported source and destination.")
    return catalog


def schema_names(provider: str) -> tuple[str, ...]:
    return tuple(schema.name for schema in require_catalog_provider(provider).schemas)


def table_names(provider: str, schema_name: str) -> tuple[str, ...]:
    catalog = require_catalog_provider(provider)
    schema = next((item for item in catalog.schemas if item.name == schema_name), None)
    if schema is None:
        raise ValueError(f"Select an available {catalog.label} schema.")
    return schema.tables


def validate_existing_object(provider: str, schema_name: str, table_name: str) -> None:
    if table_name not in table_names(provider, schema_name):
        raise ValueError("Select an existing table from the simulated connection catalog.")
