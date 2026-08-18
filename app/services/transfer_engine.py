"""Provider-neutral extract/load orchestration."""

from __future__ import annotations

from collections.abc import Callable

from sqlalchemy.orm import Session

from app.config import Settings
from app.connectors.base import ObjectSchema, TransferBatch
from app.connectors.errors import ConnectorError, TransferErrorCode
from app.connectors.locators import DefinitionSnapshot
from app.connectors.registry import connector_for, writer_enabled
from app.models import PipelineRun, utcnow
from app.services import pipeline_runs

CancelCheck = Callable[[], bool]


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
    source.test_connection(source_credentials)
    destination.test_connection(destination_credentials)
    source_schema = source.inspect_object(source_credentials, snapshot.source)
    pipeline_runs.transition(
        db,
        run,
        "extracting",
        lease_token=lease_token,
        message="Source and destination connections validated.",
    )

    batches: list[TransferBatch] = []
    extracted_rows = 0
    extracted_bytes = 0
    started = utcnow()
    for batch in source.extract(
        source_credentials,
        snapshot.source,
        batch_rows=settings.pipeline_batch_rows,
        batch_bytes=settings.pipeline_batch_target_bytes,
    ):
        if cancel_requested():
            pipeline_runs.cancel_claimed_run(db, run, lease_token=lease_token)
            return
        if (utcnow() - started).total_seconds() > settings.pipeline_max_run_seconds:
            raise ConnectorError(TransferErrorCode.RUN_TIMEOUT, "The run exceeded its time limit.")
        extracted_rows += batch.row_count
        extracted_bytes += batch.byte_count
        if extracted_bytes > settings.pipeline_max_source_bytes:
            raise ConnectorError(
                TransferErrorCode.SOURCE_LIMIT_EXCEEDED,
                "The source exceeded the configured size limit.",
            )
        batches.append(batch)
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
        db.commit()

    schema = source_schema
    if batches:
        frame = batches[0].frame
        from app.connectors.base import ColumnSchema

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
    session = destination.prepare_destination(
        destination_credentials,
        snapshot.destination,
        schema,
        snapshot.write_policy,
        run_id=run.id,
    )
    try:
        loaded_rows = 0
        loaded_bytes = 0
        for batch in batches:
            if cancel_requested():
                destination.abort(session)
                pipeline_runs.cancel_claimed_run(db, run, lease_token=lease_token)
                return
            result = destination.write_batch(session, batch)
            loaded_rows += result.rows_acknowledged
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
        pipeline_runs.transition(
            db,
            run,
            "verifying",
            lease_token=lease_token,
            message="Finalizing destination write.",
        )
        manifest = destination.finalize(session)
        verification = {
            "source_rows": extracted_rows,
            "loaded_rows": manifest.rows or loaded_rows,
            "source_bytes": extracted_bytes,
            "loaded_bytes": manifest.bytes or loaded_bytes,
        }
        pipeline_runs.complete_run(
            db,
            run,
            lease_token=lease_token,
            source_manifest={"rows": extracted_rows, "bytes": extracted_bytes},
            destination_manifest={
                "rows": manifest.rows,
                "bytes": manifest.bytes,
                "checksum": manifest.checksum,
                "remote_id": manifest.remote_id,
                "details": dict(manifest.details),
            },
            verification=verification,
        )
    except Exception:
        destination.abort(session)
        raise
