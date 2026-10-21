"""The seeded Databricks demo exercises the real UI and worker without network I/O."""

from __future__ import annotations

import json
from dataclasses import replace

import polars as pl
import pytest
from sqlalchemy import select

from app.config import get_settings
from app.connectors.base import AbortResult, TransferBatch
from app.connectors.errors import ConnectorError, TransferErrorCode
from app.connectors.fake_databricks import DemoDatabricksBackend, FakeDatabricksConnector
from app.connectors.locators import DatabricksAppendPolicy, DatabricksCreatePolicy
from app.connectors.registry import ConnectorRegistry, load_builtin_connectors
from app.database import SessionLocal, stable_session_factory
from app.infrastructure.runtime import ExecutionRuntime
from app.models import PipelineDefinition, PipelineRun, User
from app.services.catalogs import CREATE_TABLE_VALUE
from app.services.demo import DEMO_CONNECTION_CREDENTIALS, seed_demo_connections
from app.services.secret_catalog import SECRET_PROVIDER_MAP
from app.services.secrets import _validate_credentials
from tests.helpers import csrf_from, web_login

CREDENTIALS = DEMO_CONNECTION_CREDENTIALS["databricks"]


def test_delta_emulator_is_atomic_workspace_scoped_and_shared_across_instances() -> None:
    backend = DemoDatabricksBackend()
    source = FakeDatabricksConnector(backend)
    writer = FakeDatabricksConnector(backend)
    health = source.test_connection(CREDENTIALS)
    assert health.runtime_status == "RUNNING"
    assert "Emulated connection only" in health.message
    namespace = source.list_namespaces(CREDENTIALS)[0].name
    locator = source.list_objects(CREDENTIALS, namespace).items[0].locator
    schema = source.inspect_object(CREDENTIALS, locator)
    schema = replace(
        schema, columns=(replace(schema.columns[0], nullable=False), *schema.columns[1:])
    )
    target = locator.model_copy(update={"table": "new_delta", "object_id": ""})
    batches = list(source.extract(CREDENTIALS, locator, batch_rows=1, batch_bytes=1000))
    assert [batch.row_count for batch in batches] == [1, 1, 1]
    session = writer.prepare_destination(
        CREDENTIALS, target, schema, DatabricksCreatePolicy(), run_id="create"
    )
    for batch in batches:
        writer.write_batch(session, batch)
    with pytest.raises(ConnectorError):
        source.inspect_object(CREDENTIALS, target)
    assert writer.finalize(session).rows == 3
    assert source.count_rows(CREDENTIALS, target) == 3
    assert source.inspect_object(CREDENTIALS, target).columns[0].nullable is False
    with pytest.raises(ConnectorError) as stale_identity:
        source.inspect_object(CREDENTIALS, target.model_copy(update={"object_id": "stale-table"}))
    assert stale_identity.value.code == TransferErrorCode.SCHEMA_DRIFT
    with pytest.raises(ConnectorError) as conflict:
        writer.prepare_destination(
            CREDENTIALS, target, schema, DatabricksCreatePolicy(), run_id="duplicate"
        )
    assert conflict.value.code == TransferErrorCode.DESTINATION_CONFLICT
    session = writer.prepare_destination(
        CREDENTIALS, target, schema, DatabricksAppendPolicy(), run_id="abort"
    )
    writer.write_batch(session, batches[0])
    assert writer.abort(session) == AbortResult.ROLLED_BACK
    assert source.count_rows(CREDENTIALS, target) == 3

    with pytest.raises(ConnectorError) as other_workspace:
        source.inspect_object(
            {**CREDENTIALS, "workspace_url": "https://other.demo.invalid"}, target
        )
    assert other_workspace.value.code == TransferErrorCode.SCHEMA_DRIFT
    invalid = pl.DataFrame({"unexpected": [1]})
    session = writer.prepare_destination(
        CREDENTIALS, target, schema, DatabricksAppendPolicy(), run_id="drift"
    )
    with pytest.raises(ConnectorError):
        writer.write_batch(session, TransferBatch(invalid, 1, int(invalid.estimated_size()), 1))
    writer.abort(session)
    assert source.count_rows(CREDENTIALS, target) == 3


def test_reserved_demo_workspace_does_not_bypass_real_host_approval(access_app) -> None:
    settings = get_settings().model_copy(update={"pipeline_allowed_https_hosts": ""})
    provider = SECRET_PROVIDER_MAP["databricks"]
    assert (
        _validate_credentials(provider, CREDENTIALS, settings=settings)["workspace_url"]
        == CREDENTIALS["workspace_url"]
    )
    for mode, workspace in (
        ("real", CREDENTIALS["workspace_url"]),
        ("demo", "https://unapproved.demo.invalid"),
    ):
        with pytest.raises(ValueError, match="not approved"):
            _validate_credentials(
                provider,
                {**CREDENTIALS, "workspace_url": workspace},
                settings=settings.model_copy(update={"data_mover_mode": mode}),
            )


@pytest.fixture()
def databricks_demo(access_app, client, request_settings_override):
    settings = get_settings().model_copy(update={"pipeline_enable_databricks": True})
    with SessionLocal() as db:
        owner = db.scalar(select(User).where(User.email == "admin@example.gov"))
        assert owner is not None
        seed_demo_connections(db, settings, user=owner)
    registry = load_builtin_connectors(
        demo=True, registry=ConnectorRegistry(settings), settings=settings
    )
    access_app.state.execution.begin_shutdown()
    access_app.state.execution = ExecutionRuntime(settings, stable_session_factory(), registry)
    access_app.state.settings = settings
    request_settings_override(settings)
    web_login(client)
    return client, registry


@pytest.mark.parametrize("route", ["source", "append", "replace"])
def test_saved_seeded_delta_routes_run_after_backend_restart(databricks_demo, access_app, route):
    client, registry = databricks_demo
    connector = registry.catalog_reader_for("databricks")
    namespace = connector.list_namespaces(CREDENTIALS)[0].name
    delta_is_source = route == "source"
    page = client.get("/pipeline")
    saved = client.post(
        "/pipeline/save",
        data={
            "csrf_token": csrf_from(page.text),
            "pipeline_name": f"Restart Delta {route}",
            "source_provider": "databricks" if delta_is_source else "postgres",
            "source_schema": namespace if delta_is_source else "public",
            "source_table": "asset_inventory",
            "destination_provider": "postgres" if delta_is_source else "databricks",
            "destination_schema": "public" if delta_is_source else namespace,
            "destination_table": "asset_inventory",
            "write_mode": "replace" if delta_is_source else route,
        },
    )
    assert saved.status_code == 303, saved.text
    with SessionLocal() as db:
        pipeline = db.scalar(
            select(PipelineDefinition).where(PipelineDefinition.name == f"Restart Delta {route}")
        )
        assert pipeline is not None
        pipeline_id = pipeline.id

    # Keep the saved route and credentials, but reconstruct the process-local
    # connector generation as a server restart would. Do not reselect the table.
    settings = access_app.state.settings
    restarted_registry = load_builtin_connectors(
        demo=True, registry=ConnectorRegistry(settings), settings=settings
    )
    access_app.state.execution.begin_shutdown()
    access_app.state.execution = ExecutionRuntime(
        settings, stable_session_factory(), restarted_registry
    )
    queued = client.post(
        "/pipeline/runs",
        data={"csrf_token": csrf_from(page.text), "pipeline_id": pipeline_id},
    )
    assert queued.status_code == 303, queued.text
    with SessionLocal() as db:
        run = db.scalar(
            select(PipelineRun).where(PipelineRun.pipeline_definition_id == pipeline_id)
        )
        assert run is not None and run.status == "succeeded", getattr(
            run, "error_summary", "missing run"
        )
        assert run.source_rows == run.loaded_rows == 3
        verification = json.loads(run.verification_json or "{}")
        assert verification["destination_rows_after"] == (6 if route == "append" else 3)


def test_demo_connection_is_visible_and_transfers_in_both_directions(databricks_demo) -> None:
    client, registry = databricks_demo
    security = client.get("/security")
    assert "Databricks" in security.text
    assert "4 providers" in security.text
    assert "fake-databricks-token" not in security.text
    connector = registry.catalog_reader_for("databricks")
    namespace = connector.list_namespaces(CREDENTIALS)[0].name

    def save_and_run(name: str, **fields):
        page = client.get("/pipeline")
        saved = client.post(
            "/pipeline/save",
            data={"csrf_token": csrf_from(page.text), "pipeline_name": name, **fields},
        )
        assert saved.status_code == 303, saved.text
        with SessionLocal() as db:
            pipeline = db.scalar(select(PipelineDefinition).where(PipelineDefinition.name == name))
            assert pipeline is not None
            pipeline_id = pipeline.id
        queued = client.post(
            "/pipeline/runs", data={"csrf_token": csrf_from(page.text), "pipeline_id": pipeline_id}
        )
        assert queued.status_code == 303, queued.text
        with SessionLocal() as db:
            run = db.scalar(
                select(PipelineRun).where(PipelineRun.pipeline_definition_id == pipeline_id)
            )
            assert run is not None and run.status == "succeeded", getattr(
                run, "error_summary", "missing run"
            )
            assert run.verification_json is not None
            return json.loads(run.verification_json)

    for mode, before, after in (("create", None, 3), ("append", 3, 6), ("replace", 6, 3)):
        verification = save_and_run(
            f"Demo Delta {mode}",
            source_provider="postgres",
            source_schema="public",
            source_table="asset_inventory",
            destination_provider="databricks",
            destination_schema=namespace,
            destination_table=CREATE_TABLE_VALUE if mode == "create" else "qa_delta",
            destination_table_new="qa_delta" if mode == "create" else "",
            write_mode=mode,
        )
        assert verification["destination_rows_after"] == after
        assert verification["destination_rows_before"] == before

    verification = save_and_run(
        "Demo Delta source",
        source_provider="databricks",
        source_schema=namespace,
        source_table="qa_delta",
        destination_provider="postgres",
        destination_schema="public",
        destination_table="mission_orders",
        write_mode="replace",
    )
    assert verification["destination_rows_after"] == 3
