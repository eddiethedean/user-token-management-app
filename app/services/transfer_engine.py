"""Provider-neutral extract/load orchestration."""

from __future__ import annotations

import logging
from collections.abc import Callable
from itertools import chain
from typing import Literal, TypeVar, cast

from sqlalchemy.orm import Session

from app.application.ports import Clock, Sleeper, system_clock, system_sleep
from app.config import Settings
from app.connectors.base import (
    AbortResult,
    ColumnSchema,
    DestinationManifest,
    DestinationRowCounter,
    DestinationSchemaInspector,
    DestinationWriter,
    ObjectSchema,
    ProviderCapabilities,
    SourceReader,
    TransferBatch,
)
from app.connectors.errors import ConnectorError, TransferErrorCode
from app.connectors.locators import (
    DefinitionSnapshot,
    PostgresAppendPolicy,
    PostgresReplacePolicy,
    PostgresUpsertPolicy,
)
from app.connectors.registry import (
    destination_writer_for,
    route_allowed,
    source_reader_for,
    writer_enabled,
)
from app.domain.feedback import DataImpact
from app.models import PipelineRun
from app.services import pipeline_runs
from app.services.pipeline_metadata import manifest_metadata
from app.services.pipeline_state import RunConflictError

CancelCheck = Callable[[], bool]
RoleT = TypeVar("RoleT")
log = logging.getLogger(__name__)


def _require_role(
    candidate: object,
    role: type[RoleT],
    label: str,
    capability: Literal["source", "destination"],
) -> RoleT:
    """Validate an injected adapter before any transfer state changes or I/O."""

    if not isinstance(candidate, role):
        raise ConnectorError(
            TransferErrorCode.INTERNAL_ERROR,
            f"The selected provider does not support {label}.",
            retryable=False,
        )
    capabilities = getattr(candidate, "capabilities", None)
    if not isinstance(capabilities, ProviderCapabilities) or not getattr(capabilities, capability):
        raise ConnectorError(
            TransferErrorCode.INTERNAL_ERROR,
            f"The selected provider does not support {label}.",
            retryable=False,
        )
    return cast(RoleT, candidate)


def _destination_row_count(destination, credentials, locator) -> int | None:
    """Read destination counts as best-effort telemetry, never as a run prerequisite."""

    if (
        not isinstance(destination, DestinationRowCounter)
        or not destination.capabilities.exact_row_counts
    ):
        return None
    try:
        value = destination.count_rows(credentials, locator)
    except Exception:
        return None
    return int(value) if value is not None else None


def _reconcile_write_result(
    snapshot: DefinitionSnapshot,
    *,
    source_rows: int,
    manifest: DestinationManifest,
    destination_rows_before: int | None,
    destination_rows_after: int | None,
    destination: DestinationWriter,
) -> tuple[str, str | None, dict[str, object]]:
    """Compare destination results using the selected write policy's semantics."""

    policy = snapshot.write_policy
    facts: dict[str, object] = {
        "source_rows": source_rows,
        "loaded_rows": manifest.rows,
        "destination_rows_before": destination_rows_before,
        "destination_rows_after": destination_rows_after,
        "destination_row_delta": (
            destination_rows_after - destination_rows_before
            if destination_rows_before is not None and destination_rows_after is not None
            else None
        ),
        "write_policy": policy.kind,
    }
    weak_level = destination.capabilities.verification_level
    if weak_level == "exact":
        weak_level = "provider_write_count"

    if isinstance(policy, PostgresAppendPolicy):
        expected_rows = source_rows
        facts["expected_rows"] = expected_rows
        if manifest.rows != expected_rows:
            return (
                weak_level,
                "Append reconciliation failed: the destination reported "
                f"{manifest.rows} rows for {source_rows} source rows. Inspect the destination before retrying.",
                facts,
            )
        if destination_rows_before is None or destination_rows_after is None:
            return "provider_write_count", None, facts
        expected_delta = destination_rows_after - destination_rows_before
        if expected_delta != expected_rows:
            # The aggregate table count can include writes from other runs or
            # external clients. The statement manifest is per-run evidence;
            # when the global delta disagrees, keep the run successful but
            # downgrade verification because the extra change is unattributable.
            facts["verification_limitation"] = "aggregate_count_changed_during_transfer"
            return "provider_write_count", None, facts
        return "exact", None, facts

    if isinstance(policy, PostgresReplacePolicy):
        expected_rows = source_rows
        facts["expected_rows"] = expected_rows
        if manifest.rows != expected_rows:
            return (
                weak_level,
                "Replace reconciliation failed: the destination reported "
                f"{manifest.rows} rows for {source_rows} source rows. Inspect the destination before retrying.",
                facts,
            )
        if destination_rows_after is None:
            return "provider_write_count", None, facts
        if destination_rows_after != expected_rows:
            return (
                weak_level,
                "Replace reconciliation failed: the destination contains "
                f"{destination_rows_after} rows after replacing it with {expected_rows} source rows. "
                "Inspect the destination before retrying.",
                facts,
            )
        return "exact", None, facts

    if isinstance(policy, PostgresUpsertPolicy):
        expected_rows_text = manifest.details.get("expected_rows")
        if expected_rows_text is not None:
            try:
                expected_rows = int(expected_rows_text)
            except (TypeError, ValueError):
                expected_rows = -1
            facts["expected_rows"] = expected_rows
            if expected_rows < 0 or manifest.rows != expected_rows:
                return (
                    weak_level,
                    "Upsert reconciliation failed: the destination did not apply the expected "
                    f"{expected_rows} distinct source keys (reported {manifest.rows}). "
                    "Inspect the destination before retrying.",
                    facts,
                )
            return "exact", None, facts
        if manifest.rows < 0 or manifest.rows > source_rows:
            return (
                weak_level,
                "Upsert reconciliation failed: the destination reported an impossible row count. "
                "Inspect the destination before retrying.",
                facts,
            )
        # Upsert ignore and key-only upserts may legitimately write fewer rows
        # than the source because existing keys are left untouched.
        return "provider_write_count", None, facts

    return weak_level, None, facts


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


def _destination_schema_projection(
    destination_schema_after: ObjectSchema | None,
    source_schema: ObjectSchema,
) -> tuple[ObjectSchema, bool, str]:
    """Choose the persisted destination schema and accurately label its provenance."""

    selected = destination_schema_after or source_schema
    available = bool(selected.columns)
    if not available:
        provenance = "unavailable"
    elif destination_schema_after is not None:
        provenance = "captured"
    else:
        provenance = "local_manifest"
    return selected, available, provenance


def _inspect_destination_schema(destination, credentials, locator) -> ObjectSchema | None:
    if (
        not isinstance(destination, DestinationSchemaInspector)
        or not destination.capabilities.schema_inspection
    ):
        return None
    try:
        inspected = destination.inspect_object(credentials, locator)
    except Exception:
        return None
    return inspected if inspected.columns else None


def _demo_stage_pause(settings: Settings, *, sleeper: Sleeper = system_sleep) -> None:
    """Keep local demo stages visible without slowing tests or real transfers."""

    if settings.is_demo_mode and settings.app_env != "test":
        sleeper(0.7)


def _abort_quietly(destination, session) -> AbortResult:
    try:
        result = destination.abort(session)
        if result == AbortResult.ROLLED_BACK:
            return AbortResult.ROLLED_BACK
        # Cleanup is safe only when the connector explicitly confirms the
        # rollback.  None, False, and unknown connector values are all
        # treated as unresolved so callers cannot offer an unsafe retry.
        return AbortResult.UNCERTAIN
    except Exception as exc:
        exception_type = type(exc).__name__
        log.warning(
            "Destination cleanup failed (%s)",
            exception_type,
            extra={"exception_type": exception_type},
        )
        return AbortResult.UNCERTAIN


def _cancel_after_abort(db, run: PipelineRun, *, lease_token: str, destination, session) -> None:
    aborted = _abort_quietly(destination, session)
    if aborted == AbortResult.UNCERTAIN:
        impact = DataImpact.UNCERTAIN
    elif run.loaded_rows:
        impact = DataImpact.ROLLED_BACK
    else:
        impact = DataImpact.UNCHANGED
    pipeline_runs.cancel_claimed_run(
        db,
        run,
        lease_token=lease_token,
        data_impact=impact,
    )


def _validate_upsert_policy(
    destination,
    credentials,
    snapshot: DefinitionSnapshot,
    source_schema: ObjectSchema,
    destination_schema: ObjectSchema | None,
) -> ObjectSchema | None:
    policy = snapshot.write_policy
    if not isinstance(policy, PostgresUpsertPolicy):
        return destination_schema
    if destination_schema is None and (
        not isinstance(destination, DestinationSchemaInspector)
        or not destination.capabilities.schema_inspection
    ):
        raise ConnectorError(
            TransferErrorCode.SCHEMA_DRIFT,
            "Destination schema inspection is required for PostgreSQL upsert.",
            retryable=False,
        )
    inspected = destination_schema or destination.inspect_object(credentials, snapshot.destination)
    conflict_columns = tuple(policy.conflict_columns)
    eligible = {tuple(inspected.primary_key), *map(tuple, inspected.unique_constraints)}
    eligible.discard(())
    if conflict_columns not in eligible:
        raise ConnectorError(
            TransferErrorCode.SCHEMA_DRIFT,
            "The saved upsert key is no longer a primary or unique destination constraint.",
            retryable=False,
        )
    source_columns = {column.name for column in source_schema.columns}
    if not set(conflict_columns).issubset(source_columns):
        raise ConnectorError(
            TransferErrorCode.SCHEMA_DRIFT,
            "The source does not contain every column required by the destination upsert key.",
            retryable=False,
        )
    return inspected


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
    lease_lost: CancelCheck = lambda: False,
    # Resolvers are typed ports and are also validated at runtime before the
    # transfer starts so dynamic composition cannot bypass role capabilities.
    source_resolver: Callable[[str], SourceReader] | None = None,
    destination_resolver: Callable[[str], DestinationWriter] | None = None,
    route_policy: Callable[[str, str], bool] | None = None,
    writer_policy: Callable[[str], bool] | None = None,
    clock: Clock = system_clock,
    sleeper: Sleeper = system_sleep,
) -> None:
    is_route_allowed = route_policy or route_allowed
    is_writer_enabled = writer_policy or (
        lambda provider: writer_enabled(provider, settings=settings)
    )
    if not is_route_allowed(snapshot.source_provider, snapshot.destination_provider):
        raise ConnectorError(
            TransferErrorCode.PERMISSION_DENIED,
            "This source and destination route is not approved for execution.",
            retryable=False,
        )
    source = _require_role(
        (
            source_resolver(snapshot.source_provider)
            if source_resolver is not None
            else source_reader_for(snapshot.source_provider)
        ),
        SourceReader,
        "source extraction",
        "source",
    )
    destination = _require_role(
        (
            destination_resolver(snapshot.destination_provider)
            if destination_resolver is not None
            else destination_writer_for(snapshot.destination_provider)
        ),
        DestinationWriter,
        "destination writes",
        "destination",
    )
    # The resolved policy is authoritative in every mode.  The default policy
    # already accounts for demo capabilities, while injected policies must not
    # be bypassed by the executor.
    if not is_writer_enabled(snapshot.destination_provider):
        raise ConnectorError(
            TransferErrorCode.PERMISSION_DENIED,
            "This destination writer is not enabled.",
            retryable=False,
        )
    if lease_lost():
        raise RunConflictError("This worker no longer holds the run lease.")
    if cancel_requested():
        pipeline_runs.cancel_claimed_run(db, run, lease_token=lease_token)
        return

    pipeline_runs.heartbeat(
        db, run, lease_token=lease_token, lease_seconds=settings.pipeline_lease_seconds
    )
    _demo_stage_pause(settings, sleeper=sleeper)
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
    _demo_stage_pause(settings, sleeper=sleeper)

    extracted_rows = 0
    extracted_bytes = 0
    started = clock()
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
    destination_committed = False
    try:
        # Fetch only the first batch before preparing the destination so a
        # source without portable schema metadata can still define its table.
        # The remaining batches stay in the iterator and are loaded as they
        # arrive; retaining the complete source in memory made large runs
        # exceed the application's bounded-batch contract.
        first_batch = next(source_iterator, None)
        if first_batch is not None:
            if lease_lost():
                raise RunConflictError("This worker no longer holds the run lease.")
            if cancel_requested():
                pipeline_runs.cancel_claimed_run(db, run, lease_token=lease_token)
                return
            frame = first_batch.frame
            if not source_schema.columns:
                schema = ObjectSchema(
                    locator=snapshot.source,
                    columns=tuple(
                        ColumnSchema(name=name, data_type=str(dtype), nullable=True)
                        for name, dtype in frame.schema.items()
                    ),
                    primary_key=source_schema.primary_key,
                    unique_constraints=source_schema.unique_constraints,
                )

        destination_schema_before = _validate_upsert_policy(
            destination,
            destination_credentials,
            snapshot,
            schema,
            destination_schema_before,
        )

        pipeline_runs.transition(
            db,
            run,
            "loading",
            lease_token=lease_token,
            message="Starting destination load.",
        )
        _demo_stage_pause(settings, sleeper=sleeper)
        session = destination.prepare_destination(
            destination_credentials,
            snapshot.destination,
            schema,
            snapshot.write_policy,
            run_id=run.id,
        )
        loaded_bytes = 0
        for batch in chain((first_batch,) if first_batch is not None else (), source_iterator):
            if lease_lost():
                raise RunConflictError("This worker no longer holds the run lease.")
            if cancel_requested():
                _cancel_after_abort(
                    db, run, lease_token=lease_token, destination=destination, session=session
                )
                return
            if (clock() - started).total_seconds() > settings.pipeline_max_run_seconds:
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
            # Release the application-database write lock before remote I/O so
            # the independent lease keeper can renew the run during a slow
            # destination write.
            db.commit()
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
            _demo_stage_pause(settings, sleeper=sleeper)
        if lease_lost():
            raise RunConflictError("This worker no longer holds the run lease.")
        if cancel_requested():
            _cancel_after_abort(
                db, run, lease_token=lease_token, destination=destination, session=session
            )
            return
        pipeline_runs.transition(
            db,
            run,
            "verifying",
            lease_token=lease_token,
            message="Finalizing destination write.",
        )
        _demo_stage_pause(settings, sleeper=sleeper)
        if lease_lost():
            raise RunConflictError("This worker no longer holds the run lease.")
        if cancel_requested():
            _cancel_after_abort(
                db, run, lease_token=lease_token, destination=destination, session=session
            )
            return
        manifest = destination.finalize(session)
        destination_committed = True
        destination_rows_after = _destination_row_count(
            destination, destination_credentials, snapshot.destination
        )
        destination_schema_after = _inspect_destination_schema(
            destination, destination_credentials, snapshot.destination
        )
        (
            persisted_destination_schema,
            destination_schema_available,
            destination_schema_provenance,
        ) = _destination_schema_projection(destination_schema_after, schema)
        verification_level, reconciliation_error, verification_facts = _reconcile_write_result(
            snapshot,
            source_rows=extracted_rows,
            manifest=manifest,
            destination_rows_before=destination_rows_before,
            destination_rows_after=destination_rows_after,
            destination=destination,
        )
        verification = {
            **verification_facts,
            "source_bytes": extracted_bytes,
            "loaded_bytes": manifest.bytes or loaded_bytes,
            "verification_level": verification_level,
        }
        if reconciliation_error:
            pipeline_runs.fail_run(
                db,
                run,
                lease_token=lease_token,
                code=TransferErrorCode.VERIFICATION_FAILED,
                summary=reconciliation_error,
                needs_reconciliation=True,
                verification_facts=verification,
            )
            return
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
                    schema_provenance=("captured" if schema.columns else "unavailable"),
                ),
            },
            destination_manifest={
                "rows": manifest.rows,
                "bytes": manifest.bytes,
                "checksum": manifest.checksum,
                "remote_id": manifest.remote_id,
                "details": dict(manifest.details),
                "schema": _schema_manifest(persisted_destination_schema),
                "schema_before": (
                    _schema_manifest(destination_schema_before)
                    if destination_schema_before is not None
                    else None
                ),
                "metadata": manifest_metadata(
                    rows=manifest.rows,
                    schema_available=destination_schema_available,
                    row_provenance=(
                        "exact" if destination_rows_after is not None else "local_manifest"
                    ),
                    schema_provenance=destination_schema_provenance,
                ),
            },
            verification=verification,
        )
    except Exception as exc:
        abort_result = AbortResult.ROLLED_BACK
        if session is not None:
            abort_result = _abort_quietly(destination, session)
        if destination_committed:
            raise ConnectorError(
                TransferErrorCode.PUBLISH_UNCERTAIN,
                "The destination committed, but final run-state persistence was not confirmed.",
                retryable=False,
            ) from exc
        if abort_result == AbortResult.UNCERTAIN and not isinstance(exc, RunConflictError):
            raise ConnectorError(
                TransferErrorCode.PUBLISH_UNCERTAIN,
                "Destination cleanup could not be confirmed after the transfer failed.",
                retryable=False,
            ) from exc
        raise
    finally:
        close = getattr(source_iterator, "close", None)
        if close is not None:
            close()
