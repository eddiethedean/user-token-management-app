"""Connector protocols, capabilities, and transfer value types."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol

from app.connectors.errors import ConnectorError, TransferErrorCode
from app.connectors.locators import Locator, WritePolicy

Credentials = Mapping[str, str]


@dataclass(frozen=True)
class ProviderCapabilities:
    provider: str
    label: str
    technology: str
    mark: str
    source: bool
    destination: bool
    object_model: str
    write_modes: tuple[str, ...]
    namespaces_label: str
    objects_label: str
    writer_enabled: bool = False
    schema_inspection: bool = True
    exact_row_counts: bool = True
    verification_level: str = "exact"
    limitations: tuple[str, ...] = ()


@dataclass(frozen=True)
class ConnectionHealth:
    status: str
    message: str
    latency_ms: int
    server_identity: str = ""


@dataclass(frozen=True)
class RemoteNamespace:
    name: str
    display_name: str
    kind: str = "namespace"


@dataclass(frozen=True)
class RemoteObject:
    name: str
    display_name: str
    locator: Locator
    estimated_rows: int | None = None
    size_bytes: int | None = None
    updated_at: str = ""
    format: str = ""


@dataclass(frozen=True)
class CatalogPage:
    items: tuple[RemoteObject, ...]
    cursor: str | None = None


@dataclass(frozen=True)
class ColumnSchema:
    name: str
    data_type: str
    nullable: bool = True
    example: str = ""


@dataclass(frozen=True)
class ObjectSchema:
    locator: Locator
    columns: tuple[ColumnSchema, ...]
    primary_key: tuple[str, ...] = ()
    unique_constraints: tuple[tuple[str, ...], ...] = ()
    estimated_rows: int | None = None


@dataclass(frozen=True)
class TransferBatch:
    frame: Any
    row_count: int
    byte_count: int
    sequence: int


@dataclass(frozen=True)
class BatchWriteResult:
    rows_acknowledged: int
    bytes_acknowledged: int


@dataclass
class LoadSession:
    locator: Locator
    write_policy: WritePolicy
    staging_name: str = ""
    columns: tuple[str, ...] = ()
    metadata: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class DestinationManifest:
    locator: Locator
    rows: int
    bytes: int
    checksum: str = ""
    remote_id: str = ""
    details: Mapping[str, str] = field(default_factory=dict)


class Connector(Protocol):
    capabilities: ProviderCapabilities

    def test_connection(self, credentials: Credentials) -> ConnectionHealth: ...

    def list_namespaces(self, credentials: Credentials) -> list[RemoteNamespace]: ...

    def list_objects(
        self,
        credentials: Credentials,
        namespace: str,
        cursor: str | None = None,
    ) -> CatalogPage: ...

    def inspect_object(self, credentials: Credentials, locator: Locator) -> ObjectSchema: ...

    def count_rows(self, credentials: Credentials, locator: Locator) -> int | None: ...

    def extract(
        self,
        credentials: Credentials,
        locator: Locator,
        *,
        batch_rows: int,
        batch_bytes: int,
    ) -> Iterator[TransferBatch]: ...

    def prepare_destination(
        self,
        credentials: Credentials,
        locator: Locator,
        schema: ObjectSchema,
        write_policy: WritePolicy,
        *,
        run_id: str,
    ) -> LoadSession: ...

    def write_batch(self, load_session: LoadSession, batch: TransferBatch) -> BatchWriteResult: ...

    def finalize(self, load_session: LoadSession) -> DestinationManifest: ...

    def abort(self, load_session: LoadSession) -> None: ...


def map_http_status(status_code: int, *, for_destination: bool = False) -> TransferErrorCode:
    if status_code in {401}:
        return TransferErrorCode.AUTHENTICATION_FAILED
    if status_code in {403}:
        return TransferErrorCode.PERMISSION_DENIED
    if status_code == 404:
        return (
            TransferErrorCode.DESTINATION_NOT_FOUND
            if for_destination
            else TransferErrorCode.SOURCE_NOT_FOUND
        )
    if status_code == 409:
        return TransferErrorCode.DESTINATION_CONFLICT
    if status_code == 429:
        return TransferErrorCode.RATE_LIMITED
    if status_code >= 500:
        return TransferErrorCode.PROVIDER_UNAVAILABLE
    return TransferErrorCode.INTERNAL_ERROR


def fail(code: TransferErrorCode, summary: str, **kwargs: Any) -> ConnectorError:
    return ConnectorError(code=code, summary=summary, **kwargs)
