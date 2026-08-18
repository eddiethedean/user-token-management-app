"""MCS-COP Foundry connector (destination only)."""

from __future__ import annotations

from app.connectors.base import ProviderCapabilities
from app.connectors.foundry import FoundryConnector
from app.connectors.registry import register_connector


class McscopConnector(FoundryConnector):
    capabilities = ProviderCapabilities(
        provider="mcscop",
        label="MCS-COP",
        technology="Palantir Foundry",
        mark="MCS",
        source=False,
        destination=True,
        object_model="dataset RID → branch → file",
        write_modes=("replace",),
        namespaces_label="Dataset",
        objects_label="File",
        writer_enabled=False,
    )


def register() -> None:
    register_connector(McscopConnector)
