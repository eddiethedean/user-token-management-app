"""Persistence and ownership rules for reusable data-movement pipelines."""

from __future__ import annotations

import re

from fastapi import Request
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.connectors.locators import (
    CsvUploadLocator,
    FoundryDatasetFilesLocator,
    FoundryReplaceFilePolicy,
    FoundryUploadLocator,
    PostgresAppendPolicy,
    PostgresReplacePolicy,
    PostgresUpsertPolicy,
    parse_locator,
    parse_write_policy,
    postgres_table,
)
from app.connectors.registry import capabilities_for, route_allowed
from app.models import PipelineDefinition, PipelineUpload, User, new_id
from app.services.audit import record_event
from app.services.catalogs import (
    CREATE_TABLE_VALUE,
    NEW_TABLE_VALUE_PREFIX,
    require_catalog_provider,
)

_NEW_TABLE_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,62}$")
_RID_PATTERN = re.compile(r"^ri\.[A-Za-z0-9._-]+\.[A-Za-z0-9._-]+\.dataset\.[A-Za-z0-9._-]+$")


def list_pipelines(db: Session, user: User) -> list[PipelineDefinition]:
    return list(
        db.scalars(
            select(PipelineDefinition)
            .where(
                PipelineDefinition.user_id == user.id,
                PipelineDefinition.legacy_unsupported.is_(False),
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
    destination_provider: str,
    write_mode: str,
    available_providers: set[str],
    source_namespace: str = "",
    source_object: str = "",
    source_branch: str = "master",
    destination_namespace: str = "",
    destination_object: str = "",
    destination_branch: str = "master",
    destination_table_new: str = "",
    source_upload_id: str = "",
    conflict_columns: str = "",
    upsert_action: str = "ignore",
    pipeline_id: str = "",
    request: Request | None = None,
    # legacy aliases used by existing forms
    source_schema: str = "",
    source_table: str = "",
    destination_schema: str = "",
    destination_table: str = "",
) -> PipelineDefinition:
    source_namespace = source_namespace or source_schema
    source_object = source_object or source_table
    destination_namespace = destination_namespace or destination_schema
    destination_object = destination_object or destination_table
    normalized_name = " ".join(name.split())
    if len(normalized_name) < 3:
        raise ValueError("Give this pipeline a name with at least 3 characters.")
    source_provider = source_provider.casefold()
    destination_provider = destination_provider.casefold()
    if source_provider != "csv" and source_provider == destination_provider:
        raise ValueError("Source and destination must be different systems.")
    try:
        if not route_allowed(source_provider, destination_provider):
            raise ValueError("Select a supported source and destination.")
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError("Select a supported source and destination.") from exc
    if source_provider != "csv" and source_provider not in available_providers:
        raise ValueError("Configure and validate the selected source connection before saving.")
    if destination_provider not in available_providers:
        raise ValueError(
            "Configure and validate the selected destination connection before saving."
        )

    source_upload = None
    destination_create = False
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
        source_locator = CsvUploadLocator(
            upload_id=source_upload.id, checksum_sha256=source_upload.checksum_sha256
        )
        final_source_schema = "uploaded"
        final_source_table = source_upload.filename[:80]
    elif source_provider == "postgres":
        source_locator = postgres_table(source_namespace, source_object)
        final_source_schema = source_locator.schema_name
        final_source_table = source_locator.table
    else:
        paths = "all_supported" if source_object in {"", "all_supported"} else [source_object]
        source_locator = FoundryDatasetFilesLocator(
            dataset_rid=_require_rid(source_namespace, "source dataset"),
            branch=source_branch or "master",
            file_paths=paths,
        )
        final_source_schema = source_locator.dataset_rid
        final_source_table = source_object or "all_supported"

    dest_caps = capabilities_for(destination_provider)
    destination_create = destination_object == CREATE_TABLE_VALUE or destination_object.startswith(
        NEW_TABLE_VALUE_PREFIX
    )
    if destination_create:
        final_destination_table = (
            destination_object.removeprefix(NEW_TABLE_VALUE_PREFIX)
            if destination_object.startswith(NEW_TABLE_VALUE_PREFIX)
            else destination_table_new.strip()
        )
        if not _NEW_TABLE_PATTERN.fullmatch(final_destination_table):
            raise ValueError(
                "New names must be 1–63 characters and use only letters, numbers, or underscores."
            )
        destination_object = final_destination_table
    else:
        final_destination_table = destination_object

    if destination_provider == "postgres":
        destination_locator = postgres_table(destination_namespace, destination_object)
        final_destination_schema = destination_locator.schema_name
        if write_mode == "upsert":
            columns = [item.strip() for item in conflict_columns.split(",") if item.strip()]
            if not columns:
                columns = ["event_id"] if source_provider != "postgres" else [source_object]
                # Prefer an explicit column; default to first identifier-like token.
                columns = [item for item in columns if _NEW_TABLE_PATTERN.fullmatch(item)] or ["id"]
            write_policy = PostgresUpsertPolicy(
                conflict_columns=columns, action="ignore" if upsert_action != "update" else "update"
            )
        elif write_mode == "replace":
            write_policy = PostgresReplacePolicy(
                schema_policy="recreate" if destination_create else "require_compatible"
            )
        else:
            write_policy = PostgresAppendPolicy()
    else:
        if write_mode != "replace":
            raise ValueError("Foundry destinations support replace of a named file.")
        file_name = destination_object
        if not file_name.endswith(".parquet"):
            file_name = f"{file_name}.snappy.parquet"
        destination_locator = FoundryUploadLocator(
            dataset_rid=_require_rid(destination_namespace, "destination dataset"),
            branch=destination_branch or "master",
            file_name=file_name,
        )
        write_policy = FoundryReplaceFilePolicy()
        final_destination_schema = destination_locator.dataset_rid
        final_destination_table = destination_locator.file_name

    parse_locator(source_locator.model_dump(by_alias=True))
    parse_locator(destination_locator.model_dump(by_alias=True))
    parse_write_policy(write_policy.model_dump())
    _ = dest_caps
    _ = require_catalog_provider(destination_provider)

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
    pipeline.destination_schema = final_destination_schema
    pipeline.destination_table = final_destination_table
    pipeline.destination_create = destination_create
    pipeline.write_mode = write_mode
    pipeline.definition_version = 2
    pipeline.source_locator_json = source_locator.model_dump_json(by_alias=True)
    pipeline.destination_locator_json = destination_locator.model_dump_json(by_alias=True)
    pipeline.write_policy_json = write_policy.model_dump_json()
    pipeline.legacy_unsupported = False
    record_event(
        db,
        event_type,
        request=request,
        actor=user,
        target=user,
        detail={
            "pipeline_id": pipeline.id,
            "source": f"{source_provider}:{final_source_schema}.{final_source_table}",
            "destination": f"{destination_provider}:{final_destination_schema}.{final_destination_table}",
            "destination_create": destination_create,
        },
    )
    db.commit()
    db.refresh(pipeline)
    return pipeline


def _require_rid(value: str, label: str) -> str:
    if not _RID_PATTERN.fullmatch(value):
        raise ValueError(f"Enter a valid {label} RID.")
    return value
