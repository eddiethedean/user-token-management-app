"""Pure presentation projections for saved pipeline controls."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SavedPipelineFields:
    pipeline_id: str
    name: str
    source_provider: str
    source_namespace: str
    source_object: str
    destination_provider: str
    destination_namespace: str
    destination_object: str
    destination_create: bool
    destination_new_name: str = ""
    write_mode: str = ""
    source_upload_id: str = ""
    source_upload_name: str = ""
    source_upload_rows: str = ""
    source_upload_size: str = ""
    source_upload_megabytes: str = ""
    source_upload_columns: str = ""


def saved_pipeline_form_data(fields: SavedPipelineFields, *, run: bool = False) -> dict[str, str]:
    """Convert a pure projection into the HTMX form data contract."""

    data = {
        "pipeline-load": "true",
        "pipeline-id": fields.pipeline_id,
        "pipeline-name": fields.name,
        "pipeline-source": fields.source_provider,
        "pipeline-source-schema": fields.source_namespace,
        "pipeline-source-table": fields.source_object,
        "pipeline-target": fields.destination_provider,
        "pipeline-target-schema": fields.destination_namespace,
        "pipeline-target-table": "__new__"
        if fields.destination_create
        else fields.destination_object,
        "pipeline-target-table-new": fields.destination_new_name
        if fields.destination_create
        else "",
        "pipeline-mode": fields.write_mode,
    }
    if run:
        data["pipeline-run"] = "true"
    if fields.source_upload_id:
        data.update(
            {
                "pipeline-source-upload-id": fields.source_upload_id,
                "pipeline-source-upload-name": fields.source_upload_name,
                "pipeline-source-upload-rows": fields.source_upload_rows,
                "pipeline-source-upload-size": fields.source_upload_size,
                "pipeline-source-upload-megabytes": fields.source_upload_megabytes,
                "pipeline-source-upload-columns": fields.source_upload_columns,
            }
        )
    return data
