"""MCS-COP Foundry connector."""

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
        source=True,
        destination=True,
        object_model="dataset RID → branch → file",
        write_modes=("replace",),
        namespaces_label="Dataset",
        objects_label="File",
        writer_enabled=True,
        writer_setting="pipeline_enable_mcscop_writer",
        schema_inspection=True,
        exact_row_counts=False,
        verification_level="local_manifest",
        limitations=(
            "Column preview reads one selected file up to 2 MB; larger or multiple files are inspected during the run.",
        ),
        dataset_creation=True,
    )


def register() -> None:
    register_connector(McscopConnector)
