"""Provider ID to connector factory and capability lookup."""

from __future__ import annotations

from collections.abc import Callable

from app.connectors.base import Connector, ProviderCapabilities
from app.connectors.errors import ConnectorError, TransferErrorCode

ConnectorFactory = Callable[[], Connector]

_REGISTRY: dict[str, ConnectorFactory] = {}
_CAPABILITIES: dict[str, ProviderCapabilities] = {}


def register_connector(factory: ConnectorFactory) -> ConnectorFactory:
    connector = factory()
    _REGISTRY[connector.capabilities.provider] = factory
    _CAPABILITIES[connector.capabilities.provider] = connector.capabilities
    return factory


def connector_for(provider: str) -> Connector:
    factory = _REGISTRY.get(provider.casefold())
    if factory is None:
        raise ConnectorError(
            code=TransferErrorCode.INTERNAL_ERROR,
            summary="Select a supported connection provider.",
            retryable=False,
        )
    return factory()


def capabilities_for(provider: str) -> ProviderCapabilities:
    try:
        return _CAPABILITIES[provider.casefold()]
    except KeyError as exc:
        raise ConnectorError(
            code=TransferErrorCode.INTERNAL_ERROR,
            summary="Select a supported connection provider.",
            retryable=False,
        ) from exc


def listed_capabilities(*, sources: bool | None = None, destinations: bool | None = None):
    items = list(_CAPABILITIES.values())
    if sources is not None:
        items = [item for item in items if item.source is sources]
    if destinations is not None:
        items = [item for item in items if item.destination is destinations]
    return items


def route_allowed(source_provider: str, destination_provider: str) -> bool:
    if source_provider == "csv":
        return capabilities_for(destination_provider).destination
    if source_provider == destination_provider:
        return False
    source = capabilities_for(source_provider)
    destination = capabilities_for(destination_provider)
    return source.source and destination.destination


def writer_enabled(destination_provider: str) -> bool:
    capabilities = capabilities_for(destination_provider)
    if not capabilities.destination:
        return False
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


def supported_write_modes(destination_provider: str) -> tuple[str, ...]:
    return capabilities_for(destination_provider).write_modes


def load_builtin_connectors(*, demo: bool) -> None:
    """Register fake or real connectors. Safe to call more than once."""
    _REGISTRY.clear()
    _CAPABILITIES.clear()
    if demo:
        from app.connectors import fake as _fake

        _fake.register()
        return
    from app.connectors import csv_source, mcscop, mss, postgres

    postgres.register()
    mss.register()
    mcscop.register()
    csv_source.register()
