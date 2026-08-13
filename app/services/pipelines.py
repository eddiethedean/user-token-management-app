"""Persistence and ownership rules for reusable data-movement pipelines."""

from __future__ import annotations

import re

from fastapi import Request
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models import PipelineDefinition, PipelineUpload, User, new_id
from app.services.audit import record_event
from app.services.catalogs import (
    CREATE_TABLE_VALUE,
    NEW_TABLE_VALUE_PREFIX,
    schema_names,
    validate_existing_object,
)

PROVIDERS = {"advana", "mss", "postgres", "mongodb"}
SOURCE_PROVIDERS = PROVIDERS | {"csv"}
WRITE_MODES = {"upsert", "append", "replace"}
_NEW_TABLE_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_]{1,62}$")


def list_pipelines(db: Session, user: User) -> list[PipelineDefinition]:
    return list(
        db.scalars(
            select(PipelineDefinition)
            .where(
                PipelineDefinition.user_id == user.id,
                PipelineDefinition.source_provider.in_(SOURCE_PROVIDERS),
                PipelineDefinition.destination_provider.in_(PROVIDERS),
            )
            .options(selectinload(PipelineDefinition.source_upload))
            .order_by(PipelineDefinition.updated_at.desc())
            .limit(12)
        ).all()
    )


def save_pipeline(
    db: Session,
    *,
    user: User,
    name: str,
    source_provider: str,
    source_schema: str,
    source_table: str,
    destination_provider: str,
    destination_schema: str,
    destination_table: str,
    write_mode: str,
    available_providers: set[str],
    destination_table_new: str = "",
    source_upload_id: str = "",
    pipeline_id: str = "",
    request: Request | None = None,
) -> PipelineDefinition:
    normalized_name = " ".join(name.split())
    if len(normalized_name) < 3:
        raise ValueError("Give this pipeline a name with at least 3 characters.")
    if source_provider not in SOURCE_PROVIDERS or destination_provider not in PROVIDERS:
        raise ValueError("Select a supported source and destination.")
    if source_provider != "csv" and source_provider == destination_provider:
        raise ValueError("Source and destination must be different systems.")
    if source_provider != "csv" and source_provider not in available_providers:
        raise ValueError("Configure and validate the selected source connection before saving.")
    if destination_provider not in available_providers:
        raise ValueError(
            "Configure and validate the selected destination connection before saving."
        )
    source_upload = None
    if source_provider == "csv":
        if not source_upload_id:
            raise ValueError("Upload and inspect a CSV file before saving this pipeline.")
        source_upload = db.scalar(
            select(PipelineUpload).where(
                PipelineUpload.id == source_upload_id,
                PipelineUpload.user_id == user.id,
            )
        )
        if source_upload is None:
            raise ValueError("That CSV upload is no longer available.")
        final_source_schema = "uploaded"
        final_source_table = source_upload.filename[:80]
    else:
        validate_existing_object(source_provider, source_schema, source_table)
        final_source_schema = source_schema
        final_source_table = source_table
    if destination_schema not in schema_names(destination_provider):
        raise ValueError("Select an available destination schema.")
    destination_create = destination_table == CREATE_TABLE_VALUE or destination_table.startswith(
        NEW_TABLE_VALUE_PREFIX
    )
    if destination_create:
        final_destination_table = (
            destination_table.removeprefix(NEW_TABLE_VALUE_PREFIX)
            if destination_table.startswith(NEW_TABLE_VALUE_PREFIX)
            else destination_table_new.strip()
        )
        if not _NEW_TABLE_PATTERN.fullmatch(final_destination_table):
            raise ValueError(
                "New table names must be 2–63 characters and use only letters, numbers, or underscores."
            )
    else:
        validate_existing_object(destination_provider, destination_schema, destination_table)
        final_destination_table = destination_table
    if write_mode not in WRITE_MODES:
        raise ValueError("Select a supported write mode.")

    pipeline = None
    if pipeline_id:
        pipeline = db.scalar(
            select(PipelineDefinition).where(
                PipelineDefinition.id == pipeline_id,
                PipelineDefinition.user_id == user.id,
            )
        )
        if pipeline is None:
            raise ValueError("That saved pipeline is no longer available.")
    event_type = "pipeline.updated" if pipeline else "pipeline.created"
    if pipeline is None:
        pipeline = PipelineDefinition(id=new_id(), user_id=user.id)
        db.add(pipeline)
    pipeline.name = normalized_name[:120]
    pipeline.source_provider = source_provider
    pipeline.source_schema = final_source_schema
    pipeline.source_table = final_source_table
    pipeline.source_dataset = final_source_table
    pipeline.source_upload_id = source_upload.id if source_upload is not None else None
    pipeline.destination_provider = destination_provider
    pipeline.destination_schema = destination_schema
    pipeline.destination_table = final_destination_table
    pipeline.destination_create = destination_create
    pipeline.write_mode = write_mode
    record_event(
        db,
        event_type,
        request=request,
        actor=user,
        target=user,
        detail={
            "pipeline_id": pipeline.id,
            "source": f"{source_provider}:{final_source_schema}.{final_source_table}",
            "destination": (
                f"{destination_provider}:{destination_schema}.{final_destination_table}"
            ),
            "destination_create": destination_create,
        },
    )
    db.commit()
    db.refresh(pipeline)
    return pipeline
