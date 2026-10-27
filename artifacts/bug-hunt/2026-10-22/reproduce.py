"""Characterization tests: passing assertions reproduce current defects."""

import json
import uuid
from datetime import timedelta
from unittest.mock import Mock

import polars as pl
import psycopg
import pytest
from sqlalchemy import select

from app.config import get_settings
from app.connectors.base import ObjectSchema, ColumnSchema, TransferBatch
from app.connectors.csv_source import CsvSourceConnector
from app.connectors.errors import ConnectorError, TransferErrorCode
from app.connectors.locators import (
    DefinitionSnapshot,
    PostgresAppendPolicy,
    DatabricksCreatePolicy,
    postgres_table,
)
from app.connectors.postgres import PostgresConnector, connect
from app.database import SessionLocal
from app.models import PipelineRun, DatabricksPublication, utcnow
from app.services.csv_uploads import inspect_csv
from app.services.pipeline_runs import claim_run
from app.services.transfer_engine import execute_transfer
from app.services.databricks_publication import (
    record_publication_event,
    process_pending_publication_recovery,
)
from tests.postgres_support import connector_settings
from tests.test_csv_decimal_precision import _csv_credentials, _locator
from tests.test_databricks_connector import _SQLHarness, _sql_connector, _table_locator
from tests.test_databricks_coordination import (
    _enabled_databricks_settings,
    _saved_databricks_credentials,
    _locator as dbx_locator,
)

pytest_plugins = ["tests.conftest"]


def transfer_frame(credentials, table, frame, schema=None):
    connector = PostgresConnector(connector_settings())
    locator = postgres_table("public", table)
    schema = schema or ObjectSchema(
        locator=locator, columns=tuple(ColumnSchema(n, str(t)) for n, t in frame.schema.items())
    )
    session = connector.prepare_destination(
        credentials, locator, schema, PostgresAppendPolicy(), run_id=str(uuid.uuid4())
    )
    try:
        connector.write_batch(
            session,
            TransferBatch(
                frame=frame, row_count=frame.height, byte_count=frame.estimated_size(), sequence=1
            ),
        )
        return connector.finalize(session)
    finally:
        connector.abort(session)


@pytest.mark.parametrize("value", ["１２", "١٢.٣", "2026-W43-4"])
def test_csv_profile_accepts_values_extraction_rejects(value):
    content = ("value,label\n" + value + ",ok\n").encode()
    inspection = inspect_csv("boundary.csv", content)
    assert inspection.columns[0].inferred_type in {"integer", "decimal", "date"}
    with pytest.raises(ConnectorError) as failure:
        list(
            CsvSourceConnector().extract(
                _csv_credentials(content, inspection), _locator(), batch_rows=100, batch_bytes=10000
            )
        )
    print(
        "CSV accepted",
        repr(value),
        inspection.columns[0].inferred_type,
        "then failed:",
        failure.value.code,
    )
    assert failure.value.code == TransferErrorCode.UNSUPPORTED_TYPE


def test_databricks_rejects_real_csv_timestamp_schema():
    content = b"occurred\n2026-10-22T12:30:45\n"
    inspection = inspect_csv("timestamps.csv", content)
    schema = CsvSourceConnector().inspect_object(_csv_credentials(content, inspection), _locator())
    assert schema.columns[0].data_type == "Datetime(time_unit='us', time_zone=None)"
    harness = _SQLHarness(exists=False)
    connector, credentials = _sql_connector(harness)
    credentials["_publication_event"] = lambda *a, **kw: None
    with pytest.raises(ConnectorError) as failure:
        connector.prepare_destination(
            credentials,
            _table_locator(),
            schema,
            DatabricksCreatePolicy(),
            run_id=str(uuid.uuid4()),
        )
    assert failure.value.code == TransferErrorCode.UNSUPPORTED_TYPE
    assert not any("CREATE TABLE" in sql for sql, _ in harness.statements)
    print("Databricks rejected CSV type:", schema.columns[0].data_type)


def test_native_postgres_timestamp_falsely_fails_schema_drift(
    access_app, make_user, postgres_credentials
):
    owner = make_user("timestamp.audit@example.gov")
    settings = get_settings()
    with connect(postgres_credentials, connector_settings()) as conn:
        conn.execute("CREATE TABLE timestamp_source (occurred timestamp)")
        conn.execute("INSERT INTO timestamp_source VALUES ('2026-10-22 12:30:45')")
    snapshot = DefinitionSnapshot(
        name="timestamp audit",
        source_provider="postgres",
        destination_provider="postgres",
        source=postgres_table("public", "timestamp_source"),
        destination=postgres_table("public", "timestamp_target"),
        write_policy=PostgresAppendPolicy(),
    )
    with SessionLocal() as db:
        run = PipelineRun(user_id=owner.id, definition_snapshot_json=snapshot.model_dump_json())
        db.add(run)
        db.commit()
        run, lease = claim_run(db, worker_id="audit", lease_seconds=120, run_id=run.id)
        with pytest.raises(ConnectorError) as failure:
            execute_transfer(
                db,
                run=run,
                lease_token=lease,
                snapshot=snapshot,
                source_credentials=postgres_credentials,
                destination_credentials=postgres_credentials,
                settings=settings,
                cancel_requested=lambda: False,
                source_resolver=lambda _: PostgresConnector(connector_settings()),
                destination_resolver=lambda _: PostgresConnector(connector_settings()),
                route_policy=lambda *_: True,
                writer_policy=lambda _: True,
            )
        assert failure.value.code == TransferErrorCode.SCHEMA_DRIFT
        assert failure.value.summary == "The source schema changed during extraction."
        print("Unchanged PostgreSQL timestamp fails:", failure.value.code, failure.value.summary)
    with connect(postgres_credentials, connector_settings()) as conn:
        assert conn.execute("SELECT to_regclass('public.timestamp_target')").fetchone() == (None,)


def test_serial_import_leaves_next_generated_key_colliding(postgres_credentials):
    with connect(postgres_credentials, connector_settings()) as conn:
        conn.execute("CREATE TABLE serial_target (id SERIAL PRIMARY KEY, label TEXT)")
    result = transfer_frame(
        postgres_credentials, "serial_target", pl.DataFrame({"id": [1, 2], "label": ["a", "b"]})
    )
    assert result.rows == 2
    with connect(postgres_credentials, connector_settings()) as conn:
        assert conn.execute("SELECT id FROM serial_target ORDER BY id").fetchall() == [(1,), (2,)]
        with pytest.raises(psycopg.errors.UniqueViolation):
            conn.execute("INSERT INTO serial_target(label) VALUES ('next generated')")
    print("Successful serial import causes next ordinary INSERT to violate primary key")


def test_existing_timestamp_target_silently_discards_source_timezone(postgres_credentials):
    with connect(postgres_credentials, connector_settings()) as conn:
        conn.execute("CREATE TABLE tz_source (occurred timestamptz)")
        conn.execute("INSERT INTO tz_source VALUES ('2026-09-17 08:00:00-04')")
        conn.execute("CREATE TABLE tz_target (occurred timestamp)")
    connector = PostgresConnector(connector_settings())
    source = postgres_table("public", "tz_source")
    schema = connector.inspect_object(postgres_credentials, source)
    connector.preflight_destination(
        postgres_credentials, postgres_table("public", "tz_target"), schema, PostgresAppendPolicy()
    )
    batch = next(connector.extract(postgres_credentials, source, batch_rows=100, batch_bytes=10000))
    result = transfer_frame(postgres_credentials, "tz_target", batch.frame, schema)
    assert result.rows == 1
    with connect(postgres_credentials, connector_settings()) as conn:
        conn.execute("SET TIME ZONE 'America/New_York'")
        drift = conn.execute(
            "SELECT EXTRACT(EPOCH FROM d.occurred::timestamptz)-EXTRACT(EPOCH FROM s.occurred) FROM tz_target d,tz_source s"
        ).fetchone()[0]
    assert drift == 14400
    print("Existing timestamp target drift seconds:", drift)


def test_committed_publication_crash_has_no_recovery(access_app, make_user):
    owner = make_user("committed.audit@example.gov")
    settings = _enabled_databricks_settings()
    with SessionLocal() as db:
        credentials = _saved_databricks_credentials(db, settings, owner)
        run = PipelineRun(
            user_id=owner.id,
            definition_snapshot_json="{}",
            status="loading",
            stage="transfer",
            lease_token="audit",
            lease_expires_at=utcnow() + timedelta(minutes=1),
        )
        db.add(run)
        db.commit()
        run_id = run.id
        common = dict(
            db=db,
            run_id=run_id,
            lease_token="audit",
            user_id=owner.id,
            locator=dbx_locator(),
            connection_generation=credentials["connection_generation"],
            write_mode="databricks_append",
        )
        record_publication_event(
            **common,
            event="prepare_started",
            staging_name="private.staging.data_mover_" + run_id.replace("-", ""),
        )
        record_publication_event(**common, event="staging_ready")
        record_publication_event(**common, event="publish_submitted")
        record_publication_event(**common, event="committed")
        # Crash immediately after the committed event; lease recovery later terminates the run.
        run.status = "failed_needs_reconciliation"
        run.stage = "failed"
        run.lease_expires_at = utcnow() - timedelta(seconds=1)
        db.commit()
    connector = Mock()
    result = process_pending_publication_recovery(
        settings=settings, session_factory=SessionLocal, connector_resolver=lambda _: connector
    )
    assert result is False
    connector.cleanup_staging.assert_not_called()
    with SessionLocal() as db:
        record = db.get(DatabricksPublication, run_id)
        assert record.state == "committed" and record.confirmed_at is not None
    print("Committed crash state is permanently excluded from staging recovery")


def test_timezone_loss_is_reported_as_success_by_real_transfer_engine(
    access_app, make_user, postgres_credentials
):
    owner = make_user("tz.engine.audit@example.gov")
    with connect(postgres_credentials, connector_settings()) as conn:
        conn.execute("CREATE TABLE tz_engine_source (occurred timestamptz)")
        conn.execute("INSERT INTO tz_engine_source VALUES ('2026-09-17 08:00:00-04')")
        conn.execute("CREATE TABLE tz_engine_target (occurred timestamp)")
    snapshot = DefinitionSnapshot(
        name="timezone audit",
        source_provider="postgres",
        destination_provider="postgres",
        source=postgres_table("public", "tz_engine_source"),
        destination=postgres_table("public", "tz_engine_target"),
        write_policy=PostgresAppendPolicy(),
    )
    with SessionLocal() as db:
        run = PipelineRun(user_id=owner.id, definition_snapshot_json=snapshot.model_dump_json())
        db.add(run)
        db.commit()
        run, lease = claim_run(db, worker_id="audit", lease_seconds=120, run_id=run.id)
        execute_transfer(
            db,
            run=run,
            lease_token=lease,
            snapshot=snapshot,
            source_credentials=postgres_credentials,
            destination_credentials=postgres_credentials,
            settings=get_settings(),
            cancel_requested=lambda: False,
            source_resolver=lambda _: PostgresConnector(connector_settings()),
            destination_resolver=lambda _: PostgresConnector(connector_settings()),
            route_policy=lambda *_: True,
            writer_policy=lambda _: True,
        )
        db.refresh(run)
        assert run.status == "succeeded"
        print("Timezone-loss pipeline status:", run.status, "verification:", run.verification_json)
    with connect(postgres_credentials, connector_settings()) as conn:
        conn.execute("SET TIME ZONE 'America/New_York'")
        drift = conn.execute(
            "SELECT EXTRACT(EPOCH FROM d.occurred::timestamptz)-EXTRACT(EPOCH FROM s.occurred) FROM tz_engine_target d,tz_engine_source s"
        ).fetchone()[0]
        assert drift == 14400
