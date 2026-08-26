"""Provider-neutral extract/load orchestration."""

from __future__ import annotations

import time
from collections.abc import Callable
from itertools import chain

from sqlalchemy.orm import Session

from app.config import Settings
from app.connectors.base import ColumnSchema, ObjectSchema, TransferBatch
from app.connectors.errors import ConnectorError, TransferErrorCode
from app.connectors.locators import DefinitionSnapshot
from app.connectors.registry import connector_for, writer_enabled
from app.models import PipelineRun, utcnow
from app.services import pipeline_runs
from app.services.pipeline_metadata import manifest_metadata

CancelCheck = Callable[[], bool]


def _destination_row_count(destination, credentials, locator) -> int | None:
    """Read destination counts as best-effort telemetry, never as a run prerequisite."""

    count_rows = getattr(destination, "count_rows", None)
    if count_rows is None:
        return None
    try:
        value = count_rows(credentials, locator)
    except Exception:
        return None
    return int(value) if value is not None else None


def _schema_manifest(schema: ObjectSchema) -> dict:
    return {
        "columns": [
            {
                "name": column.name,
                "data_type": column.data_type,
                "nullable": column.nullable,
                "example": column.example,
            }
            for column in schema.columns
        ],
        "primary_key": list(schema.primary_key),
        "unique_constraints": [list(item) for item in schema.unique_constraints],
    }


def _inspect_destination_schema(destination, credentials, locator) -> ObjectSchema | None:
    try:
        inspected = destination.inspect_object(credentials, locator)
    except Exception:
        return None
    return inspected if inspected.columns else None


def _demo_stage_pause(settings: Settings) -> None:
    """Keep local demo stages visible without slowing tests or real transfers."""

    if settings.is_demo_mode and settings.app_env != "test":
        time.sleep(0.7)


def execute_transfer(
    db: Session,
    *,
    run: PipelineRun,
    lease_token: str,
    snapshot: DefinitionSnapshot,
    source_credentials: dict[str, str],
    destination_credentials: dict[str, str],
    settings: Settings,
    cancel_requested: CancelCheck,
) -> None:
    source = connector_for(snapshot.source_provider)
    destination = connector_for(snapshot.destination_provider)
    if not writer_enabled(snapshot.destination_provider) and not settings.is_demo_mode:
        raise ConnectorError(
            TransferErrorCode.PERMISSION_DENIED,
            "This destination writer is not enabled.",
            retryable=False,
        )
    if cancel_requested():
        pipeline_runs.cancel_claimed_run(db, run, lease_token=lease_token)
        return

    pipeline_runs.heartbeat(
        db, run, lease_token=lease_token, lease_seconds=settings.pipeline_lease_seconds
    )
    _demo_stage_pause(settings)
    source.test_connection(source_credentials)
    destination.test_connection(destination_credentials)
    destination_rows_before = _destination_row_count(
        destination, destination_credentials, snapshot.destination
    )
    destination_schema_before = _inspect_destination_schema(
        destination, destination_credentials, snapshot.destination
    )
    source_schema = source.inspect_object(source_credentials, snapshot.source)
    pipeline_runs.transition(
        db,
        run,
        "extracting",
        lease_token=lease_token,
        message="Source and destination connections validated.",
    )
    _demo_stage_pause(settings)

    extracted_rows = 0
    extracted_bytes = 0
    started = utcnow()
    source_iterator = iter(
        source.extract(
            source_credentials,
            snapshot.source,
            batch_rows=settings.pipeline_batch_rows,
            batch_bytes=settings.pipeline_batch_target_bytes,
        )
    )
    schema = source_schema
    session = None
    try:
        # Fetch only the first batch before preparing the destination so a
        # source without portable schema metadata can still define its table.
        # The remaining batches stay in the iterator and are loaded as they
        # arrive; retaining the complete source in memory made large runs
        # exceed the application's bounded-batch contract.
        first_batch = next(source_iterator, None)
        if first_batch is not None:
            if cancel_requested():
                pipeline_runs.cancel_claimed_run(db, run, lease_token=lease_token)
                return
            frame = first_batch.frame
            schema = ObjectSchema(
                locator=snapshot.source,
                columns=tuple(
                    ColumnSchema(name=name, data_type=str(dtype), nullable=True)
                    for name, dtype in frame.schema.items()
                ),
                primary_key=source_schema.primary_key,
                unique_constraints=source_schema.unique_constraints,
            )

        pipeline_runs.transition(
            db,
            run,
            "loading",
            lease_token=lease_token,
            message="Starting destination load.",
        )
        _demo_stage_pause(settings)
        session = destination.prepare_destination(
            destination_credentials,
            snapshot.destination,
            schema,
            snapshot.write_policy,
            run_id=run.id,
        )
        loaded_bytes = 0
        for batch in chain((first_batch,) if first_batch is not None else (), source_iterator):
            if cancel_requested():
                destination.abort(session)
                pipeline_runs.cancel_claimed_run(db, run, lease_token=lease_token)
                return
            if (utcnow() - started).total_seconds() > settings.pipeline_max_run_seconds:
                raise ConnectorError(
                    TransferErrorCode.RUN_TIMEOUT, "The run exceeded its time limit."
                )
            extracted_rows += batch.row_count
            extracted_bytes += batch.byte_count
            if extracted_bytes > settings.pipeline_max_source_bytes:
                raise ConnectorError(
                    TransferErrorCode.SOURCE_LIMIT_EXCEEDED,
                    "The source exceeded the configured size limit.",
                )
            expected_columns = tuple(column.name for column in schema.columns)
            actual_columns = tuple(str(name) for name in batch.frame.columns)
            if set(actual_columns) != set(expected_columns):
                raise ConnectorError(
                    TransferErrorCode.SCHEMA_DRIFT,
                    "The source schema changed during extraction.",
                    retryable=False,
                )
            if actual_columns != expected_columns:
                batch = TransferBatch(
                    frame=batch.frame.select(list(expected_columns)),
                    row_count=batch.row_count,
                    byte_count=batch.byte_count,
                    sequence=batch.sequence,
                )
            pipeline_runs.add_counters(
                db,
                run,
                lease_token=lease_token,
                source_rows=batch.row_count,
                source_bytes=batch.byte_count,
            )
            pipeline_runs.append_event(
                db,
                run,
                f"Extracted batch {batch.sequence}: {batch.row_count} rows.",
                stage="inspect",
            )
            result = destination.write_batch(session, batch)
            loaded_bytes += result.bytes_acknowledged
            pipeline_runs.add_counters(
                db,
                run,
                lease_token=lease_token,
                loaded_rows=result.rows_acknowledged,
                loaded_bytes=result.bytes_acknowledged,
            )
            pipeline_runs.append_event(
                db,
                run,
                f"Loaded batch {batch.sequence}: {result.rows_acknowledged} rows.",
                stage="transfer",
            )
            db.commit()
            _demo_stage_pause(settings)
        pipeline_runs.transition(
            db,
            run,
            "verifying",
            lease_token=lease_token,
            message="Finalizing destination write.",
        )
        _demo_stage_pause(settings)
        manifest = destination.finalize(session)
        destination_rows_after = _destination_row_count(
            destination, destination_credentials, snapshot.destination
        )
        destination_schema_after = _inspect_destination_schema(
            destination, destination_credentials, snapshot.destination
        )
        verification = {
            "source_rows": extracted_rows,
            "loaded_rows": manifest.rows,
            "source_bytes": extracted_bytes,
            "loaded_bytes": manifest.bytes or loaded_bytes,
            "destination_rows_before": destination_rows_before,
            "destination_rows_after": destination_rows_after,
            "destination_row_delta": (
                destination_rows_after - destination_rows_before
                if destination_rows_before is not None and destination_rows_after is not None
                else None
            ),
        }
        pipeline_runs.complete_run(
            db,
            run,
            lease_token=lease_token,
            source_manifest={
                "rows": extracted_rows,
                "bytes": extracted_bytes,
                "schema": _schema_manifest(schema),
                "metadata": manifest_metadata(
                    rows=extracted_rows,
                    schema_available=bool(schema.columns),
                    row_provenance="exact",
                    schema_provenance="captured",
                ),
            },
            destination_manifest={
                "rows": manifest.rows,
                "bytes": manifest.bytes,
                "checksum": manifest.checksum,
                "remote_id": manifest.remote_id,
                "details": dict(manifest.details),
                "schema": _schema_manifest(destination_schema_after or schema),
                "schema_before": (
                    _schema_manifest(destination_schema_before)
                    if destination_schema_before is not None
                    else None
                ),
                "metadata": manifest_metadata(
                    rows=manifest.rows,
                    schema_available=bool(destination_schema_after or schema),
                    row_provenance=(
                        "exact" if destination_rows_after is not None else "local_manifest"
                    ),
                    schema_provenance=(
                        "captured" if destination_schema_after is not None else "local_manifest"
                    ),
                ),
            },
            verification=verification,
        )
    except Exception:
        if session is not None:
            destination.abort(session)
        raise
    finally:
        close = getattr(source_iterator, "close", None)
        if close is not None:
            close()
