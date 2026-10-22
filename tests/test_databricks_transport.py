"""Real SDK requests through scripted HTTP, PAT and TCP failure boundaries."""

from __future__ import annotations

import time
from collections.abc import Iterator
from datetime import timedelta
from typing import Any

import pytest
from databricks.sdk import WorkspaceClient
from databricks.sdk.core import Config
from sqlalchemy import select

from app.config import get_settings
from app.connectors.databricks import DatabricksConnector
from app.connectors.errors import ConnectorError, TransferErrorCode
from app.database import SessionLocal
from app.models import DatabricksComputeAttempt, UserSecret, utcnow
from app.services.databricks_compute import process_due_compute_attempt, request_compute_start
from app.services.secrets import decrypt_user_credentials_for_run, store_user_credentials
from tests.databricks_tools_support import (
    ToxicProxy,
    WireMock,
    request_json,
    run_toxiproxy,
    run_wiremock,
)
from tests.minilake_support import run_minilake

pytestmark = [pytest.mark.integration, pytest.mark.databricks_transport]
HOST = "dbc-transport.cloud.databricks.com"
WAREHOUSE = "/api/2.0/sql/warehouses/test-warehouse"
CREDENTIALS = {
    "workspace_url": f"https://{HOST}",
    "token": "synthetic-api-token-never-log",
    "compute_kind": "warehouse",
    "compute_id": "test-warehouse",
    "http_path": "sql/1.0/warehouses/test-warehouse",
}


@pytest.fixture(scope="session")
def wiremock_server(tmp_path_factory) -> Iterator[str]:
    with run_wiremock(tmp_path_factory.mktemp("wiremock")) as endpoint:
        yield endpoint


@pytest.fixture()
def wiremock(wiremock_server: str) -> WireMock:
    request_json(wiremock_server, "/__admin/reset", method="POST")
    server = WireMock(wiremock_server)
    server.stub("GET", "/.well-known/databricks-config", _json({}))
    return server


@pytest.fixture(scope="session")
def toxiproxy_server(tmp_path_factory) -> Iterator[str]:
    with run_toxiproxy(tmp_path_factory.mktemp("toxiproxy")) as endpoint:
        yield endpoint


@pytest.fixture(scope="session")
def transport_minilake(tmp_path_factory) -> Iterator[str]:
    with run_minilake(tmp_path_factory.mktemp("transport-minilake")) as endpoint:
        yield endpoint


def _client(endpoint: str, *, retry_seconds: int = 1) -> WorkspaceClient:
    return WorkspaceClient(
        config=Config(
            host=endpoint,
            token=CREDENTIALS["token"],
            auth_type="pat",
            http_timeout_seconds=1,
            retry_timeout_seconds=retry_seconds,
        )
    )


def _connector(client: WorkspaceClient) -> DatabricksConnector:
    def factory(**kwargs):
        assert kwargs["host"] == CREDENTIALS["workspace_url"]
        assert kwargs["auth_type"] == "pat"
        assert kwargs["token"] == CREDENTIALS["token"]
        return client

    def no_sql(**kwargs):
        pytest.fail("Management and authentication checks must never open SQL")

    settings = get_settings().model_copy(
        update={"pipeline_enable_databricks": True, "pipeline_allowed_https_hosts": HOST}
    )
    return DatabricksConnector(settings, workspace_client_factory=factory, sql_connect=no_sql)


def _json(body: dict[str, Any], status: int = 200) -> dict[str, Any]:
    return {"status": status, "headers": {"Content-Type": "application/json"}, "jsonBody": body}


def _warehouse(state: str) -> dict[str, Any]:
    return _json({"id": "test-warehouse", "name": "Synthetic warehouse", "state": state})


def test_scripted_lifecycle_uses_real_sdk_without_starting_from_health(wiremock: WireMock) -> None:
    for previous, state in [
        ("Started", "STOPPED"),
        ("STOPPED", "STARTING"),
        ("STARTING", "RUNNING"),
    ]:
        wiremock.stub(
            "GET",
            WAREHOUSE,
            _warehouse(state),
            scenarioName="warehouse",
            requiredScenarioState=previous,
            newScenarioState=state,
        )
    connector = _connector(_client(wiremock.endpoint))
    states = [connector.test_connection(CREDENTIALS).runtime_status for _ in range(3)]
    assert states == ["STOPPED", "STARTING", "RUNNING"]
    assert len(wiremock.requests("GET", WAREHOUSE)) == 3
    assert wiremock.requests("POST", WAREHOUSE + "/start") == []


@pytest.mark.parametrize("failure", ["throttled", "unavailable", "stalled"])
def test_optional_expiry_failure_does_not_delay_successful_health_check(
    wiremock: WireMock, failure
) -> None:
    wiremock.stub("GET", WAREHOUSE, _warehouse("RUNNING"))
    response = _json({"error_code": "TEMPORARILY_UNAVAILABLE"}, 503)
    response["headers"]["Retry-After"] = "600"
    if failure == "throttled":
        response["status"] = 429
    if failure == "stalled":
        response["fixedDelayMilliseconds"] = 10000
    wiremock.stub("GET", "/api/2.0/token/list", response)
    connector = _connector(_client(wiremock.endpoint, retry_seconds=300))
    started = time.monotonic()
    health = connector.test_connection(CREDENTIALS)
    elapsed = time.monotonic() - started
    assert health.status == "connected"
    assert health.runtime_status == "RUNNING"
    assert health.token_expiry.source == "unknown"
    assert elapsed < 5
    assert len(wiremock.requests("GET", "/api/2.0/token/list")) == 1


@pytest.mark.parametrize(
    "status,code", [(429, "RESOURCE_EXHAUSTED"), (503, "TEMPORARILY_UNAVAILABLE")]
)
def test_sdk_retries_transient_http_response_then_decodes_state(
    wiremock: WireMock, status: int, code: str
) -> None:
    response = _json({"error_code": code, "message": "synthetic transient failure"}, status)
    response["headers"]["Retry-After"] = "0"
    wiremock.stub(
        "GET",
        WAREHOUSE,
        response,
        scenarioName="retry",
        requiredScenarioState="Started",
        newScenarioState="healthy",
    )
    wiremock.stub(
        "GET",
        WAREHOUSE,
        _warehouse("RUNNING"),
        scenarioName="retry",
        requiredScenarioState="healthy",
    )
    health = _connector(_client(wiremock.endpoint, retry_seconds=5)).test_connection(CREDENTIALS)
    assert health.runtime_status == "RUNNING"
    assert len(wiremock.requests("GET", WAREHOUSE)) == 2


@pytest.mark.parametrize(
    "status,provider_code,expected",
    [
        (401, "UNAUTHENTICATED", TransferErrorCode.AUTHENTICATION_FAILED),
        (403, "PERMISSION_DENIED", TransferErrorCode.PERMISSION_DENIED),
        (404, "RESOURCE_DOES_NOT_EXIST", TransferErrorCode.SOURCE_NOT_FOUND),
    ],
)
def test_real_sdk_errors_are_classified_and_sanitized(
    wiremock: WireMock, status: int, provider_code: str, expected: TransferErrorCode
) -> None:
    wiremock.stub(
        "GET",
        WAREHOUSE,
        _json({"error_code": provider_code, "message": CREDENTIALS["token"]}, status),
    )
    with pytest.raises(ConnectorError) as failure:
        _connector(_client(wiremock.endpoint)).test_connection(CREDENTIALS)
    assert failure.value.code is expected
    assert failure.value.retryable is False
    assert CREDENTIALS["token"] not in str(failure.value)
    assert wiremock.requests("POST", WAREHOUSE + "/start") == []


def test_pat_is_sent_as_bearer_and_does_not_call_token_endpoint(wiremock: WireMock) -> None:
    wiremock.stub("GET", WAREHOUSE, _warehouse("RUNNING"))
    connector = _connector(_client(wiremock.endpoint))
    assert connector.test_connection(CREDENTIALS).runtime_status == "RUNNING"
    auth_headers = [r["headers"]["Authorization"] for r in wiremock.requests("GET", WAREHOUSE)]
    assert auth_headers == [f"Bearer {CREDENTIALS['token']}"]
    assert wiremock.requests("POST", "/oidc/v1/token") == []


def test_pat_auth_failure_does_not_expose_token(wiremock: WireMock) -> None:
    wiremock.stub(
        "GET",
        WAREHOUSE,
        _json({"error_code": "UNAUTHENTICATED", "message": CREDENTIALS["token"]}, 401),
    )
    with pytest.raises(ConnectorError) as failure:
        _connector(_client(wiremock.endpoint)).test_connection(CREDENTIALS)
    assert failure.value.code is TransferErrorCode.AUTHENTICATION_FAILED
    assert failure.value.retryable is False
    assert CREDENTIALS["token"] not in str(failure.value)


def test_lost_start_response_is_fenced_then_resolved_by_readiness_poll(
    access_app, make_user, wiremock: WireMock
) -> None:
    wiremock.stub(
        "GET",
        WAREHOUSE,
        _warehouse("STOPPED"),
        scenarioName="start",
        requiredScenarioState="Started",
    )
    wiremock.stub(
        "POST",
        WAREHOUSE + "/start",
        {"fault": "EMPTY_RESPONSE"},
        scenarioName="start",
        requiredScenarioState="Started",
        newScenarioState="running",
    )
    wiremock.stub(
        "POST",
        WAREHOUSE + "/start",
        {"fault": "EMPTY_RESPONSE"},
        scenarioName="start",
        requiredScenarioState="running",
    )
    wiremock.stub(
        "GET",
        WAREHOUSE,
        _warehouse("RUNNING"),
        scenarioName="start",
        requiredScenarioState="running",
    )
    owner = make_user("transport.start@example.gov")
    connector = _connector(_client(wiremock.endpoint))
    with SessionLocal() as db:
        store_user_credentials(
            db, connector.settings, user=owner, provider="databricks", credentials=CREDENTIALS
        )
        credentials = decrypt_user_credentials_for_run(
            db, connector.settings, user=owner, provider="databricks"
        )
        result = request_compute_start(
            db,
            settings=connector.settings,
            user=owner,
            credentials=credentials,
            connector_resolver=lambda _: connector,
        )
        assert result["outcome"] == "start_uncertain"
        posts = len(wiremock.requests("POST", WAREHOUSE + "/start"))
        assert posts >= 1
        duplicate = request_compute_start(
            db,
            settings=connector.settings,
            user=owner,
            credentials=credentials,
            connector_resolver=lambda _: connector,
        )
        assert duplicate["outcome"] == "already_starting"
        assert len(wiremock.requests("POST", WAREHOUSE + "/start")) == posts
        attempt = db.scalar(
            select(DatabricksComputeAttempt).where(DatabricksComputeAttempt.user_id == owner.id)
        )
        assert attempt is not None and attempt.active_key is not None
        attempt.next_poll_at = utcnow() - timedelta(seconds=1)
        db.commit()
        attempt_id = attempt.id
    assert (
        process_due_compute_attempt(
            settings=connector.settings,
            session_factory=SessionLocal,
            connector_resolver=lambda _: connector,
        )
        is True
    )
    with SessionLocal() as db:
        attempt = db.get(DatabricksComputeAttempt, attempt_id)
        assert attempt is not None and attempt.status == "ready"
        assert attempt.active_key is None
        stored = db.scalar(
            select(UserSecret).where(
                UserSecret.user_id == owner.id, UserSecret.provider == "databricks"
            )
        )
        assert stored is not None and stored.runtime_status == "RUNNING"
    assert len(wiremock.requests("POST", WAREHOUSE + "/start")) == posts


@pytest.mark.parametrize(
    "kind,attributes",
    [
        ("reset_peer", {"timeout": 0}),
        ("limit_data", {"bytes": 200}),
        ("timeout", {"timeout": 0}),
    ],
)
def test_minilake_network_fault_is_retryable_and_connection_recovers(
    toxiproxy_server: str, transport_minilake: str, kind: str, attributes: dict[str, int]
) -> None:
    direct = _client(transport_minilake)
    warehouse = direct.warehouses.create(name="network-fault", cluster_size="Small")
    assert warehouse.id
    credentials = {**CREDENTIALS, "compute_id": warehouse.id}
    proxy = ToxicProxy(toxiproxy_server, transport_minilake)
    try:
        connector = _connector(_client(proxy.endpoint))
        assert connector.test_connection(credentials).runtime_status == "RUNNING"
        # Start the fault on a fresh SDK connection. An existing keep-alive
        # stream can have a read already buffered when a toxic is installed.
        connector = _connector(_client(proxy.endpoint))
        proxy.add(kind, attributes)
        with pytest.raises(ConnectorError) as failure:
            connector.test_connection(credentials)
        assert failure.value.code in {
            TransferErrorCode.CONNECTION_TIMEOUT,
            TransferErrorCode.PROVIDER_UNAVAILABLE,
        }
        assert failure.value.retryable is True
        assert CREDENTIALS["token"] not in str(failure.value)
        proxy.clear()
        assert connector.test_connection(credentials).runtime_status == "RUNNING"
        assert direct.warehouses.get(id=warehouse.id).state is not None
    finally:
        proxy.close()
        direct.warehouses.delete(id=warehouse.id)
