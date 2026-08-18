"""MSS Foundry connector."""

from __future__ import annotations

from app.connectors.base import ProviderCapabilities
from app.connectors.foundry import FoundryConnector
from app.connectors.registry import register_connector


class MssConnector(FoundryConnector):
    capabilities = ProviderCapabilities(
        provider="mss",
        label="MSS",
        technology="Palantir Foundry",
        mark="MSS",
        source=True,
        destination=True,
        object_model="dataset RID → branch → file",
        write_modes=("replace",),
        namespaces_label="Dataset",
        objects_label="File",
        writer_enabled=False,
    )


def register() -> None:
    register_connector(MssConnector)
