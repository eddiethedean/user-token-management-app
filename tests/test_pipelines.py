"""Saved pipeline definitions and demo workspace coverage."""

from __future__ import annotations

import json
import re

import pytest
from sqlalchemy import select

from app.connectors.base import ColumnSchema, ObjectSchema
from app.connectors.locators import (
    FoundryDatasetFilesLocator,
    FoundryUploadLocator,
    PostgresUpsertPolicy,
    parse_locator,
    parse_write_policy,
)
from app.database import SessionLocal
from app.models import (
    AuditEvent,
    FoundryDataset,
    PipelineCatalogCache,
    PipelineDefinition,
    PipelineRun,
    PipelineUpload,
    UserSecret,
)
from app.services.catalogs import UserCatalog
from app.services.csv_uploads import inspect_csv
from tests.helpers import csrf_from, web_login


def test_pipeline_destination_selection_uses_request_writer_policy(access_app) -> None:
    from app.ui.routes.pipeline import _eligible_destinations

    connections = {
        provider: {"configured": True, "validation": "connected", "runtime": "demo"}
        for provider in ("mss", "mcscop", "postgres")
    }
    destinations = _eligible_destinations(
        connections,
        "postgres",
        writer_policy=lambda provider: provider == "mss",
    )

    assert [catalog.name for catalog in destinations] == ["mss"]


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
    assert 'data-hedron-flow-appearance="plain"' in response.text
    assert "hedron-alert-success" in response.text
    assert "hedron-badge-success" in response.text
    assert 'value="mss"' in response.text
    assert 'value="postgres"' in response.text
    assert 'value="mcscop"' in response.text
    assert "PostgreSQL 16" in response.text
    assert "Palantir Foundry" in response.text
    assert "Create a new file" in response.text
    assert "Schema &amp; row counts" in response.text
    assert "Pre-run review" in response.text
    run_button = re.search(r'<button[^>]+data-pipeline-start="true"[^>]*>', response.text)
    assert run_button is None
    assert "Save and Run" in response.text
    assert 'hx-get="/pipeline/saved-routes"' in response.text
    assert 'hx-trigger="click from:#pipeline-workspace-tabs-tab-2"' in response.text
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
    with SessionLocal() as db:
        assert db.scalar(select(PipelineCatalogCache.id).limit(1)) is not None

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
    assert "Upload required" in preview.text
    assert "pipeline-schema-preview" in preview.text


def test_pipeline_surface_exposes_metadata_capabilities_and_accessible_regions(
    client, demo_connections
) -> None:
    web_login(client, next_path="/pipeline")
    response = client.get("/pipeline")

    assert response.status_code == 200
    assert "Route capabilities" in response.text
    assert "What will be known before and after the run" in response.text
    assert "Source route" in response.text
    assert "Destination route" in response.text
    assert "Pre-transfer inspection and extraction visibility." in response.text
    assert "Write options and post-transfer verification." in response.text
    assert response.text.index('role="tablist"') < response.text.index("Route capabilities")
    assert 'aria-label="Workspace"' in response.text
    assert 'aria-label="Account navigation"' in response.text
    assert 'role="tablist"' in response.text
    assert 'aria-live="polite"' in response.text
    assert "Schema: Catalog metadata" in response.text


def test_pipeline_preview_provider_calls_do_not_block_event_loop(
    client, demo_connections, monkeypatch
) -> None:
    import asyncio
    import threading
    import time

    import httpx2

    web_login(client, next_path="/pipeline")
    page = client.get("/pipeline")
    csrf_token = csrf_from(page.text)
    original = UserCatalog.list_namespaces
    provider_threads = []

    def slow_list_namespaces(self, provider):
        provider_threads.append(threading.get_ident())
        time.sleep(0.2)
        return original(self, provider)

    monkeypatch.setattr(UserCatalog, "list_namespaces", slow_list_namespaces)

    async def exercise() -> None:
        loop_thread = threading.get_ident()
        ticks = []

        async def ticker() -> None:
            for _ in range(100):
                ticks.append(time.monotonic())
                await asyncio.sleep(0.01)

        async with httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=client.app),
            base_url="http://testserver",
            cookies=client.cookies,
        ) as async_client:
            request = async_client.post(
                "/pipeline/preview",
                data={
                    "csrf_token": csrf_token,
                    "source_provider": "mss",
                    "source_schema": MSS_DATASET,
                    "source_table": MSS_FILE,
                    "destination_provider": "postgres",
                    "destination_schema": "public",
                    "destination_table": "mission_orders",
                    "write_mode": "append",
                },
                headers={"HX-Request": "true", "HX-Target": "pipeline-preview-region"},
            )
            response, _ = await asyncio.gather(request, ticker())

        assert response.status_code == 200
        assert provider_threads
        assert all(thread_id != loop_thread for thread_id in provider_threads)
        assert max(after - before for before, after in zip(ticks, ticks[1:], strict=False)) < 0.1

    asyncio.run(exercise())


MSS_DATASET = "ri.foundry.main.dataset.demo-operations"
MSS_RAW_DATASET = "ri.foundry.main.dataset.demo-raw"
MSS_FILE = "mission_orders.parquet"
MSS_DEST_DATASET = "ri.foundry.main.dataset.demo-destination"
FOUNDRY_FOLDER = "ri.compass.main.folder.11111111-1111-1111-1111-111111111111"


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
        event = db.scalar(
            select(AuditEvent).where(
                AuditEvent.event_type == "pipeline.created",
                AuditEvent.actor_user_id == pipeline.user_id,
            )
        )
        assert event is not None
        assert event.request_id
        assert event.source_ip == "127.0.0.1"

    reloaded = client.get(saved.headers["location"])
    assert reloaded.status_code == 200
    assert "Pipeline saved" in reloaded.text
    assert "Mission orders to warehouse" in reloaded.text
    assert f'data-pipeline-id="{pipeline.id}"' in reloaded.text
    assert 'data-pipeline-run="true"' in reloaded.text
    source_dataset = re.search(
        r'<input[^>]+id="pipeline-source-schema-select"[^>]*>', reloaded.text
    )
    assert source_dataset is not None
    assert f'value="{MSS_DATASET}"' in source_dataset.group(0)
    source_object = _pipeline_control(reloaded.text, "pipeline-source-table-select")
    assert f'value="{MSS_FILE}"' in source_object
    assert '<option value="public" selected>' in reloaded.text
    assert '<option value="mission_orders" selected>' in reloaded.text


def test_pipeline_ui_creates_and_uses_a_new_foundry_dataset(client, demo_connections) -> None:
    from app.config import get_settings
    from app.connectors.fake import FakeFoundryConnector
    from app.connectors.registry import ConnectorRegistry, load_builtin_connectors
    from app.services.demo import DEMO_CONNECTION_CREDENTIALS, restore_demo_foundry_datasets
    from app.services.secrets import store_user_credentials

    with SessionLocal() as db:
        secret = db.scalar(select(UserSecret).where(UserSecret.provider == "mss"))
        assert secret is not None
        credentials = dict(DEMO_CONNECTION_CREDENTIALS["mss"])
        credentials["dataset_rid"] = ""
        stored = store_user_credentials(
            db,
            get_settings(),
            user=secret.user,
            provider="mss",
            credentials=credentials,
        )
        assert stored.validation_status == "untested"

    web_login(client, next_path="/pipeline")
    page = client.get("/pipeline")
    assert "Create Foundry dataset" in page.text

    created = client.post(
        "/pipeline/foundry-datasets",
        data={
            "csrf_token": csrf_from(page.text),
            "destination_provider": "mss",
            "parent_folder_rid": FOUNDRY_FOLDER,
            "dataset_name": "Daily readiness landing",
        },
        headers={"HX-Request": "true", "HX-Target": "pipeline-dataset-creator"},
    )

    assert created.status_code == 201
    assert "Dataset ready" in created.text
    assert created.headers["HX-Trigger-After-Settle"] == "pipelineDatasetCreated"
    assert "Create another dataset" in created.text
    for select_id in ("pipeline-target-schema-select", "pipeline-target-table-select"):
        assert created.text.count(f'id="{select_id}"') == 1
        control = _pipeline_select(created.text, select_id)
        assert f'hx-swap-oob="outerHTML:#{select_id}"' in control
    assert "Daily readiness landing" in created.text
    assert "pipeline-target-schema-select" in created.text
    rid_match = re.search(
        r'option value="(ri\.foundry\.main\.dataset\.[^"]+)" selected', created.text
    )
    assert rid_match is not None
    dataset_rid = rid_match.group(1)

    fresh_registry = load_builtin_connectors(
        demo=True,
        registry=ConnectorRegistry(get_settings()),
        settings=get_settings(),
    )
    with SessionLocal() as db:
        assert restore_demo_foundry_datasets(db, get_settings(), fresh_registry) == 1
    restored_connector = fresh_registry.connector_for("mss")
    assert isinstance(restored_connector, FakeFoundryConnector)
    assert restored_connector.list_objects(credentials, dataset_rid).items == ()

    saved = client.post(
        "/pipeline/save",
        data={
            "csrf_token": csrf_from(page.text),
            "pipeline_name": "Readiness to provisioned dataset",
            "source_provider": "postgres",
            "source_schema": "public",
            "source_table": "readiness_events",
            "destination_provider": "mss",
            "destination_schema": dataset_rid,
            "destination_table": "__new__",
            "destination_table_new": "readiness_export",
            "write_mode": "replace",
        },
    )

    assert saved.status_code == 303
    with SessionLocal() as db:
        dataset = db.scalar(select(FoundryDataset).where(FoundryDataset.dataset_rid == dataset_rid))
        pipeline = db.scalar(
            select(PipelineDefinition).where(
                PipelineDefinition.name == "Readiness to provisioned dataset"
            )
        )
        assert dataset is not None
        assert dataset.name == "Daily readiness landing"
        assert dataset.parent_folder_rid == FOUNDRY_FOLDER
        secret = db.scalar(select(UserSecret).where(UserSecret.provider == "mss"))
        assert secret is not None
        assert secret.validation_status == "connected"
        assert pipeline is not None
        assert pipeline.destination_schema == dataset_rid

    run = client.post(
        "/pipeline/runs",
        data={
            "csrf_token": csrf_from(page.text),
            "pipeline_id": pipeline.id,
        },
        headers={"HX-Request": "true", "HX-Target": "pipeline-run-monitor"},
    )
    assert run.status_code == 202
    with SessionLocal() as db:
        completed = db.scalar(
            select(PipelineRun).where(PipelineRun.pipeline_definition_id == pipeline.id)
        )
        assert completed is not None
        assert completed.status == "succeeded"


def test_pipeline_dataset_creation_validates_parent_folder(client, demo_connections) -> None:
    web_login(client, next_path="/pipeline")
    page = client.get("/pipeline")

    response = client.post(
        "/pipeline/foundry-datasets",
        data={
            "csrf_token": csrf_from(page.text),
            "destination_provider": "mss",
            "parent_folder_rid": "not-a-folder",
            "dataset_name": "Invalid destination",
        },
        headers={"HX-Request": "true", "HX-Target": "pipeline-dataset-creator"},
    )

    assert response.status_code == 422
    assert "valid Foundry folder RID" in response.text


def test_saved_pipeline_can_copy_within_the_same_system(client, demo_connections) -> None:
    web_login(client, next_path="/pipeline")
    page = client.get("/pipeline")
    response = client.post(
        "/pipeline/save",
        data={
            "csrf_token": csrf_from(page.text),
            "pipeline_id": "",
            "pipeline_name": "Foundry dataset copy",
            "source_provider": "mss",
            "source_schema": MSS_DATASET,
            "source_table": MSS_FILE,
            "destination_provider": "mss",
            "destination_schema": MSS_DATASET,
            "destination_table": "readiness_rollup.parquet",
            "write_mode": "replace",
        },
    )

    assert response.status_code == 303
    with SessionLocal() as db:
        pipeline = db.scalar(
            select(PipelineDefinition).where(PipelineDefinition.name == "Foundry dataset copy")
        )
        assert pipeline is not None
        assert pipeline.source_provider == "mss"
        assert pipeline.destination_provider == "mss"


def test_pipeline_rejects_a_postgres_route_to_the_same_table(client, demo_connections) -> None:
    web_login(client, next_path="/pipeline")
    page = client.get("/pipeline")
    response = client.post(
        "/pipeline/save",
        data={
            "csrf_token": csrf_from(page.text),
            "pipeline_name": "Unsafe self append",
            "source_provider": "postgres",
            "source_schema": "public",
            "source_table": "readiness_events",
            "destination_provider": "postgres",
            "destination_schema": "public",
            "destination_table": "readiness_events",
            "write_mode": "append",
        },
    )

    assert response.status_code == 422
    assert "different from the source object" in response.text


def test_pipeline_rejects_a_foundry_route_to_the_same_file(client, demo_connections) -> None:
    web_login(client, next_path="/pipeline")
    page = client.get("/pipeline")
    response = client.post(
        "/pipeline/save",
        data={
            "csrf_token": csrf_from(page.text),
            "pipeline_name": "Unsafe Foundry overwrite",
            "source_provider": "mss",
            "source_schema": MSS_DATASET,
            "source_table": MSS_FILE,
            "destination_provider": "mss",
            "destination_schema": MSS_DATASET,
            "destination_table": MSS_FILE,
            "write_mode": "replace",
        },
    )

    assert response.status_code == 422
    assert "different from the source object" in response.text


def test_pipeline_persists_long_multi_file_source_labels(client, demo_connections) -> None:
    web_login(client, next_path="/pipeline")
    page = client.get("/pipeline")
    source_files = ", ".join(
        [
            "daily_readiness_events_2026_09_15.parquet",
            "daily_readiness_events_2026_09_14.parquet",
            "daily_readiness_events_2026_09_13.parquet",
        ]
    )
    response = client.post(
        "/pipeline/save",
        data={
            "csrf_token": csrf_from(page.text),
            "pipeline_name": "Long multi-file source",
            "source_provider": "mss",
            "source_schema": MSS_DATASET,
            "source_table": source_files,
            "destination_provider": "postgres",
            "destination_schema": "public",
            "destination_table": "readiness_events",
            "write_mode": "replace",
        },
    )

    assert response.status_code == 303
    with SessionLocal() as db:
        pipeline = db.scalar(
            select(PipelineDefinition).where(PipelineDefinition.name == "Long multi-file source")
        )
        assert pipeline is not None
        assert pipeline.source_table == source_files


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
        assert pipeline.definition_version == 3
        policy = parse_write_policy(json.loads(pipeline.write_policy_json))
        assert isinstance(policy, PostgresUpsertPolicy)
        assert policy.conflict_columns == ["event_id"]


def test_pipeline_save_uses_connected_foundry_branches(
    client, demo_connections, monkeypatch
) -> None:
    monkeypatch.setattr(
        "app.services.catalogs.UserCatalog.default_branch",
        lambda _self, provider: "release" if provider in {"mss", "mcscop"} else "",
    )
    web_login(client, next_path="/pipeline")
    page = client.get("/pipeline")
    source_saved = client.post(
        "/pipeline/save",
        data={
            "csrf_token": csrf_from(page.text),
            "pipeline_name": "Release branch source",
            "source_provider": "mss",
            "source_schema": MSS_DATASET,
            "source_table": MSS_FILE,
            "destination_provider": "postgres",
            "destination_schema": "public",
            "destination_table": "mission_orders",
            "write_mode": "append",
        },
    )
    assert source_saved.status_code == 303

    page = client.get("/pipeline")
    destination_saved = client.post(
        "/pipeline/save",
        data={
            "csrf_token": csrf_from(page.text),
            "pipeline_name": "Release branch destination",
            "source_provider": "postgres",
            "source_schema": "public",
            "source_table": "mission_orders",
            "destination_provider": "mss",
            "destination_schema": MSS_DEST_DATASET,
            "destination_table": "readiness_rollup.parquet",
            "write_mode": "replace",
        },
    )
    assert destination_saved.status_code == 303

    with SessionLocal() as db:
        source_pipeline = db.scalar(
            select(PipelineDefinition).where(PipelineDefinition.name == "Release branch source")
        )
        destination_pipeline = db.scalar(
            select(PipelineDefinition).where(
                PipelineDefinition.name == "Release branch destination"
            )
        )
        assert source_pipeline is not None and destination_pipeline is not None
        source_locator = parse_locator(json.loads(source_pipeline.source_locator_json))
        destination_locator = parse_locator(
            json.loads(destination_pipeline.destination_locator_json)
        )
        assert isinstance(source_locator, FoundryDatasetFilesLocator)
        assert isinstance(destination_locator, FoundryUploadLocator)
        assert source_locator.branch == "release"
        assert destination_locator.branch == "release"


def test_pipeline_save_persists_selected_unique_upsert_key(
    client, demo_connections, monkeypatch
) -> None:
    original_inspect = UserCatalog.inspect_object

    def inspect_with_unique(self, provider, locator):
        if provider == "postgres":
            return ObjectSchema(
                locator=locator,
                columns=(
                    ColumnSchema(name="event_id", data_type="Int64"),
                    ColumnSchema(name="unit_name", data_type="Utf8"),
                ),
                primary_key=("event_id",),
                unique_constraints=(("unit_name",),),
            )
        return original_inspect(self, provider, locator)

    monkeypatch.setattr("app.services.catalogs.UserCatalog.inspect_object", inspect_with_unique)
    web_login(client, next_path="/pipeline")
    page = client.get("/pipeline")
    preview = client.post(
        "/pipeline/preview",
        data={
            "csrf_token": csrf_from(page.text),
            "source_provider": "mss",
            "source_schema": MSS_DATASET,
            "source_table": MSS_FILE,
            "destination_provider": "postgres",
            "destination_schema": "public",
            "destination_table": "readiness_events",
            "write_mode": "upsert",
        },
        headers={"HX-Request": "true", "HX-Target": "pipeline-preview-region"},
    )
    assert preview.status_code == 200
    assert 'name="conflict_columns"' in preview.text
    assert '<option value="unit_name">unit_name</option>' in preview.text

    response = client.post(
        "/pipeline/save",
        data={
            "csrf_token": csrf_from(page.text),
            "pipeline_name": "Unique key upsert",
            "source_provider": "mss",
            "source_schema": MSS_DATASET,
            "source_table": MSS_FILE,
            "destination_provider": "postgres",
            "destination_schema": "public",
            "destination_table": "readiness_events",
            "write_mode": "upsert",
            "conflict_columns": "unit_name",
        },
    )
    assert response.status_code == 303

    with SessionLocal() as db:
        pipeline = db.scalar(
            select(PipelineDefinition).where(PipelineDefinition.name == "Unique key upsert")
        )
        assert pipeline is not None
        policy = parse_write_policy(json.loads(pipeline.write_policy_json))
        assert isinstance(policy, PostgresUpsertPolicy)
        assert policy.conflict_columns == ["unit_name"]


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


def test_saved_routes_fragment_refreshes_after_a_pipeline_is_saved(
    client, demo_connections
) -> None:
    web_login(client, next_path="/pipeline")
    page = client.get("/pipeline")
    headers = {"HX-Request": "true", "HX-Target": "pipeline-saved-routes"}
    initial = client.get("/pipeline/saved-routes", headers=headers)
    assert initial.status_code == 200
    assert 'id="pipeline-saved-routes"' in initial.text
    assert "0 total" in initial.text

    saved = client.post(
        "/pipeline/save",
        data={
            "csrf_token": csrf_from(page.text),
            "pipeline_name": "Refreshed saved route",
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

    refreshed = client.get("/pipeline/saved-routes", headers=headers)
    assert refreshed.status_code == 200
    assert "1 total" in refreshed.text
    assert "Refreshed saved route" in refreshed.text


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
    assert "Schema ready. Choose another CSV to replace it." in scanned.text
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
    assert "unit_readiness.csv" in _pipeline_csv_summary(
        reloaded.text, "pipeline-source-table-select"
    )
    assert "Scanned CSV" in _pipeline_csv_summary(reloaded.text, "pipeline-source-schema-select")
    assert "Source · Scanned CSV" in reloaded.text
    assert "Schema ready. Choose another CSV to replace it." in reloaded.text
    assert f'name="source_upload_id" value="{upload_id}"' in reloaded.text


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
    assert "Scan failed. Choose another CSV to try again." in response.text


def _pipeline_select(markup: str, element_id: str) -> str:
    match = re.search(rf'<select[^>]+id="{element_id}"[^>]*>.*?</select>', markup)
    assert match is not None
    return match.group(0)


def _pipeline_csv_summary(markup: str, element_id: str) -> str:
    match = re.search(rf'<div[^>]+id="{element_id}"[^>]*>.*?</div>', markup)
    assert match is not None
    return match.group(0)


def _pipeline_control(markup: str, element_id: str) -> str:
    try:
        return _pipeline_select(markup, element_id)
    except AssertionError:
        match = re.search(rf'<input[^>]+id="{element_id}"[^>]*>', markup)
        assert match is not None
        return match.group(0)


def test_pipeline_live_writer_flags_select_a_valid_initial_route(
    client, demo_connections, monkeypatch, request_settings_override
) -> None:
    from app.config import get_settings

    # Keep fixture connectors while exercising the real live-mode writer policy.
    settings = get_settings()
    monkeypatch.setattr(settings, "data_mover_mode", "real")
    monkeypatch.setattr(settings, "pipeline_enable_postgres_writer", True)
    monkeypatch.setattr(settings, "pipeline_enable_mss_writer", False)
    monkeypatch.setattr(settings, "pipeline_enable_mcscop_writer", False)
    request_settings_override(settings)
    web_login(client, next_path="/pipeline")
    response = client.get("/pipeline")
    assert response.status_code == 200
    source = _pipeline_select(response.text, "pipeline-source-select")
    target = _pipeline_select(response.text, "pipeline-target-select")
    assert 'value="mss" selected' in source
    assert 'value="postgres"' in source  # Can switch direction without a circular filter.
    assert 'value="postgres" selected' in target
    assert 'value="mss"' not in target


@pytest.mark.parametrize(
    ("source", "old_target", "expected_targets"),
    [
        ("mss", "mss", {"mss", "postgres", "mcscop"}),
        ("mcscop", "mcscop", {"mss", "postgres", "mcscop"}),
        ("postgres", "postgres", {"mss", "postgres", "mcscop"}),
        ("csv", "postgres", {"postgres", "mss", "mcscop"}),
    ],
)
def test_pipeline_source_change_refreshes_destination_options(
    client, demo_connections, source, old_target, expected_targets
) -> None:
    web_login(client, next_path="/pipeline")
    page = client.get("/pipeline")
    response = client.post(
        "/pipeline/preview",
        data={
            "csrf_token": csrf_from(page.text),
            "source_provider": source,
            "destination_provider": old_target,
            "destination_schema": "public",
            "destination_table": "readiness_events",
        },
        headers={
            "HX-Request": "true",
            "HX-Target": "pipeline-preview-region",
            "HX-Trigger": "pipeline-source-select",
        },
    )
    assert response.status_code == 200
    target = _pipeline_select(response.text, "pipeline-target-select")
    assert set(re.findall(r'<option value="([^"]+)"', target)) == expected_targets
    assert 'hx-swap-oob="outerHTML:#pipeline-target-select"' in target
    assert 'hx-post="/pipeline/preview"' in target
    assert 'hx-include="#pipeline-form"' in target
    assert "disabled" not in target.split(">")[0]
    if source == "mss":
        source_dataset = _pipeline_control(response.text, "pipeline-source-schema-select")
        source_file = _pipeline_control(response.text, "pipeline-source-table-select")
        assert f'value="{MSS_DATASET}"' in source_dataset
        assert 'value="public"' not in source_dataset
        assert 'value="asset_inventory"' not in source_file
    if source == "csv":
        assert "Scanned CSV" not in response.text
        assert "Upload required" in _pipeline_csv_summary(
            response.text, "pipeline-source-schema-select"
        )
        assert "Upload required" in _pipeline_csv_summary(
            response.text, "pipeline-source-table-select"
        )
    source_controls = (
        () if source == "csv" else ("pipeline-source-schema-select", "pipeline-source-table-select")
    )
    for element_id in (
        *source_controls,
        "pipeline-target-schema-select",
        "pipeline-target-table-select",
    ):
        assert 'hx-post="/pipeline/preview"' in _pipeline_control(response.text, element_id)


def test_foundry_source_dataset_rid_is_free_text_and_persists_in_route(
    client, demo_connections
) -> None:
    web_login(client, next_path="/pipeline")
    page = client.get("/pipeline")
    fields = {
        "csrf_token": csrf_from(page.text),
        "source_provider": "mss",
        "source_schema": MSS_RAW_DATASET,
        "source_table": "source_events.csv, incoming_orders.csv",
        "destination_provider": "postgres",
        "destination_schema": "public",
        "destination_table": "readiness_events",
        "write_mode": "replace",
    }
    preview = client.post(
        "/pipeline/preview",
        data=fields,
        headers={"HX-Request": "true", "HX-Target": "pipeline-preview-region"},
    )

    assert preview.status_code == 200
    dataset_control = re.search(
        r'<input[^>]+id="pipeline-source-schema-select"[^>]*>', preview.text
    )
    assert dataset_control is not None
    assert f'value="{MSS_RAW_DATASET}"' in dataset_control.group(0)
    assert 'name="source_schema"' in dataset_control.group(0)
    assert 'maxlength="240"' in dataset_control.group(0)
    assert 'value="source_events.csv, incoming_orders.csv"' in preview.text
    assert 'list="pipeline-source-dataset-suggestions"' in preview.text
    assert 'list="pipeline-source-file-suggestions"' in preview.text
    assert 'id="pipeline-source-file-suggestions"' in preview.text

    saved = client.post(
        "/pipeline/save",
        data={**fields, "pipeline_name": "Raw events export"},
    )
    assert saved.status_code == 303
    with SessionLocal() as db:
        pipeline = db.scalar(
            select(PipelineDefinition).where(PipelineDefinition.name == "Raw events export")
        )
        assert pipeline is not None
        locator = parse_locator(json.loads(pipeline.source_locator_json))
        assert isinstance(locator, FoundryDatasetFilesLocator)
        assert locator.dataset_rid == MSS_RAW_DATASET
        assert locator.file_paths == ["source_events.csv", "incoming_orders.csv"]


def test_pipeline_exposes_swap_direction_and_dynamic_write_mode_refresh(
    client, demo_connections
) -> None:
    web_login(client, next_path="/pipeline")
    response = client.get("/pipeline")

    assert response.status_code == 200
    assert 'data-pipeline-swap="true"' in response.text
    swap = re.search(r'<button[^>]+data-pipeline-swap="true"[^>]*>', response.text)
    assert swap is not None
    assert 'hx-post="/pipeline/preview"' in swap.group(0)
    assert 'hx-vals="{&quot;swap_direction&quot;:&quot;true&quot;}"' in swap.group(0)
    assert 'aria-describedby="pipeline-availability-note"' in swap.group(0)
    assert 'id="pipeline-swap-unavailable-note"' not in response.text
    assert response.text.count('id="pipeline-source-dataset-suggestions"') == 1
    assert response.text.count('id="pipeline-source-file-suggestions"') == 1
    mode_select = _pipeline_select(response.text, "pipeline-mode-select")
    assert 'hx-post="/pipeline/preview"' in mode_select
    assert 'hx-include="#pipeline-form"' in mode_select


def test_foundry_single_file_preview_shows_detected_and_sent_types(
    client, demo_connections
) -> None:
    web_login(client, next_path="/pipeline")
    page = client.get("/pipeline")
    response = client.post(
        "/pipeline/preview",
        data={
            "csrf_token": csrf_from(page.text),
            "source_provider": "mss",
            "source_schema": MSS_DATASET,
            "source_table": "mission_orders.parquet",
            "destination_provider": "postgres",
            "destination_schema": "public",
            "destination_table": "readiness_events",
            "write_mode": "replace",
        },
        headers={"HX-Request": "true", "HX-Target": "pipeline-preview-region"},
    )
    assert response.status_code == 200
    assert "Source file inspected" in response.text
    assert "Sent as" in response.text
    assert "Use detected type (Int64)" in response.text


def test_pipeline_preview_updates_swap_state_and_multi_file_preview(
    client, demo_connections
) -> None:
    web_login(client, next_path="/pipeline")
    page = client.get("/pipeline")
    headers = {
        "HX-Request": "true",
        "HX-Target": "pipeline-preview-region",
    }
    multi_file = client.post(
        "/pipeline/preview",
        data={
            "csrf_token": csrf_from(page.text),
            "source_provider": "mss",
            "source_schema": MSS_DATASET,
            "source_table": "mission_orders.parquet, readiness_rollup.parquet",
            "destination_provider": "postgres",
            "destination_schema": "public",
            "destination_table": "readiness_events",
            "write_mode": "replace",
        },
        headers=headers,
    )
    assert multi_file.status_code == 200
    assert "2 file(s) selected" in multi_file.text
    assert "Select an object to preview it" not in multi_file.text
    swap = re.search(r'<button[^>]+data-pipeline-swap="true"[^>]*>', multi_file.text)
    assert swap is not None
    assert "disabled" in swap.group(0)
    assert (
        'aria-describedby="pipeline-availability-note pipeline-swap-unavailable-note"'
        in swap.group(0)
    )
    assert 'id="pipeline-swap-unavailable-note"' in multi_file.text
    assert "multiple files and all_supported cannot be reversed" in multi_file.text

    invalid_swap = client.post(
        "/pipeline/preview",
        data={
            "csrf_token": csrf_from(page.text),
            "source_provider": "mss",
            "source_schema": MSS_DATASET,
            "source_table": "mission_orders.parquet, readiness_rollup.parquet",
            "destination_provider": "postgres",
            "destination_schema": "public",
            "destination_table": "readiness_events",
            "write_mode": "replace",
            "swap_direction": "true",
        },
        headers=headers,
    )
    assert invalid_swap.status_code == 422
    assert "multiple files and all_supported cannot be reversed" in invalid_swap.text

    swapped = client.post(
        "/pipeline/preview",
        data={
            "csrf_token": csrf_from(page.text),
            "source_provider": "postgres",
            "source_schema": "public",
            "source_table": "readiness_events",
            "destination_provider": "mss",
            "destination_schema": MSS_DATASET,
            "destination_table": MSS_FILE,
            "write_mode": "replace",
            "swap_direction": "true",
        },
        headers=headers,
    )
    assert swapped.status_code == 200
    source_provider = _pipeline_select(swapped.text, "pipeline-source-select")
    target_provider = _pipeline_select(swapped.text, "pipeline-target-select")
    assert 'value="mss" selected' in source_provider
    assert 'value="postgres" selected' in target_provider
    source_dataset = _pipeline_control(swapped.text, "pipeline-source-schema-select")
    source_file = _pipeline_control(swapped.text, "pipeline-source-table-select")
    assert f'value="{MSS_DATASET}"' in source_dataset
    assert f'value="{MSS_FILE}"' in source_file
    assert '<option value="public" selected>' in swapped.text
    assert '<option value="readiness_events" selected>' in swapped.text

    overlap = client.post(
        "/pipeline/preview",
        data={
            "csrf_token": csrf_from(page.text),
            "source_provider": "postgres",
            "source_schema": "public",
            "source_table": "readiness_events",
            "destination_provider": "postgres",
            "destination_schema": "public",
            "destination_table": "readiness_events",
            "write_mode": "append",
        },
        headers=headers,
    )
    assert overlap.status_code == 200
    availability_start = overlap.text.index('id="pipeline-availability-note"')
    availability = overlap.text[availability_start : availability_start + 500]
    assert "hedron-alert-warning" in availability

    csv_route = client.post(
        "/pipeline/preview",
        data={
            "csrf_token": csrf_from(page.text),
            "source_provider": "csv",
            "source_schema": "uploaded",
            "source_table": "pending.csv",
            "destination_provider": "mss",
            "destination_schema": MSS_DATASET,
            "destination_table": MSS_FILE,
            "write_mode": "replace",
        },
        headers=headers,
    )
    assert csv_route.status_code == 200
    swap = re.search(r'<button[^>]+data-pipeline-swap="true"[^>]*>', csv_route.text)
    assert swap is not None
    assert "disabled" in swap.group(0)
    assert "CSV sources cannot be reversed because uploads are source-only" in csv_route.text


@pytest.mark.parametrize(
    "source_table",
    ["all_supported", "mission_orders.parquet, readiness_rollup.parquet"],
)
def test_pipeline_disables_swap_for_non_single_foundry_sources(
    client, demo_connections, source_table
) -> None:
    web_login(client, next_path="/pipeline")
    page = client.get("/pipeline")
    response = client.post(
        "/pipeline/preview",
        data={
            "csrf_token": csrf_from(page.text),
            "source_provider": "mss",
            "source_schema": MSS_DATASET,
            "source_table": source_table,
            "destination_provider": "postgres",
            "destination_schema": "public",
            "destination_table": "readiness_events",
            "write_mode": "replace",
        },
        headers={"HX-Request": "true", "HX-Target": "pipeline-preview-region"},
    )

    assert response.status_code == 200
    swap = re.search(r'<button[^>]+data-pipeline-swap="true"[^>]*>', response.text)
    assert swap is not None
    assert "disabled" in swap.group(0)
    assert "multiple files and all_supported cannot be reversed" in response.text


def test_pipeline_disables_swap_for_an_unlisted_foundry_source(client, demo_connections) -> None:
    web_login(client, next_path="/pipeline")
    page = client.get("/pipeline")
    response = client.post(
        "/pipeline/preview",
        data={
            "csrf_token": csrf_from(page.text),
            "source_provider": "mss",
            "source_schema": "ri.foundry.main.dataset.unlisted",
            "source_table": MSS_FILE,
            "destination_provider": "postgres",
            "destination_schema": "public",
            "destination_table": "readiness_events",
            "write_mode": "replace",
        },
        headers={"HX-Request": "true", "HX-Target": "pipeline-preview-region"},
    )

    assert response.status_code == 200
    swap = re.search(r'<button[^>]+data-pipeline-swap="true"[^>]*>', response.text)
    assert swap is not None
    assert "disabled" in swap.group(0)
    assert "Foundry dataset and file to be present in the catalog" in response.text


def test_pipeline_can_swap_between_distinct_postgres_objects(client, demo_connections) -> None:
    web_login(client, next_path="/pipeline")
    page = client.get("/pipeline")
    response = client.post(
        "/pipeline/preview",
        data={
            "csrf_token": csrf_from(page.text),
            "source_provider": "postgres",
            "source_schema": "public",
            "source_table": "readiness_events",
            "destination_provider": "postgres",
            "destination_schema": "public",
            "destination_table": "asset_inventory",
            "write_mode": "append",
            "swap_direction": "true",
        },
        headers={"HX-Request": "true", "HX-Target": "pipeline-preview-region"},
    )

    assert response.status_code == 200
    assert 'value="asset_inventory" selected' in _pipeline_select(
        response.text, "pipeline-source-table-select"
    )
    assert 'value="readiness_events" selected' in _pipeline_select(
        response.text, "pipeline-target-table-select"
    )
    assert 'id="pipeline-swap-unavailable-note"' not in response.text


def test_pipeline_can_swap_between_distinct_foundry_files(client, demo_connections) -> None:
    web_login(client, next_path="/pipeline")
    page = client.get("/pipeline")
    response = client.post(
        "/pipeline/preview",
        data={
            "csrf_token": csrf_from(page.text),
            "source_provider": "mss",
            "source_schema": MSS_DATASET,
            "source_table": MSS_FILE,
            "destination_provider": "mss",
            "destination_schema": MSS_DATASET,
            "destination_table": "readiness_rollup.parquet",
            "write_mode": "replace",
            "swap_direction": "true",
        },
        headers={"HX-Request": "true", "HX-Target": "pipeline-preview-region"},
    )

    assert response.status_code == 200
    source_file = _pipeline_control(response.text, "pipeline-source-table-select")
    target_file = _pipeline_control(response.text, "pipeline-target-table-select")
    assert 'value="readiness_rollup.parquet"' in source_file
    assert 'value="mission_orders.parquet" selected' in target_file
    assert 'id="pipeline-swap-unavailable-note"' not in response.text


def test_pipeline_can_swap_mcscop_destination_to_source(client, demo_connections) -> None:
    web_login(client, next_path="/pipeline")
    page = client.get("/pipeline")
    response = client.post(
        "/pipeline/preview",
        data={
            "csrf_token": csrf_from(page.text),
            "source_provider": "postgres",
            "source_schema": "public",
            "source_table": "readiness_events",
            "destination_provider": "mcscop",
            "destination_schema": MSS_DEST_DATASET,
            "destination_table": "readiness.snappy.parquet",
            "write_mode": "replace",
            "swap_direction": "true",
        },
        headers={"HX-Request": "true", "HX-Target": "pipeline-preview-region"},
    )

    assert response.status_code == 200
    source = _pipeline_select(response.text, "pipeline-source-select")
    target = _pipeline_select(response.text, "pipeline-target-select")
    assert 'value="mcscop" selected' in source
    assert 'value="postgres" selected' in target
    assert 'id="pipeline-swap-unavailable-note"' not in response.text


@pytest.mark.parametrize(
    ("route", "reason"),
    [
        (
            {
                "source_provider": "postgres",
                "source_schema": "public",
                "source_table": "readiness_events",
                "destination_provider": "postgres",
                "destination_schema": "public",
                "destination_table": "__new__",
            },
            "Choose an existing destination object before swapping.",
        ),
        (
            {
                "source_provider": "postgres",
                "source_schema": "public",
                "source_table": "readiness_events",
                "destination_provider": "postgres",
                "destination_schema": "public",
                "destination_table": "readiness_events",
            },
            "Choose a destination object different from the source object.",
        ),
    ],
)
def test_pipeline_swap_reason_is_shared_between_ui_and_server(
    client, demo_connections, route, reason
) -> None:
    web_login(client, next_path="/pipeline")
    page = client.get("/pipeline")
    base = {
        "csrf_token": csrf_from(page.text),
        "write_mode": "replace",
        **route,
    }
    headers = {"HX-Request": "true", "HX-Target": "pipeline-preview-region"}

    preview = client.post("/pipeline/preview", data=base, headers=headers)
    assert preview.status_code == 200
    swap = re.search(r'<button[^>]+data-pipeline-swap="true"[^>]*>', preview.text)
    assert swap is not None
    assert "disabled" in swap.group(0)
    assert (
        'aria-describedby="pipeline-availability-note pipeline-swap-unavailable-note"'
        in swap.group(0)
    )
    assert reason in preview.text

    rejected = client.post(
        "/pipeline/preview",
        data={**base, "swap_direction": "true"},
        headers=headers,
    )
    assert rejected.status_code == 422
    assert reason in rejected.text


def test_pipeline_empty_destination_explains_disabled_writes_and_recovers(
    client, demo_connections, monkeypatch, request_settings_override
) -> None:
    from app.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "data_mover_mode", "real")
    monkeypatch.setattr(settings, "pipeline_enable_postgres_writer", False)
    monkeypatch.setattr(settings, "pipeline_enable_mss_writer", False)
    monkeypatch.setattr(settings, "pipeline_enable_mcscop_writer", False)
    request_settings_override(settings)
    web_login(client, next_path="/pipeline")
    page = client.get("/pipeline")
    headers = {
        "HX-Request": "true",
        "HX-Target": "pipeline-preview-region",
        "HX-Trigger": "pipeline-source-select",
    }
    data = {
        "csrf_token": csrf_from(page.text),
        "source_provider": "postgres",
        "destination_provider": "postgres",
    }
    empty = client.post("/pipeline/preview", data=data, headers=headers)
    assert empty.status_code == 200
    assert "disabled by the administrator" in empty.text
    assert "A successful connection check does not enable writes" in empty.text
    target = _pipeline_select(empty.text, "pipeline-target-select")
    assert "No writable destination for this source" in target
    assert "disabled" in target.split(">")[0]
    swap = re.search(r'<button[^>]+data-pipeline-swap="true"[^>]*>', empty.text)
    assert swap is not None
    assert "disabled" in swap.group(0)
    assert (
        'aria-describedby="pipeline-availability-note pipeline-swap-unavailable-note"'
        in swap.group(0)
    )
    assert "Select a destination before swapping." in empty.text

    rejected = client.post(
        "/pipeline/preview",
        data={**data, "destination_provider": "", "swap_direction": "true"},
        headers=headers,
    )
    assert rejected.status_code == 422
    assert "Select a destination before swapping." in rejected.text

    # Disabled destination controls are omitted by the browser on the next change.
    monkeypatch.setattr(settings, "pipeline_enable_postgres_writer", True)
    recovered = client.post(
        "/pipeline/preview",
        data={
            "csrf_token": csrf_from(page.text),
            "source_provider": "mss",
        },
        headers=headers,
    )
    assert recovered.status_code == 200
    target = _pipeline_select(recovered.text, "pipeline-target-select")
    assert 'value="postgres" selected' in target
    assert "disabled" not in target.split(">")[0]
    schema = _pipeline_select(recovered.text, "pipeline-target-schema-select")
    assert "disabled" not in schema.split(">")[0]


def test_pipeline_preview_accepts_mcscop_as_a_source(client, demo_connections) -> None:
    web_login(client, next_path="/pipeline")
    page = client.get("/pipeline")
    response = client.post(
        "/pipeline/preview",
        data={
            "csrf_token": csrf_from(page.text),
            "source_provider": "mcscop",
            "destination_provider": "postgres",
            "source_schema": MSS_DEST_DATASET,
            "source_table": "readiness.snappy.parquet",
            "destination_schema": "public",
            "destination_table": "readiness_events",
        },
        headers={"HX-Request": "true", "HX-Target": "pipeline-preview-region"},
    )
    assert response.status_code == 200
    source = _pipeline_select(response.text, "pipeline-source-select")
    assert 'value="mcscop" selected' in source
    target = _pipeline_select(response.text, "pipeline-target-select")
    assert set(re.findall(r'<option value="([^"]+)"', target)) == {
        "mss",
        "mcscop",
        "postgres",
    }


def test_preview_updates_route_facts_without_duplicate_control_ids(
    client, demo_connections
) -> None:
    web_login(client, next_path="/pipeline")
    page = client.get("/pipeline")
    response = client.post(
        "/pipeline/preview",
        data={
            "csrf_token": csrf_from(page.text),
            "source_provider": "mss",
            "destination_provider": "postgres",
        },
        headers={"HX-Request": "true", "HX-Target": "pipeline-preview-region"},
    )
    assert response.status_code == 200
    for element_id in (
        "pipeline-source-node",
        "pipeline-target-node",
        "pipeline-schema-preview",
        "pipeline-csv-file",
        "pipeline-csv-inspection",
        "pipeline-capabilities",
    ):
        assert response.text.count(f'id="{element_id}"') == 1
    assert "MSS to PostgreSQL" in response.text
    assert 'data-field-label="Dataset"' in _pipeline_control(
        response.text, "pipeline-source-schema-select"
    )
    assert 'data-field-label="Schema"' in _pipeline_select(
        response.text, "pipeline-target-schema-select"
    )
    assert "14 fields" not in response.text


@pytest.mark.parametrize("provider", ["mss", "mcscop"])
def test_created_foundry_file_can_be_saved_again_after_reload(
    client, demo_connections, provider
) -> None:
    from html.parser import HTMLParser

    class NewNameParser(HTMLParser):
        value = ""
        pattern = ""

        def handle_starttag(self, tag, attrs):
            attrs = dict(attrs)
            if tag == "input" and attrs.get("name") == "destination_table_new":
                self.value = attrs["value"]
                self.pattern = attrs["pattern"]

    web_login(client, next_path="/pipeline")
    page = client.get("/pipeline")
    fields = {
        "csrf_token": csrf_from(page.text),
        "pipeline_name": "Reusable Foundry export",
        "source_provider": "postgres",
        "source_schema": "public",
        "source_table": "readiness_events",
        "destination_provider": provider,
        "destination_schema": MSS_DEST_DATASET if provider == "mcscop" else MSS_DATASET,
        "destination_table": "__new__",
        "destination_table_new": "a",
        "write_mode": "replace",
    }
    saved = client.post("/pipeline/save", data=fields)
    assert saved.status_code == 303
    reloaded = client.get(saved.headers["location"])
    parser = NewNameParser()
    parser.feed(reloaded.text)
    assert parser.value == "a"
    assert parser.pattern is not None
    assert re.fullmatch(parser.pattern, parser.value)
    pipeline_match = re.search(r'name="pipeline_id" value="([^"]+)"', reloaded.text)
    assert pipeline_match is not None
    pipeline_id = pipeline_match.group(1)
    fields.update(pipeline_id=pipeline_id, destination_table_new=parser.value)
    saved_again = client.post("/pipeline/save", data=fields)
    assert saved_again.status_code == 303
    with SessionLocal() as db:
        pipeline = db.get(PipelineDefinition, pipeline_id)
        assert pipeline is not None
        assert pipeline.destination_table == "a.snappy.parquet"


def test_remote_pipeline_save_still_requires_source_location(client, demo_connections) -> None:
    web_login(client, next_path="/pipeline")
    page = client.get("/pipeline")
    response = client.post(
        "/pipeline/save",
        data={
            "csrf_token": csrf_from(page.text),
            "pipeline_name": "Missing source object",
            "source_provider": "postgres",
            "destination_provider": "mss",
            "destination_schema": MSS_DATASET,
            "destination_table": "orders.parquet",
            "write_mode": "replace",
        },
    )
    assert response.status_code == 422
