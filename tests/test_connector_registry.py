"""Tests for the replaceable connector registry abstraction."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.connectors.base import ConnectionHealth, ProviderCapabilities
from app.connectors.errors import ConnectorError
from app.connectors.registry import ConnectorRegistry


def _connector(provider: str) -> SimpleNamespace:
    capabilities = ProviderCapabilities(
        provider=provider,
        label=provider.title(),
        technology="Test",
        mark=provider[:2].upper(),
        source=True,
        destination=True,
        object_model="table",
        write_modes=("append",),
        namespaces_label="Schema",
        objects_label="Table",
    )
    return SimpleNamespace(
        capabilities=capabilities,
        test_connection=lambda credentials: ConnectionHealth("connected", "ok", 0),
    )


def test_registry_instances_are_isolated_and_replaceable() -> None:
    registry = ConnectorRegistry()

    def factory() -> object:
        return _connector("example")

    registry.register(factory)

    assert registry.connector_for("EXAMPLE").capabilities.provider == "example"
    assert registry.capabilities_for("example").label == "Example"
    assert registry.listed_capabilities()[0].provider == "example"

    other = ConnectorRegistry()
    assert other.listed_capabilities() == []
    with pytest.raises(ConnectorError):
        other.connector_for("example")
