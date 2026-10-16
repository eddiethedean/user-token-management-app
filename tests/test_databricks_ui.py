"""HTTP regressions found while exercising the Databricks browser workflow."""

from __future__ import annotations

import json
import re
from dataclasses import replace
from html import unescape
from types import SimpleNamespace

import pytest

from app.config import get_settings
from app.connectors.base import (
    CatalogPage,
    ColumnSchema,
    ObjectSchema,
    RemoteNamespace,
    RemoteObject,
)
from app.connectors.databricks import DatabricksConnector
from app.connectors.errors import ConnectorError, TransferErrorCode
from app.connectors.locators import (
    DatabricksCreatePolicy,
    DatabricksTableLocator,
    DefinitionSnapshot,
    databricks_workspace_id,
    postgres_table,
)
from app.connectors.registry import ConnectorRegistry, load_builtin_connectors
from app.database import SessionLocal, stable_session_factory
from app.infrastructure.persistence.catalog_cache import SqlAlchemyCatalogCache
from app.infrastructure.runtime import ExecutionRuntime
from app.models import PipelineDefinition, PipelineRun
from app.services import transfer_engine
from app.services.catalogs import CREATE_TABLE_VALUE
from app.services.demo import seed_demo_connections
from app.services.secrets import store_user_credentials
from app.ui.presenters.feedback import verification_summary
from app.ui.presenters.run_status import destination_count_metric
from tests.helpers import USER_PASSWORD, csrf_from, web_login

WORKSPACE = "https://dbc-ui.cloud.databricks.com"
WORKSPACE_ID = databricks_workspace_id(WORKSPACE)
NAMESPACE = json.dumps([WORKSPACE_ID, "main", "analytics"], separators=(",", ":"))


@pytest.mark.parametrize(
    ("verification", "expected"),
    [
        ({"destination_rows_after": 3}, "3 rows after run"),
        ({"destination_rows_before": 3, "destination_rows_after": 6}, "3 → 6 rows"),
        ([], "Count unavailable"),
    ],
)
def test_databricks_run_review_uses_table_counts(verification, expected) -> None:
    run = SimpleNamespace(verification_json=json.dumps(verification))
    metric = destination_count_metric(run, provider="databricks")
    assert metric.props.label == "Destination table"
    assert metric.props.value == expected


def test_create_review_does_not_claim_databricks_lacks_count_support() -> None:
    run = SimpleNamespace(
        verification_json=json.dumps(
            {"verification_level": "local_manifest", "destination_rows_after": 3}
        )
    )
    assert "comparison was not recorded" in verification_summary(run)


def test_review_distinguishes_observed_delta_from_exact_per_run_verification() -> None:
    run = SimpleNamespace(
        verification_json=json.dumps(
            {"verification_level": "provider_write_count", "destination_row_delta": 3}
        )
    )
    assert verification_summary(run) == (
        "Observed destination row change: +3. Exact per-run verification was not recorded."
    )


def test_delta_publication_refreshes_only_the_owners_affected_catalog(
    access_app, make_user
) -> None:
    owner = make_user("catalog.owner@example.gov")
    other = make_user("catalog.other@example.gov")
    locator = DatabricksTableLocator(
        workspace_id=WORKSPACE_ID, catalog="main", schema="分析", table="new_delta_table"
    )
    namespace = json.dumps(
        [WORKSPACE_ID, "main", "分析"], ensure_ascii=False, separators=(",", ":")
    )
    stale_listing = {"items": []}
    with SessionLocal() as db:
        cache = SqlAlchemyCatalogCache(db, get_settings(), owner)
        other_cache = SqlAlchemyCatalogCache(db, get_settings(), other)
        cache.put("databricks", namespace, stale_listing)
        cache.put("databricks", "another-schema", stale_listing)
        cache.put("postgres", namespace, stale_listing)
        other_cache.put("databricks", namespace, stale_listing)
        transfer_engine._refresh_published_destination_cache(
            db,
            PipelineRun(user_id=owner.id),
            DefinitionSnapshot(
                name="Created Delta table",
                source_provider="postgres",
                destination_provider="databricks",
                source=postgres_table("public", "orders"),
                destination=locator,
                write_policy=DatabricksCreatePolicy(),
            ),
        )
        assert cache.get("databricks", namespace) is None
        assert cache.get("databricks", "another-schema") == stale_listing
        assert cache.get("postgres", namespace) == stale_listing
        assert other_cache.get("databricks", namespace) == stale_listing


class _UnavailableMetadata:
    capabilities = replace(DatabricksConnector.capabilities, writer_enabled=True)

    def list_namespaces(self, credentials):
        return [RemoteNamespace(NAMESPACE, "main · analytics")]

    def list_objects(self, credentials, namespace, cursor=None):
        return CatalogPage(
            items=(
                RemoteObject(
                    "orders",
                    "orders",
                    DatabricksTableLocator(
                        workspace_id=WORKSPACE_ID,
                        catalog="main",
                        schema="analytics",
                        table="orders",
                    ),
                ),
            )
        )

    def inspect_object(self, credentials, locator):
        raise ConnectorError(
            TransferErrorCode.METADATA_INCOMPLETE,
            "Databricks metadata coverage could not be verified.",
            retryable=False,
        )

    def count_rows(self, credentials, locator):
        pytest.fail("An unverified source must not be counted or scanned")


@pytest.fixture()
def metadata_ui(access_app, client, make_user, request_settings_override):
    owner = make_user("metadata.ui@example.gov")
    settings = get_settings().model_copy(
        update={
            "pipeline_enable_databricks": True,
            "pipeline_enable_databricks_writer": True,
            "pipeline_databricks_staging_schema": "private.staging",
            "pipeline_databricks_metadata_visibility_verified": True,
            "pipeline_databricks_startup_contract_verified": True,
            "pipeline_databricks_writer_live_acceptance_verified": True,
            "pipeline_allowed_https_hosts": "dbc-ui.cloud.databricks.com",
        }
    )
    with SessionLocal() as db:
        seed_demo_connections(db, settings, user=owner)
        stored = store_user_credentials(
            db,
            settings,
            user=owner,
            provider="databricks",
            credentials={
                "workspace_url": WORKSPACE,
                "client_id": "synthetic-client",
                "client_secret": "synthetic-secret",
                "compute_kind": "warehouse",
                "compute_id": "warehouse-1",
                "http_path": "sql/1.0/warehouses/warehouse-1",
            },
        )
        stored.validation_status = "connected"
        stored.runtime_status = "RUNNING"
        db.commit()
    registry = load_builtin_connectors(
        demo=True, registry=ConnectorRegistry(settings), settings=settings
    )
    registry.register(_UnavailableMetadata)
    access_app.state.execution.begin_shutdown()
    access_app.state.execution = ExecutionRuntime(settings, stable_session_factory(), registry)
    access_app.state.settings = settings
    request_settings_override(settings)
    web_login(client, owner.email, USER_PASSWORD)
    page = client.get("/pipeline")
    assert page.status_code == 200
    return client, csrf_from(page.text)


@pytest.mark.parametrize("scan", [False, True])
def test_unavailable_metadata_renders_blocked_preview_or_rejects_scan(metadata_ui, scan) -> None:
    client, csrf = metadata_ui
    response = client.post(
        "/pipeline/preview",
        data={
            "csrf_token": csrf,
            "source_provider": "databricks",
            "source_schema": NAMESPACE,
            "source_table": "orders",
            "destination_provider": "postgres",
            "destination_schema": "public",
            "destination_table": "mission_orders",
            "write_mode": "replace",
            "guardrail_actions": json.dumps(
                ["databricks_metadata", "label", "hash", "old-review-key"]
            ),
            "guardrail_scan_result": "unverified-old-scan",
            "guardrail_scan_complete": "true",
            "scan_sensitive_data": "true" if scan else "false",
        },
        headers={"HX-Request": "true", "HX-Target": "pipeline-preview-region"},
    )
    assert response.status_code == (422 if scan else 200)
    assert "old-review-key" not in response.text
    assert "unverified-old-scan" not in response.text
    if scan:
        assert "metadata coverage could not be verified" in response.text
    else:
        assert "Databricks metadata coverage incomplete" in response.text
        assert "Blocked before destination writes" in response.text
        assert "main.analytics.orders" in response.text
        assert "disabled" in response.text.split("Scan source for SSNs")[0].rsplit("<button", 1)[-1]


def test_new_databricks_target_saves_with_current_connection_identity(metadata_ui) -> None:
    client, csrf = metadata_ui
    response = client.post(
        "/pipeline/save",
        data={
            "csrf_token": csrf,
            "pipeline_name": "New Delta destination",
            "source_provider": "postgres",
            "source_schema": "public",
            "source_table": "mission_orders",
            "destination_provider": "databricks",
            "destination_schema": NAMESPACE,
            "destination_table": CREATE_TABLE_VALUE,
            "destination_table_new": "new_delta_table",
            "write_mode": "create",
        },
    )
    assert response.status_code == 303
    with SessionLocal() as db:
        pipeline = db.query(PipelineDefinition).filter_by(name="New Delta destination").one()
        locator = json.loads(pipeline.destination_locator_json)
        assert locator["table"] == "new_delta_table"
        assert locator["connection_generation"]
        assert pipeline.destination_create is True


def test_databricks_destination_preview_counts_the_inspected_connection_identity(
    metadata_ui, monkeypatch
) -> None:
    client, csrf = metadata_ui
    monkeypatch.setattr(
        _UnavailableMetadata,
        "inspect_object",
        lambda self, credentials, locator: ObjectSchema(
            locator=locator,
            columns=(ColumnSchema(name="id", data_type="Int64"),),
            metadata_coverage="verified",
        ),
    )

    def count(self, credentials, locator):
        assert locator.connection_generation == credentials["connection_generation"]
        assert locator.connection_generation
        return 7

    monkeypatch.setattr(_UnavailableMetadata, "count_rows", count)
    response = client.post(
        "/pipeline/preview",
        data={
            "csrf_token": csrf,
            "source_provider": "postgres",
            "source_schema": "public",
            "source_table": "mission_orders",
            "destination_provider": "databricks",
            "destination_schema": NAMESPACE,
            "destination_table": "orders",
            "write_mode": "append",
        },
        headers={"HX-Request": "true", "HX-Target": "pipeline-preview-region"},
    )
    assert response.status_code == 200
    assert "Rows: Exact" in response.text
    visible_text = " ".join(unescape(re.sub(r"<[^>]+>", " ", response.text)).split())
    assert "Rows before run 7" in visible_text


@pytest.mark.parametrize("action", ["hash", "remove"])
def test_databricks_review_action_survives_preview_refresh_and_save(
    metadata_ui, monkeypatch, action
) -> None:
    client, csrf = metadata_ui

    def inspect(self, credentials, locator):
        return ObjectSchema(
            locator=locator.model_copy(update={"object_id": "orders-1"}),
            columns=(
                ColumnSchema(name="id", data_type="Int64"),
                ColumnSchema(name="label", data_type="String", sensitivity_markers=("contact",)),
            ),
            metadata_coverage="verified",
            schema_fingerprint="a" * 64,
            sensitivity_fingerprint="b" * 64,
        )

    monkeypatch.setattr(_UnavailableMetadata, "inspect_object", inspect)
    fields = {
        "csrf_token": csrf,
        "pipeline_name": f"Reviewed {action}",
        "source_provider": "databricks",
        "source_schema": NAMESPACE,
        "source_table": "orders",
        "destination_provider": "postgres",
        "destination_schema": "public",
        "destination_table": "mission_orders",
        "write_mode": "replace",
    }
    headers = {"HX-Request": "true", "HX-Target": "pipeline-preview-region"}
    preview = client.post("/pipeline/preview", data=fields, headers=headers)
    assert preview.status_code == 200
    option = re.search(
        r'<option[^>]*value="([^"]+)"[^>]*>' + action.title() + r"</option>", preview.text
    )
    assert option is not None
    decision = unescape(option.group(1))
    fields["guardrail_actions"] = decision
    refreshed = client.post("/pipeline/preview", data=fields, headers=headers)
    assert refreshed.status_code == 200
    selected = re.search(
        r'<option[^>]*value="([^"]+)"[^>]*selected[^>]*>' + action.title() + r"</option>",
        refreshed.text,
    )
    assert selected is not None
    assert unescape(selected.group(1)) == decision
    saved = client.post("/pipeline/save", data=fields)
    assert saved.status_code == 303
    with SessionLocal() as db:
        pipeline = db.query(PipelineDefinition).filter_by(name=f"Reviewed {action}").one()
        decisions = json.loads(pipeline.guardrail_actions_json)
        assert len(decisions) == 1
        assert decisions[0]["action"] == action
        assert decisions[0]["source_key"] == json.loads(decision)[3]
