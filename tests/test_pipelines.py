"""Saved pipeline definitions and demo workspace coverage."""

from __future__ import annotations

import re

from sqlalchemy import select

from app.database import SessionLocal
from app.models import AuditEvent, PipelineDefinition, PipelineUpload
from app.services.csv_uploads import inspect_csv
from tests.helpers import csrf_from, web_login


def test_pipeline_workspace_renders_live_feedback_controls(client) -> None:
    web_login(client, next_path="/pipeline")
    response = client.get("/pipeline")

    assert response.status_code == 200
    assert "Pipeline workspace" in response.text
    assert 'role="tablist"' in response.text
    assert 'data-hedron-appearance="underline"' in response.text
    assert "Route setup" in response.text
    assert "Live transfer" in response.text
    assert "Saved routes" in response.text
    assert 'value="advana"' not in response.text
    assert 'value="mss"' not in response.text
    assert 'value="postgres"' not in response.text
    assert 'value="mcscop"' not in response.text
    assert 'id="pipeline-source-schema-select"' in response.text
    assert 'id="pipeline-target-table-select"' in response.text
    assert "Set up a connection first" in response.text
    assert "CSV file · Upload from device" in response.text
    assert 'id="pipeline-csv-file"' in response.text
    assert 'id="pipeline-csv-inspection"' in response.text
    assert "0/3 connections ready" in response.text
    assert "Set up at least one connection" in response.text
    assert "hedron-alert-warning" in response.text
    assert "hedron-badge-info" in response.text


def test_pipeline_workspace_only_lists_configured_connections(client, demo_connections) -> None:
    web_login(client, next_path="/pipeline")
    response = client.get("/pipeline")

    assert response.status_code == 200
    assert "3/3 connections ready" in response.text
    assert "hedron-process-flow" in response.text
    assert "hedron-alert-success" in response.text
    assert "hedron-badge-success" in response.text
    assert 'value="mss"' in response.text
    assert 'value="postgres"' in response.text
    assert 'value="mcscop"' in response.text
    assert "PostgreSQL 16" in response.text
    assert "Palantir Foundry" in response.text
    assert "Create a new table" in response.text
    assert "Schema &amp; row counts" in response.text
    assert "Pre-run review" in response.text
    run_button = re.search(r'<button[^>]+data-pipeline-start="true"[^>]*>', response.text)
    assert run_button is not None
    assert "disabled" in run_button.group(0)
    assert "Save this pipeline to enable runs." in response.text
    mode_select = re.search(
        r'<select[^>]+id="pipeline-mode-select"[^>]*>.*?</select>',
        response.text,
    )
    assert mode_select is not None
    assert 'value="replace" selected' in mode_select.group(0)
    assert 'value="upsert"' not in mode_select.group(0)
    assert 'hx-post="/pipeline/preview"' in response.text
    assert 'hx-get="/pipeline/preview"' not in response.text

    preview = client.post(
        "/pipeline/preview",
        data={
            "csrf_token": csrf_from(response.text),
            "source_provider": "csv",
            "source_schema": "uploaded",
            "source_table": "pending.csv",
            "destination_provider": "mss",
            "destination_schema": "ri.foundry.main.dataset.demo-operations",
            "destination_table": "mission_orders.parquet",
            "destination_table_new": "",
            "source_upload_id": "",
            "write_mode": "replace",
        },
        headers={"HX-Request": "true", "HX-Target": "pipeline-preview-region"},
    )
    assert preview.status_code == 200
    assert "Upload a CSV to inspect its schema" in preview.text
    assert "pipeline-schema-preview" in preview.text


def test_pipeline_surface_exposes_metadata_capabilities_and_accessible_regions(
    client, demo_connections
) -> None:
    web_login(client, next_path="/pipeline")
    response = client.get("/pipeline")

    assert response.status_code == 200
    assert "Route capabilities" in response.text
    assert "What will be known before and after the run" in response.text
    assert 'aria-label="Workspace"' in response.text
    assert 'aria-label="Account navigation"' in response.text
    assert 'role="tablist"' in response.text
    assert 'aria-live="polite"' in response.text
    assert "Schema: Catalog metadata" in response.text


MSS_DATASET = "ri.foundry.main.dataset.demo-operations"
MSS_FILE = "mission_orders.parquet"
MSS_DEST_DATASET = "ri.foundry.main.dataset.demo-destination"


def test_pipeline_can_be_saved_and_loaded_later(client, demo_connections) -> None:
    web_login(client, next_path="/pipeline")
    page = client.get("/pipeline")
    saved = client.post(
        "/pipeline/save",
        data={
            "csrf_token": csrf_from(page.text),
            "pipeline_id": "",
            "pipeline_name": "Mission orders to warehouse",
            "source_provider": "mss",
            "source_schema": MSS_DATASET,
            "source_table": MSS_FILE,
            "destination_provider": "postgres",
            "destination_schema": "public",
            "destination_table": "mission_orders",
            "write_mode": "append",
        },
    )

    assert saved.status_code == 303
    assert "notice=saved" in saved.headers["location"]
    assert "pipeline_id=" in saved.headers["location"]
    with SessionLocal() as db:
        pipeline = db.scalar(
            select(PipelineDefinition).where(
                PipelineDefinition.name == "Mission orders to warehouse"
            )
        )
        assert pipeline is not None
        assert pipeline.source_provider == "mss"
        assert pipeline.destination_provider == "postgres"
        assert pipeline.source_table == MSS_FILE
        assert pipeline.destination_schema == "public"
        assert pipeline.destination_table == "mission_orders"
        assert pipeline.destination_create is False
        assert (
            db.scalar(
                select(AuditEvent).where(
                    AuditEvent.event_type == "pipeline.created",
                    AuditEvent.actor_user_id == pipeline.user_id,
                )
            )
            is not None
        )

    reloaded = client.get(saved.headers["location"])
    assert reloaded.status_code == 200
    assert "Pipeline saved" in reloaded.text
    assert "Mission orders to warehouse" in reloaded.text
    assert f'data-pipeline-id="{pipeline.id}"' in reloaded.text
    assert 'data-pipeline-run="true"' in reloaded.text
    assert f'<option value="{MSS_DATASET}" selected>' in reloaded.text
    assert f'<option value="{MSS_FILE}" selected>' in reloaded.text
    assert '<option value="public" selected>' in reloaded.text
    assert '<option value="mission_orders" selected>' in reloaded.text


def test_saved_pipeline_requires_distinct_systems(client, demo_connections) -> None:
    web_login(client, next_path="/pipeline")
    page = client.get("/pipeline")
    response = client.post(
        "/pipeline/save",
        data={
            "csrf_token": csrf_from(page.text),
            "pipeline_id": "",
            "pipeline_name": "Invalid loop",
            "source_provider": "mss",
            "source_schema": MSS_DATASET,
            "source_table": MSS_FILE,
            "destination_provider": "mss",
            "destination_schema": MSS_DATASET,
            "destination_table": "readiness_rollup.parquet",
            "write_mode": "replace",
        },
    )

    assert response.status_code == 422
    assert "Source and destination must be different" in response.text


def test_pipeline_save_rejects_connections_that_are_not_setup(client) -> None:
    web_login(client, next_path="/pipeline")
    page = client.get("/pipeline")
    response = client.post(
        "/pipeline/save",
        data={
            "csrf_token": csrf_from(page.text),
            "pipeline_id": "",
            "pipeline_name": "Forged unavailable route",
            "source_provider": "mss",
            "source_schema": MSS_DATASET,
            "source_table": MSS_FILE,
            "destination_provider": "postgres",
            "destination_schema": "public",
            "destination_table": "readiness_events",
            "write_mode": "upsert",
        },
    )

    assert response.status_code == 422
    assert "Configure and validate the selected source connection" in response.text


def test_pipeline_can_be_saved_with_postgres_destination(client, demo_connections) -> None:
    web_login(client, next_path="/pipeline")
    page = client.get("/pipeline")
    response = client.post(
        "/pipeline/save",
        data={
            "csrf_token": csrf_from(page.text),
            "pipeline_id": "",
            "pipeline_name": "Readiness warehouse load",
            "source_provider": "mss",
            "source_schema": MSS_DATASET,
            "source_table": MSS_FILE,
            "destination_provider": "postgres",
            "destination_schema": "public",
            "destination_table": "readiness_events",
            "write_mode": "upsert",
        },
    )

    assert response.status_code == 303
    with SessionLocal() as db:
        pipeline = db.scalar(
            select(PipelineDefinition).where(PipelineDefinition.name == "Readiness warehouse load")
        )
        assert pipeline is not None
        assert pipeline.destination_provider == "postgres"


def test_pipeline_can_move_between_mss_and_postgres(client, demo_connections) -> None:
    web_login(client, next_path="/pipeline")
    page = client.get("/pipeline")
    response = client.post(
        "/pipeline/save",
        data={
            "csrf_token": csrf_from(page.text),
            "pipeline_id": "",
            "pipeline_name": "Document readiness export",
            "source_provider": "mss",
            "source_schema": MSS_DATASET,
            "source_table": MSS_FILE,
            "destination_provider": "postgres",
            "destination_schema": "staging",
            "destination_table": "readiness_events_stage",
            "write_mode": "append",
        },
    )

    assert response.status_code == 303
    with SessionLocal() as db:
        pipeline = db.scalar(
            select(PipelineDefinition).where(PipelineDefinition.name == "Document readiness export")
        )
        assert pipeline is not None
        assert pipeline.source_provider == "mss"
        assert pipeline.destination_provider == "postgres"


def test_pipeline_can_create_a_named_destination_table(client, demo_connections) -> None:
    web_login(client, next_path="/pipeline")
    page = client.get("/pipeline")
    response = client.post(
        "/pipeline/save",
        data={
            "csrf_token": csrf_from(page.text),
            "pipeline_id": "",
            "pipeline_name": "Create reporting table",
            "source_provider": "mss",
            "source_schema": MSS_DATASET,
            "source_table": MSS_FILE,
            "destination_provider": "postgres",
            "destination_schema": "reporting",
            "destination_table": "__new__",
            "destination_table_new": "mission_objects_daily",
            "write_mode": "replace",
        },
    )

    assert response.status_code == 303
    with SessionLocal() as db:
        pipeline = db.scalar(
            select(PipelineDefinition).where(PipelineDefinition.name == "Create reporting table")
        )
        assert pipeline is not None
        assert pipeline.destination_create is True
        assert pipeline.destination_schema == "reporting"
        assert pipeline.destination_table == "mission_objects_daily"

    refreshed = client.get("/pipeline")
    assert 'data-catalog-table="mission_objects_daily"' in refreshed.text


def test_pipeline_can_save_an_enter_committed_destination_table(client, demo_connections) -> None:
    web_login(client, next_path="/pipeline")
    page = client.get("/pipeline")
    response = client.post(
        "/pipeline/save",
        data={
            "csrf_token": csrf_from(page.text),
            "pipeline_name": "Enter committed table",
            "source_provider": "postgres",
            "source_schema": "public",
            "source_table": "readiness_events",
            "destination_provider": "mss",
            "destination_schema": MSS_DEST_DATASET,
            "destination_table": "__new__:enter_committed_table",
            "destination_table_new": "",
            "write_mode": "replace",
        },
    )

    assert response.status_code == 303
    with SessionLocal() as db:
        pipeline = db.scalar(
            select(PipelineDefinition).where(PipelineDefinition.name == "Enter committed table")
        )
        assert pipeline is not None
        assert pipeline.destination_create is True
        assert pipeline.destination_table == "enter_committed_table.snappy.parquet"


def test_csv_inference_detects_headers_and_conservative_types() -> None:
    inspection = inspect_csv(
        "readiness.csv",
        (
            b"event_id,ready,score,service_date,observed_at,notes,unused\n"
            b"101,true,98.5,2026-08-12,2026-08-12T14:30:00Z,nominal,\n"
            b"102,false,87,2026-08-13,2026-08-13T09:15:00Z,review,\n"
        ),
    )

    assert inspection.row_count == 2
    assert [column.name for column in inspection.columns] == [
        "event_id",
        "ready",
        "score",
        "service_date",
        "observed_at",
        "notes",
        "unused",
    ]
    assert [column.inferred_type for column in inspection.columns] == [
        "integer",
        "boolean",
        "decimal",
        "date",
        "datetime",
        "text",
        "empty",
    ]


def test_uploaded_csv_can_be_scanned_saved_and_reloaded(client, demo_connections) -> None:
    web_login(client, next_path="/pipeline")
    page = client.get("/pipeline")
    csv_content = (
        b"event_id,unit_name,ready,score,observed_at\n"
        b"1001,Alpha,true,98.4,2026-08-13T10:00:00Z\n"
        b"1002,Bravo,false,82.0,2026-08-13T10:05:00Z\n"
    )
    scanned = client.post(
        "/pipeline/csv/inspect",
        data={"csrf_token": csrf_from(page.text)},
        files={"csv_file": ("unit_readiness.csv", csv_content, "text/csv")},
        headers={"HX-Request": "true", "HX-Target": "pipeline-csv-inspection"},
    )

    assert scanned.status_code == 200
    assert "Schema detected" in scanned.text
    assert "unit_readiness.csv" in scanned.text
    assert "5 columns" in scanned.text
    assert "event_id" in scanned.text
    assert "integer" in scanned.text
    assert "datetime" in scanned.text
    upload_match = re.search(r'name="source_upload_id" value="([^"]+)"', scanned.text)
    assert upload_match is not None
    upload_id = upload_match.group(1)

    saved = client.post(
        "/pipeline/save",
        data={
            "csrf_token": csrf_from(page.text),
            "pipeline_id": "",
            "pipeline_name": "Uploaded readiness feed",
            "source_provider": "csv",
            "source_schema": "uploaded",
            "source_table": "unit_readiness.csv",
            "source_upload_id": upload_id,
            "destination_provider": "postgres",
            "destination_schema": "staging",
            "destination_table": "readiness_events_stage",
            "write_mode": "append",
        },
    )

    assert saved.status_code == 303
    with SessionLocal() as db:
        pipeline = db.scalar(
            select(PipelineDefinition).where(PipelineDefinition.name == "Uploaded readiness feed")
        )
        upload = db.get(PipelineUpload, upload_id)
        assert pipeline is not None
        assert upload is not None
        assert pipeline.source_provider == "csv"
        assert pipeline.source_upload_id == upload_id
        assert pipeline.source_schema == "uploaded"
        assert pipeline.source_table == "unit_readiness.csv"
        assert upload.row_count == 2
        assert upload.column_count == 5
        assert upload.content == csv_content
        assert len(upload.checksum_sha256) == 64

    reloaded = client.get(saved.headers["location"])
    assert reloaded.status_code == 200
    assert "Uploaded readiness feed" in reloaded.text
    assert "CSV file" in reloaded.text
    assert f'data-pipeline-source-upload-id="{upload_id}"' in reloaded.text
    assert "unit_readiness.csv" in reloaded.text


def test_csv_scan_rejects_duplicate_headers(client) -> None:
    web_login(client, next_path="/pipeline")
    page = client.get("/pipeline")
    response = client.post(
        "/pipeline/csv/inspect",
        data={"csrf_token": csrf_from(page.text)},
        files={"csv_file": ("duplicate.csv", b"unit,UNIT\nA,B\n", "text/csv")},
        headers={"HX-Request": "true", "HX-Target": "pipeline-csv-inspection"},
    )

    assert response.status_code == 422
    assert "column names must be unique" in response.text
