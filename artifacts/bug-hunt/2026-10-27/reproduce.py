"""Audit characterization tests: PASS means the documented defect is present.

Run from the repository root:
  .venv/bin/python -m pytest artifacts/bug-hunt/2026-10-27/reproduce.py -c pyproject.toml -s

All database servers and HTTP services are disposable local test fixtures.
"""

from __future__ import annotations

import io
import json
import uuid
from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import Mock

import polars as pl
import pytest

from app.config import get_settings
from app.connectors.base import ConnectionHealth, ProvisionedDataset
from app.connectors.csv_source import CsvSourceConnector
from app.connectors.errors import ConnectorError, TransferErrorCode
from app.connectors.foundry import FoundryConnector
from app.connectors.locators import (
    DatabricksCreatePolicy,
    DefinitionSnapshot,
    FoundryDatasetFilesLocator,
    FoundryReplaceFilePolicy,
    FoundryUploadLocator,
    PostgresAppendPolicy,
    postgres_table,
)
from app.connectors.mss import MssConnector
from app.connectors.postgres import PostgresConnector, connect
from app.database import SessionLocal
from app.models import DatabricksPublication, PipelineRun, User, UserSecret, utcnow
from app.services.csv_uploads import inspect_csv
from app.services.pipeline_runs import claim_run, janitor, record_reconciliation_review
from app.services.transfer_engine import execute_transfer
from tests.postgres_support import connector_settings
from tests.test_csv_decimal_precision import _csv_credentials, _locator
from tests.test_databricks_connector import _sql_connector, _SQLHarness, _table_locator

pytest_plugins = ["tests.conftest"]


def _execute_postgres(db, owner, credentials, *, settings=None):
    snapshot = DefinitionSnapshot(
        name="PostgreSQL type audit",
        source_provider="postgres",
        destination_provider="postgres",
        source=postgres_table("public", "audit_source"),
        destination=postgres_table("public", "audit_target"),
        write_policy=PostgresAppendPolicy(),
    )
    run = PipelineRun(user_id=owner.id, definition_snapshot_json=snapshot.model_dump_json())
    db.add(run)
    db.commit()
    run, lease = claim_run(db, worker_id="audit", lease_seconds=120, run_id=run.id)
    execute_transfer(
        db,
        run=run,
        lease_token=lease,
        snapshot=snapshot,
        source_credentials=credentials,
        destination_credentials=credentials,
        settings=settings or get_settings(),
        cancel_requested=lambda: False,
        source_resolver=lambda _: PostgresConnector(connector_settings()),
        destination_resolver=lambda _: PostgresConnector(connector_settings()),
        route_policy=lambda *_: True,
        writer_policy=lambda _: True,
    )
    return run


@pytest.mark.parametrize(
    ("destination_type", "source_value"),
    [("real", 1_000_000_001), ("double precision", 9_007_199_254_740_993)],
)
def test_existing_float_target_silently_rounds_integer(
    access_app, make_user, postgres_credentials, destination_type, source_value
):
    owner = make_user("numeric.audit@example.gov")
    cfg = connector_settings()
    with connect(postgres_credentials, cfg) as conn:
        conn.execute("CREATE TABLE audit_source (value bigint)")
        conn.execute(f"CREATE TABLE audit_target (value {destination_type})")
        conn.execute("INSERT INTO audit_source VALUES (%s)", (source_value,))
    connector = PostgresConnector(cfg)
    source_schema = connector.inspect_object(
        postgres_credentials, postgres_table("public", "audit_source")
    )
    connector.preflight_destination(
        postgres_credentials,
        postgres_table("public", "audit_target"),
        source_schema,
        PostgresAppendPolicy(),
    )
    with SessionLocal() as db:
        run = _execute_postgres(db, owner, postgres_credentials)
        assert run.status == "succeeded"
        assert json.loads(run.verification_json)["verification_level"] == "exact"
    with connect(postgres_credentials, cfg) as conn:
        actual = Decimal.from_float(conn.execute("SELECT value FROM audit_target").fetchone()[0])
    assert actual != Decimal(source_value)
    print(f"Integer {source_value} -> {destination_type} {actual}; run succeeded/exact")


def test_existing_timezone_issue_remains_on_current_head(
    access_app, make_user, postgres_credentials
):
    owner = make_user("timezone.audit@example.gov")
    cfg = connector_settings()
    with connect(postgres_credentials, cfg) as conn:
        conn.execute("CREATE TABLE audit_source (value timestamptz)")
        conn.execute("CREATE TABLE audit_target (value timestamp)")
        conn.execute("INSERT INTO audit_source VALUES ('2026-10-27 08:00:00-04')")
    with SessionLocal() as db:
        run = _execute_postgres(db, owner, postgres_credentials)
        assert run.status == "succeeded"
        assert json.loads(run.verification_json)["verification_level"] == "exact"
    with connect(postgres_credentials, cfg) as conn:
        conn.execute("SET TIME ZONE 'America/New_York'")
        shift = conn.execute(
            "SELECT extract(epoch FROM (t.value::timestamptz - s.value)) "
            "FROM audit_source s CROSS JOIN audit_target t"
        ).fetchone()[0]
    assert shift == 14400
    print(
        "Existing #27: timezone semantics still lost; 14400-second difference after succeeded/exact"
    )


@pytest.mark.parametrize("publication_state", ["uncertain", "cleanup_pending"])
def test_retention_deletes_pending_publication(access_app, make_user, publication_state):
    owner = make_user("retention.audit@example.gov")
    stale = utcnow() - timedelta(days=100)
    settings = get_settings()
    with SessionLocal() as db:
        run = PipelineRun(
            user_id=owner.id,
            definition_snapshot_json="{}",
            status="failed_needs_reconciliation"
            if publication_state == "uncertain"
            else "succeeded",
            stage="reconcile" if publication_state == "uncertain" else "verify",
            finished_at=stale,
            created_at=stale,
            queued_at=stale,
            started_at=stale,
            reconciliation_required=publication_state == "uncertain",
            data_impact="uncertain" if publication_state == "uncertain" else "verified",
        )
        db.add(run)
        db.flush()
        run_id = run.id
        publication = DatabricksPublication(
            run_id=run.id,
            user_id=owner.id,
            workspace_id="a" * 64,
            connection_generation="test-generation",
            target_key="b" * 64,
            active_target_key="b" * 64 if publication_state == "uncertain" else None,
            target_locator_json=_table_locator().model_dump_json(),
            write_mode="databricks_append",
            staging_name="private.staging.audit",
            state=publication_state,
            submission_id=run.id,
            submitted_at=stale,
            created_at=stale,
            updated_at=stale,
            confirmed_at=stale if publication_state == "cleanup_pending" else None,
        )
        db.add(publication)
        db.commit()
        if publication_state == "uncertain":
            record_reconciliation_review(db, user=owner, run_id=run.id)
        assert db.get(DatabricksPublication, run_id) is not None
    with SessionLocal() as db:
        counts = janitor(db, settings)
        assert counts["runs"] == 1
        assert db.get(DatabricksPublication, run_id) is None
    print(
        f"Janitor cascaded unresolved {publication_state} publication, including its fence/evidence"
    )


def test_databricks_loses_timestamp_ntz_identity(access_app):
    harness = _SQLHarness(
        exists=True, columns=[("occurred", "timestamp_ntz", "TIMESTAMP_NTZ", "YES")]
    )
    source, credentials = _sql_connector(harness)
    schema_ntz = source.inspect_object(credentials, _table_locator())
    harness.columns = [("occurred", "timestamp", "TIMESTAMP", "YES")]
    schema_tz = source.inspect_object(credentials, _table_locator())
    assert schema_ntz.columns == schema_tz.columns
    assert schema_ntz.schema_fingerprint == schema_tz.schema_fingerprint

    target_harness = _SQLHarness(exists=False)
    destination, credentials = _sql_connector(target_harness)
    credentials["_publication_event"] = lambda *a, **kw: None
    load = destination.prepare_destination(
        credentials,
        _table_locator(table="copy_of_ntz"),
        schema_ntz,
        DatabricksCreatePolicy(),
        run_id=str(uuid.uuid4()),
    )
    create = next(sql for sql, _ in target_harness.statements if sql.startswith("CREATE TABLE"))
    assert "`occurred` TIMESTAMP" in create
    assert "TIMESTAMP_NTZ" not in create
    destination.abort(load)
    print(
        "TIMESTAMP and TIMESTAMP_NTZ have identical schema fingerprints; NTZ copy declares TIMESTAMP"
    )


@pytest.mark.parametrize("batch_rows", [1, 2])
def test_foundry_decimal_override_depends_on_batch_size(
    access_app, make_user, postgres_credentials, tmp_path, batch_rows
):
    from tests.simulators.foundry import FOUNDRY_DATASET, FOUNDRY_TOKEN, FoundrySimulator

    owner = make_user("decimal.audit@example.gov")
    cfg = connector_settings()
    with connect(postgres_credentials, cfg) as conn:
        conn.execute("CREATE TABLE audit_source (amount text)")
        conn.execute("INSERT INTO audit_source VALUES ('1.2'), ('1.23')")
    simulator = FoundrySimulator()
    settings = get_settings().model_copy(
        update={"pipeline_spool_root": str(tmp_path), "pipeline_batch_rows": batch_rows}
    )
    target = FoundryUploadLocator(
        dataset_rid=FOUNDRY_DATASET, branch="master", file_name="copy.parquet"
    )
    snapshot = DefinitionSnapshot(
        name="Decimal override audit",
        source_provider="postgres",
        destination_provider="mss",
        source=postgres_table("public", "audit_source"),
        destination=target,
        write_policy=FoundryReplaceFilePolicy(column_type_overrides={"amount": "decimal"}),
    )
    with simulator.serve(), SessionLocal() as db:
        run = PipelineRun(user_id=owner.id, definition_snapshot_json=snapshot.model_dump_json())
        db.add(run)
        db.commit()
        run, lease = claim_run(db, worker_id="audit", lease_seconds=120, run_id=run.id)

        def execute():
            execute_transfer(
                db,
                run=run,
                lease_token=lease,
                snapshot=snapshot,
                source_credentials=postgres_credentials,
                destination_credentials={
                    "endpoint": simulator.base_url,
                    "token": FOUNDRY_TOKEN,
                    "dataset_rid": FOUNDRY_DATASET,
                },
                settings=settings,
                cancel_requested=lambda: False,
                source_resolver=lambda _: PostgresConnector(cfg),
                destination_resolver=lambda _: MssConnector(settings),
                route_policy=lambda *_: True,
                writer_policy=lambda _: True,
            )

        if batch_rows == 1:
            with pytest.raises(ConnectorError) as failure:
                execute()
            assert failure.value.code == TransferErrorCode.SCHEMA_DRIFT
            assert "incompatible column types" in failure.value.summary
            assert "copy.parquet" not in simulator.files
            print("Decimal override failed for 1.2 / 1.23 split across batches; no upload")
        else:
            execute()
            assert run.status == "succeeded"
            frame = pl.read_parquet(io.BytesIO(simulator.files["copy.parquet"]))
            assert frame["amount"].to_list() == [Decimal("1.20"), Decimal("1.23")]
            print("Identical Decimal override and data succeeded when both rows share a batch")


@pytest.mark.parametrize("format", ["csv", "parquet"])
def test_empty_foundry_source_is_reported_missing(access_app, tmp_path, format):
    from tests.simulators.foundry import FOUNDRY_DATASET, FOUNDRY_TOKEN, FoundrySimulator

    simulator = FoundrySimulator()
    path = "notes.csv" if format == "csv" else "readiness.parquet"
    if format == "csv":
        simulator.files[path] = b"value,label\n"
    else:
        buffer = io.BytesIO()
        pl.DataFrame(schema={"value": pl.Int64, "label": pl.String}).write_parquet(buffer)
        simulator.files[path] = buffer.getvalue()
    settings = get_settings().model_copy(update={"pipeline_spool_root": str(tmp_path)})
    with simulator.serve():
        connector = FoundryConnector(settings)
        credentials = {
            "endpoint": simulator.base_url,
            "token": FOUNDRY_TOKEN,
            "dataset_rid": FOUNDRY_DATASET,
        }
        source = FoundryDatasetFilesLocator(
            dataset_rid=FOUNDRY_DATASET, branch="master", file_paths=[path]
        )
        schema = connector.inspect_object(credentials, source)
        assert [column.name for column in schema.columns] == ["value", "label"]
        with pytest.raises(ConnectorError) as failure:
            list(connector.extract(credentials, source, batch_rows=10, batch_bytes=10000))
        assert failure.value.code == TransferErrorCode.SOURCE_NOT_FOUND
        assert "no CSV or Parquet files" in failure.value.summary
    print(
        f"Existing zero-row Foundry {format} is inspected successfully, then reported as no files"
    )


@pytest.mark.parametrize("value", ["T12:34:56", "12:34:56,123"])
def test_uploaded_csv_still_profiles_unparseable_time_values(value):
    content = ('value,label\n"' + value + '",ok\n').encode()
    inspection = inspect_csv("time.csv", content)
    assert inspection.columns[0].inferred_type == "time"
    credentials = _csv_credentials(content, inspection)
    credentials["quote_char"] = inspection.quote_char
    credentials["skip_initial_space"] = inspection.skip_initial_space
    with pytest.raises(ConnectorError) as failure:
        list(
            CsvSourceConnector().extract(credentials, _locator(), batch_rows=10, batch_bytes=10000)
        )
    assert failure.value.code == TransferErrorCode.UNSUPPORTED_TYPE
    print(f"Existing #157: CSV {value!r} profiles as time, then fails extraction")


def test_foundry_csv_still_profiles_unparseable_minute_timestamp(tmp_path):
    from app.connectors.foundry import _csv_file_scan

    path = tmp_path / "minutes.csv"
    path.write_bytes(b"occurred,label\n2026-10-27 12:30,ok\n")
    scan, profiles = _csv_file_scan(path)
    assert profiles[0].inferred_type == "datetime"
    with pytest.raises(pl.exceptions.InvalidOperationError):
        scan.collect()
    print(
        "Existing #157: Foundry CSV minute-only datetime profiles successfully, then fails extraction"
    )


def test_dataset_creation_overwrites_replacement_connection_health(
    access_app, make_user, monkeypatch
):
    from sqlalchemy import select

    import app.services.foundry_datasets as datasets
    from app.services.secrets import (
        decrypt_user_credentials_for_run,
        store_user_credentials,
        test_user_connection,
    )

    owner = make_user("dataset.race.audit@example.gov")
    settings = get_settings()
    old = {"endpoint": "https://old.demo.invalid", "token": "synthetic-old-token"}
    new = {"endpoint": "https://new.demo.invalid", "token": "synthetic-new-token"}
    with SessionLocal() as db:
        store_user_credentials(db, settings, user=owner, provider="mss", credentials=old)

    def interleaved_creation(credentials, *, parent_folder_rid, name):
        assert credentials["endpoint"] == old["endpoint"]
        with SessionLocal() as other:
            current_owner = other.get(User, owner.id)
            store_user_credentials(
                other, settings, user=current_owner, provider="mss", credentials=new
            )
            failed = Mock()
            failed.test_connection.return_value = ConnectionHealth(
                status="failed", message="Replacement endpoint failed", latency_ms=1
            )
            checked = test_user_connection(
                other,
                user=current_owner,
                provider="mss",
                settings=settings,
                connector_resolver=lambda _: failed,
            )
            assert checked.validation_status == "failed"
        return ProvisionedDataset(
            dataset_rid="ri.foundry.main.dataset.synthetic-created",
            name=name,
            parent_folder_rid=parent_folder_rid,
        )

    provisioner = SimpleNamespace(
        capabilities=SimpleNamespace(dataset_creation=True), create_dataset=interleaved_creation
    )
    monkeypatch.setattr(datasets, "dataset_provisioner_for", lambda _: provisioner)
    with SessionLocal() as db:
        datasets.create_foundry_dataset(
            db,
            settings,
            user=db.get(User, owner.id),
            provider="mss",
            parent_folder_rid="ri.compass.main.folder.synthetic",
            name="Audit dataset",
        )
    with SessionLocal() as db:
        stored = db.scalar(
            select(UserSecret).where(UserSecret.user_id == owner.id, UserSecret.provider == "mss")
        )
        current = decrypt_user_credentials_for_run(
            db, settings, user=db.get(User, owner.id), provider="mss"
        )
        assert current["endpoint"] == new["endpoint"]
        assert stored.validation_status == "connected"
        assert "successful dataset creation" in stored.validation_message
    print(
        "Existing #81: dataset creation on old credentials overwrote failed replacement health with connected"
    )
