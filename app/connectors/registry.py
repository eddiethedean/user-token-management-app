"""Provider ID to connector factory and capability lookup."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Literal, TypeVar, cast

from app.connectors.base import (
    CatalogBrowser,
    CatalogReader,
    ConnectionTester,
    DatasetProvisioner,
    DestinationWriter,
    ObjectSchemaInspector,
    ProviderCapabilities,
    RegisteredConnector,
    RowCounter,
    SourceReader,
)
from app.connectors.errors import ConnectorError, TransferErrorCode

if TYPE_CHECKING:
    from app.config import Settings

ConnectorFactory = Callable[[], RegisteredConnector]
RoleT = TypeVar("RoleT")


class ConnectorRegistry:
    """Replaceable connector registry for application and test composition."""

    def __init__(self) -> None:
        self._factories: dict[str, ConnectorFactory] = {}
        self._capabilities: dict[str, ProviderCapabilities] = {}

    def register(self, factory: ConnectorFactory) -> ConnectorFactory:
        connector = factory()
        provider = connector.capabilities.provider.casefold()
        self._factories[provider] = factory
        self._capabilities[provider] = connector.capabilities
        return factory

    def connector_for(self, provider: str) -> RegisteredConnector:
        factory = self._factories.get(provider.casefold())
        if factory is None:
            raise ConnectorError(
                code=TransferErrorCode.INTERNAL_ERROR,
                summary="Select a supported connection provider.",
                retryable=False,
            )
        return factory()

    def _role_for(
        self,
        provider: str,
        role: type[RoleT],
        label: str,
        *,
        capabilities: tuple[
            Literal[
                "source", "destination", "dataset_creation", "schema_inspection", "exact_row_counts"
            ],
            ...,
        ] = (),
    ) -> RoleT:
        provider_capabilities = self.capabilities_for(provider)
        if any(not getattr(provider_capabilities, capability) for capability in capabilities):
            raise ConnectorError(
                code=TransferErrorCode.INTERNAL_ERROR,
                summary=f"The selected provider does not support {label}.",
                retryable=False,
            )
        connector = self.connector_for(provider)
        if not isinstance(connector, role):
            raise ConnectorError(
                code=TransferErrorCode.INTERNAL_ERROR,
                summary=f"The selected provider does not support {label}.",
                retryable=False,
            )
        return cast(RoleT, connector)

    def catalog_reader_for(self, provider: str) -> CatalogReader:
        return self._role_for(
            provider,
            CatalogReader,
            "catalog browsing",
            capabilities=("schema_inspection", "exact_row_counts"),
        )

    def catalog_browser_for(self, provider: str) -> CatalogBrowser:
        return self._role_for(provider, CatalogBrowser, "catalog browsing")

    def object_schema_inspector_for(self, provider: str) -> ObjectSchemaInspector:
        return self._role_for(
            provider,
            ObjectSchemaInspector,
            "schema inspection",
            capabilities=("schema_inspection",),
        )

    def row_counter_for(self, provider: str) -> RowCounter:
        return self._role_for(
            provider,
            RowCounter,
            "row counting",
            capabilities=("exact_row_counts",),
        )

    def connection_tester_for(self, provider: str) -> ConnectionTester:
        return self._role_for(provider, ConnectionTester, "connection testing")

    def source_reader_for(self, provider: str) -> SourceReader:
        return self._role_for(
            provider,
            SourceReader,
            "source extraction",
            capabilities=("source",),
        )

    def destination_writer_for(self, provider: str) -> DestinationWriter:
        return self._role_for(
            provider,
            DestinationWriter,
            "destination writes",
            capabilities=("destination",),
        )

    def dataset_provisioner_for(self, provider: str) -> DatasetProvisioner:
        return cast(
            DatasetProvisioner,
            self._role_for(
                provider,
                DatasetProvisioner,
                "dataset creation",
                capabilities=("dataset_creation",),
            ),
        )

    def capabilities_for(self, provider: str) -> ProviderCapabilities:
        try:
            return self._capabilities[provider.casefold()]
        except KeyError as exc:
            raise ConnectorError(
                code=TransferErrorCode.INTERNAL_ERROR,
                summary="Select a supported connection provider.",
                retryable=False,
            ) from exc

    def listed_capabilities(
        self,
        *,
        sources: bool | None = None,
        destinations: bool | None = None,
    ) -> list[ProviderCapabilities]:
        items = list(self._capabilities.values())
        if sources is not None:
            items = [item for item in items if item.source is sources]
        if destinations is not None:
            items = [item for item in items if item.destination is destinations]
        return items

    def clear(self) -> None:
        self._factories.clear()
        self._capabilities.clear()


_DEFAULT_REGISTRY = ConnectorRegistry()


def register_connector(factory: ConnectorFactory) -> ConnectorFactory:
    return _DEFAULT_REGISTRY.register(factory)


def connector_for(provider: str) -> RegisteredConnector:
    return _DEFAULT_REGISTRY.connector_for(provider)


def catalog_reader_for(provider: str) -> CatalogReader:
    return _DEFAULT_REGISTRY.catalog_reader_for(provider)


def catalog_browser_for(provider: str) -> CatalogBrowser:
    return _DEFAULT_REGISTRY.catalog_browser_for(provider)


def object_schema_inspector_for(provider: str) -> ObjectSchemaInspector:
    return _DEFAULT_REGISTRY.object_schema_inspector_for(provider)


def row_counter_for(provider: str) -> RowCounter:
    return _DEFAULT_REGISTRY.row_counter_for(provider)


def connection_tester_for(provider: str) -> ConnectionTester:
    return _DEFAULT_REGISTRY.connection_tester_for(provider)


def source_reader_for(provider: str) -> SourceReader:
    return _DEFAULT_REGISTRY.source_reader_for(provider)


def destination_writer_for(provider: str) -> DestinationWriter:
    return _DEFAULT_REGISTRY.destination_writer_for(provider)


def capabilities_for(provider: str) -> ProviderCapabilities:
    return _DEFAULT_REGISTRY.capabilities_for(provider)


def dataset_provisioner_for(provider: str) -> DatasetProvisioner:
    """Resolve a provider that explicitly supports dataset provisioning."""

    return _DEFAULT_REGISTRY.dataset_provisioner_for(provider)


def listed_capabilities(*, sources: bool | None = None, destinations: bool | None = None):
    return _DEFAULT_REGISTRY.listed_capabilities(sources=sources, destinations=destinations)


def route_allowed(source_provider: str, destination_provider: str) -> bool:
    """Return whether the registered connector roles can form this route.

    Route compatibility is derived from connector capabilities so adding a
    provider does not require a second, easy-to-forget global route matrix.
    Operational writer flags remain a separate gate for destinations.
    """

    try:
        source = capabilities_for(source_provider)
        destination = capabilities_for(destination_provider)
    except ConnectorError:
        return False
    return bool(source.source and destination.destination)


def writer_enabled(destination_provider: str, *, settings: Settings | None = None) -> bool:
    capabilities = capabilities_for(destination_provider)
    if not capabilities.destination:
        return False
    if settings is None:
        from app.config import get_settings

        settings = get_settings()
    if settings.is_demo_mode:
        return capabilities.writer_enabled
    flags = {
        "postgres": settings.pipeline_enable_postgres_writer,
        "mss": settings.pipeline_enable_mss_writer,
        "mcscop": settings.pipeline_enable_mcscop_writer,
    }
    return bool(flags.get(destination_provider.casefold(), capabilities.writer_enabled))


def load_builtin_connectors(*, demo: bool) -> None:
    """Register fake or real connectors. Safe to call more than once."""
    _DEFAULT_REGISTRY.clear()
    if demo:
        from app.connectors import fake as _fake

        _fake.register()
        return
    from app.connectors import csv_source, mcscop, mss, postgres

    postgres.register()
    mss.register()
    mcscop.register()
    csv_source.register()
