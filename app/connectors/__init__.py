"""Outbound connector ports for Data Mover transfers."""

from __future__ import annotations

from app.connectors.base import (
    BatchWriteResult,
    CatalogPage,
    ColumnSchema,
    ConnectionHealth,
    Connector,
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
    DefinitionSnapshot,
    FoundryDatasetFilesLocator,
    FoundryUploadLocator,
    Locator,
    PostgresTableLocator,
    WritePolicy,
    parse_locator,
    parse_snapshot,
    parse_write_policy,
)

__all__ = [
    "BatchWriteResult",
    "CatalogPage",
    "ColumnSchema",
    "ConnectionHealth",
    "Connector",
    "ConnectorError",
    "CsvUploadLocator",
    "DefinitionSnapshot",
    "DestinationManifest",
    "FoundryDatasetFilesLocator",
    "FoundryUploadLocator",
    "LoadSession",
    "Locator",
    "ObjectSchema",
    "PostgresTableLocator",
    "ProviderCapabilities",
    "RemoteNamespace",
    "RemoteObject",
    "TransferBatch",
    "TransferErrorCode",
    "WritePolicy",
    "parse_locator",
    "parse_snapshot",
    "parse_write_policy",
]
