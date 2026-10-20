"""Databricks management contracts over real HTTP and generated SDK models.

MiniLake does not implement the SQL connector's Thrift protocol. Only
the injected management factory redirects to loopback; production endpoint
validation stays enabled, and any unexpected SQL connection fails the test.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import timedelta
from typing import Any
from uuid import uuid4

import pytest
from databricks.sdk import WorkspaceClient
from databricks.sdk.core import Config
from databricks.sdk.service.sql import State
from sqlalchemy import select

from app.config import Settings, get_settings
from app.connectors.databricks import DatabricksConnector
from app.connectors.errors import ConnectorError, TransferErrorCode
from app.connectors.locators import databricks_workspace_id
from app.database import SessionLocal
from app.domain.feedback import FeedbackCode
from app.models import DatabricksComputeAttempt, User, UserSecret, utcnow
from app.services.databricks_compute import (
    cancel_compute_observation,
    create_compute_attempt,
    process_due_compute_attempt,
    request_compute_start,
)
from app.services.secrets import (
    decrypt_user_credentials_for_run,
    store_user_credentials,
)
from app.services.secrets import test_user_connection as check_user_connection
from tests.minilake_support import run_minilake

pytestmark = [pytest.mark.integration, pytest.mark.minilake]

WORKSPACE_HOST = "dbc-minilake.cloud.databricks.com"
WORKSPACE_URL = f"https://{WORKSPACE_HOST}"


@pytest.fixture(scope="session")
def minilake_endpoint(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    with run_minilake(tmp_path_factory.mktemp("minilake")) as endpoint:
        yield endpoint


@dataclass
class MiniLakeConnection:
    client: WorkspaceClient
    connector: DatabricksConnector
    credentials: dict[str, Any]
    settings: Settings
    sql_calls: list[dict[str, Any]]


@pytest.fixture()
def minilake_connection(minilake_endpoint: str) -> MiniLakeConnection:
    client = WorkspaceClient(
        config=Config(
            host=minilake_endpoint,
            token="minilake-test-token",
            auth_type="pat",
            http_timeout_seconds=2,
            retry_timeout_seconds=2,
        )
    )
    warehouse = client.warehouses.create(name=f"connection-{uuid4().hex}", cluster_size="Small")
    assert warehouse.id is not None
    settings = get_settings().model_copy(
        update={
            "pipeline_enable_databricks": True,
            "pipeline_allowed_https_hosts": WORKSPACE_HOST,
            "pipeline_databricks_startup_contract_verified": True,
        }
    )
    credentials: dict[str, Any] = {
        "workspace_url": WORKSPACE_URL,
        "token": "synthetic-api-token",
        "compute_kind": "warehouse",
        "compute_id": warehouse.id,
        "http_path": f"sql/1.0/warehouses/{warehouse.id}",
        "startup_policy": "manual",
    }
    sql_calls: list[dict[str, Any]] = []

    def workspace_factory(**kwargs) -> WorkspaceClient:
        assert kwargs["host"] == WORKSPACE_URL
        assert kwargs["auth_type"] == "pat"
        assert kwargs["token"] == credentials["token"]
        return client

    def unexpected_sql(**kwargs):
        sql_calls.append(kwargs)
        pytest.fail("Management validation/readiness must not open a SQL connection")

    connector = DatabricksConnector(
        settings,
        workspace_client_factory=workspace_factory,
        sql_connect=unexpected_sql,
    )
    return MiniLakeConnection(client, connector, credentials, settings, sql_calls)


def test_management_health_uses_real_sdk_state_enums(
    minilake_connection: MiniLakeConnection,
) -> None:
    dbx = minilake_connection
    assert dbx.client.warehouses.get(id=dbx.credentials["compute_id"]).state is State.RUNNING

    health = dbx.connector.test_connection(dbx.credentials)
    assert health.status == "connected"
    assert health.runtime_status == "RUNNING"
    assert "SQL readiness: ready" in health.message
    assert health.server_identity == databricks_workspace_id(WORKSPACE_URL)
    assert dbx.connector.compute_status(dbx.credentials)["state"] == "RUNNING"
    assert dbx.sql_calls == []


def test_stopped_connection_checks_are_read_only_and_block_manual_sql(
    minilake_connection: MiniLakeConnection,
) -> None:
    dbx = minilake_connection
    warehouse_id = dbx.credentials["compute_id"]
    dbx.client.warehouses.stop(id=warehouse_id)

    health = dbx.connector.test_connection(dbx.credentials)
    assert health.status == "connected"
    assert health.runtime_status == "STOPPED"
    assert "SQL readiness: not ready" in health.message
    assert dbx.connector.compute_status(dbx.credentials)["state"] == "STOPPED"
    with pytest.raises(ConnectorError) as blocked:
        dbx.connector.list_namespaces(dbx.credentials)
    assert blocked.value.code is TransferErrorCode.COMPUTE_NOT_READY
    assert not blocked.value.retryable
    assert dbx.client.warehouses.get(id=warehouse_id).state is State.STOPPED
    assert dbx.sql_calls == []


def test_explicit_start_is_idempotent_after_compute_becomes_ready(
    minilake_connection: MiniLakeConnection,
) -> None:
    dbx = minilake_connection
    dbx.client.warehouses.stop(id=dbx.credentials["compute_id"])

    assert dbx.connector.start_compute(dbx.credentials) == {
        "state": "STOPPED",
        "outcome": "start_submitted",
        "kind": "warehouse",
    }
    assert dbx.connector.compute_status(dbx.credentials)["state"] == "RUNNING"
    assert dbx.connector.start_compute(dbx.credentials) == {
        "state": "RUNNING",
        "outcome": "already_running",
        "kind": "warehouse",
    }
    assert dbx.sql_calls == []


@pytest.mark.parametrize("operation", ["test_connection", "compute_status", "start_compute"])
def test_real_sdk_not_found_errors_are_safe_and_terminal(
    minilake_connection: MiniLakeConnection, operation: str
) -> None:
    dbx = minilake_connection
    credentials = {**dbx.credentials, "compute_id": "missing-compute-canary"}
    with pytest.raises(ConnectorError) as missing:
        getattr(dbx.connector, operation)(credentials)
    assert missing.value.code is TransferErrorCode.SOURCE_NOT_FOUND
    assert not missing.value.retryable
    assert "missing-compute-canary" not in missing.value.summary
    assert "synthetic-client-secret" not in str(missing.value)
    assert dbx.sql_calls == []


def test_emulated_readiness_does_not_bypass_startup_attestation(
    minilake_connection: MiniLakeConnection,
) -> None:
    dbx = minilake_connection
    dbx.connector.settings = dbx.settings.model_copy(
        update={"pipeline_databricks_startup_contract_verified": False}
    )
    with pytest.raises(ConnectorError) as blocked:
        dbx.connector.list_namespaces(dbx.credentials)
    assert blocked.value.code is TransferErrorCode.COMPUTE_NOT_READY
    assert dbx.sql_calls == []


def test_saved_connection_health_tracks_real_warehouse_transitions(
    access_app, make_user, minilake_connection: MiniLakeConnection
) -> None:
    dbx = minilake_connection
    owner = make_user("minilake.health@example.gov")
    dbx.client.warehouses.stop(id=dbx.credentials["compute_id"])
    with SessionLocal() as db:
        secret = store_user_credentials(
            db, dbx.settings, user=owner, provider="databricks", credentials=dbx.credentials
        )
        generation = secret.updated_at
        stopped = check_user_connection(
            db,
            settings=dbx.settings,
            user=owner,
            provider="databricks",
            connector_resolver=lambda _provider: dbx.connector,
        )
        assert stopped.validation_status == "connected"
        assert stopped.runtime_status == "STOPPED"
        assert stopped.updated_at == generation
        assert dbx.client.warehouses.get(id=dbx.credentials["compute_id"]).state is State.STOPPED

        dbx.connector.start_compute(dbx.credentials)
        ready = check_user_connection(
            db,
            settings=dbx.settings,
            user=owner,
            provider="databricks",
            connector_resolver=lambda _provider: dbx.connector,
        )
        assert ready.validation_status == "connected"
        assert ready.runtime_status == "RUNNING"
        assert ready.updated_at == generation
    assert dbx.sql_calls == []


def test_opted_in_sql_defers_to_durable_start_and_worker_poll_over_http(
    access_app, make_user, minilake_connection: MiniLakeConnection
) -> None:
    dbx = minilake_connection
    owner = make_user("minilake.start@example.gov")
    dbx.credentials["startup_policy"] = "allow_start"
    dbx.client.warehouses.stop(id=dbx.credentials["compute_id"])
    with SessionLocal() as db:
        store_user_credentials(
            db, dbx.settings, user=owner, provider="databricks", credentials=dbx.credentials
        )
        credentials = decrypt_user_credentials_for_run(
            db, dbx.settings, user=owner, provider="databricks"
        )

        def authorize_start(observed_state: str) -> dict[str, str]:
            return request_compute_start(
                db,
                settings=dbx.settings,
                user=owner,
                credentials=credentials,
                connector_resolver=lambda _provider: dbx.connector,
                observed_state=observed_state,
                require_opt_in=True,
            )

        authorized_credentials: dict[str, Any] = {
            **credentials,
            "_startup_request": authorize_start,
        }
        with pytest.raises(ConnectorError) as deferred:
            dbx.connector.list_namespaces(authorized_credentials)
        assert deferred.value.code is TransferErrorCode.COMPUTE_NOT_READY
        assert deferred.value.retryable
        assert dbx.client.warehouses.get(id=credentials["compute_id"]).state is State.RUNNING
        attempt = db.scalar(
            select(DatabricksComputeAttempt).where(DatabricksComputeAttempt.user_id == owner.id)
        )
        assert attempt is not None and attempt.status == "starting"
        attempt_id = attempt.id
        attempt.next_poll_at = utcnow() - timedelta(seconds=1)
        db.commit()

    assert process_due_compute_attempt(
        settings=dbx.settings,
        session_factory=SessionLocal,
        connector_resolver=lambda _provider: dbx.connector,
    )
    with SessionLocal() as db:
        saved = db.get(DatabricksComputeAttempt, attempt_id)
        secret = db.scalar(
            select(UserSecret).where(
                UserSecret.user_id == owner.id, UserSecret.provider == "databricks"
            )
        )
        assert saved is not None and saved.status == "ready"
        assert saved.last_state == "RUNNING"
        assert saved.active_key is None
        assert saved.poll_count == 1
        assert secret is not None and secret.runtime_status == "RUNNING"
    assert dbx.sql_calls == []


def test_classic_cluster_with_unverifiable_uc_metadata_fails_closed(
    minilake_connection: MiniLakeConnection,
) -> None:
    dbx = minilake_connection
    cluster = dbx.client.clusters.create(
        spark_version="14.3.x-scala2.12", cluster_name=f"classic-{uuid4().hex}", num_workers=1
    )
    credentials = {
        **dbx.credentials,
        "compute_kind": "classic_cluster",
        "compute_id": cluster.cluster_id,
    }
    with pytest.raises(ConnectorError) as unsupported:
        dbx.connector.test_connection(credentials)
    assert unsupported.value.code is TransferErrorCode.UNSUPPORTED_TYPE
    assert dbx.sql_calls == []


def test_allow_start_without_owner_authorization_cannot_start_warehouse(
    minilake_connection: MiniLakeConnection,
) -> None:
    dbx = minilake_connection
    dbx.credentials["startup_policy"] = "allow_start"
    dbx.client.warehouses.stop(id=dbx.credentials["compute_id"])
    with pytest.raises(ConnectorError) as denied:
        dbx.connector.list_namespaces(dbx.credentials)
    assert denied.value.code is TransferErrorCode.PERMISSION_DENIED
    assert dbx.client.warehouses.get(id=dbx.credentials["compute_id"]).state is State.STOPPED
    assert dbx.sql_calls == []


def test_missing_saved_compute_persists_safe_connection_failure(
    access_app, make_user, minilake_connection: MiniLakeConnection
) -> None:
    dbx = minilake_connection
    owner = make_user("minilake.missing@example.gov")
    credentials = {
        **dbx.credentials,
        "compute_id": "missing-compute-canary",
        "http_path": "sql/1.0/warehouses/missing-compute-canary",
    }
    with SessionLocal() as db:
        store_user_credentials(
            db, dbx.settings, user=owner, provider="databricks", credentials=credentials
        )
        failed = check_user_connection(
            db,
            settings=dbx.settings,
            user=owner,
            provider="databricks",
            connector_resolver=lambda _provider: dbx.connector,
        )
        assert failed.validation_status == "failed"
        assert failed.validation_code == FeedbackCode.CONNECTION_INVALID
        assert failed.runtime_status == ""
        assert "missing-compute-canary" not in failed.validation_message
        assert "synthetic-client-secret" not in failed.validation_message
    assert dbx.sql_calls == []


def _replacement_credentials(dbx: MiniLakeConnection) -> dict[str, Any]:
    warehouse = dbx.client.warehouses.create(
        name=f"replacement-{uuid4().hex}", cluster_size="Small"
    )
    assert warehouse.id is not None
    dbx.client.warehouses.stop(id=warehouse.id)
    return {
        **dbx.credentials,
        "compute_id": warehouse.id,
        "http_path": f"sql/1.0/warehouses/{warehouse.id}",
        "token": "rotated-api-token-canary",
        "startup_policy": "manual",
    }


def _rotate_saved_connection(
    dbx: MiniLakeConnection, user_id: str, credentials: dict[str, Any]
) -> str:
    with SessionLocal() as db:
        owner = db.get(User, user_id)
        assert owner is not None
        stored = store_user_credentials(
            db, dbx.settings, user=owner, provider="databricks", credentials=credentials
        )
        return stored.updated_at.isoformat(timespec="microseconds")


def test_connection_rotation_during_health_check_discards_old_provider_result(
    access_app, make_user, minilake_connection: MiniLakeConnection
) -> None:
    dbx = minilake_connection
    owner = make_user("minilake.rotate-health@example.gov")
    replacement = _replacement_credentials(dbx)
    rotated_generations: list[str] = []

    class RotateDuringHealthCheck:
        capabilities = dbx.connector.capabilities

        def test_connection(self, credentials):
            health = dbx.connector.test_connection(credentials)
            rotated_generations.append(_rotate_saved_connection(dbx, owner.id, replacement))
            return health

    with SessionLocal() as db:
        store_user_credentials(
            db, dbx.settings, user=owner, provider="databricks", credentials=dbx.credentials
        )
        result = check_user_connection(
            db,
            settings=dbx.settings,
            user=owner,
            provider="databricks",
            connector_resolver=lambda _provider: RotateDuringHealthCheck(),
        )
        assert result.validation_status == "untested"
        assert result.runtime_status == ""
        assert result.updated_at.isoformat(timespec="microseconds") == rotated_generations[0]
    assert dbx.client.warehouses.get(id=replacement["compute_id"]).state is State.STOPPED
    assert dbx.sql_calls == []


@pytest.mark.parametrize("provider_response", ["success", "ambiguous", "denied"])
def test_connection_rotation_during_start_discards_obsolete_response(
    access_app, make_user, minilake_connection: MiniLakeConnection, provider_response: str
) -> None:
    dbx = minilake_connection
    owner = make_user("minilake.rotate-start@example.gov")
    old_warehouse = dbx.credentials["compute_id"]
    dbx.client.warehouses.stop(id=old_warehouse)
    replacement = _replacement_credentials(dbx)

    class RotateDuringStart:
        def start_compute(self, credentials):
            result = dbx.connector.start_compute(credentials)
            _rotate_saved_connection(dbx, owner.id, replacement)
            if provider_response != "success":
                raise ConnectorError(
                    TransferErrorCode.PROVIDER_UNAVAILABLE
                    if provider_response == "ambiguous"
                    else TransferErrorCode.PERMISSION_DENIED,
                    "obsolete-provider-response",
                    retryable=provider_response == "ambiguous",
                )
            return result

    with SessionLocal() as db:
        store_user_credentials(
            db, dbx.settings, user=owner, provider="databricks", credentials=dbx.credentials
        )
        credentials = decrypt_user_credentials_for_run(
            db, dbx.settings, user=owner, provider="databricks"
        )
        with pytest.raises(ConnectorError) as stale:
            request_compute_start(
                db,
                settings=dbx.settings,
                user=owner,
                credentials=credentials,
                connector_resolver=lambda _provider: RotateDuringStart(),
            )
        assert stale.value.code is TransferErrorCode.CREDENTIALS_STALE
        assert "rotated-secret-canary" not in stale.value.summary
        assert "obsolete-provider-response" not in stale.value.summary

    with SessionLocal() as db:
        attempt = db.scalar(
            select(DatabricksComputeAttempt).where(DatabricksComputeAttempt.user_id == owner.id)
        )
        stored = db.scalar(
            select(UserSecret).where(
                UserSecret.user_id == owner.id, UserSecret.provider == "databricks"
            )
        )
        assert attempt is not None and attempt.status == "stale"
        assert attempt.active_key is None
        assert stored is not None and stored.runtime_status == ""
        assert stored.validation_status == "untested"
        assert (
            stored.updated_at.isoformat(timespec="microseconds")
            != credentials["connection_generation"]
        )
    assert dbx.client.warehouses.get(id=old_warehouse).state is State.RUNNING
    assert dbx.client.warehouses.get(id=replacement["compute_id"]).state is State.STOPPED
    assert dbx.sql_calls == []


def test_connection_rotation_during_readiness_poll_keeps_new_connection_untested(
    access_app, make_user, minilake_connection: MiniLakeConnection
) -> None:
    dbx = minilake_connection
    owner = make_user("minilake.rotate-poll@example.gov")
    replacement = _replacement_credentials(dbx)
    with SessionLocal() as db:
        store_user_credentials(
            db, dbx.settings, user=owner, provider="databricks", credentials=dbx.credentials
        )
        credentials = decrypt_user_credentials_for_run(
            db, dbx.settings, user=owner, provider="databricks"
        )
        attempt, _ = create_compute_attempt(
            db, user=owner, credentials=credentials, settings=dbx.settings
        )
        attempt_id = attempt.id
        attempt.next_poll_at = utcnow() - timedelta(seconds=1)
        db.commit()

    class RotateDuringPoll:
        def compute_status(self, saved_credentials):
            status = dbx.connector.compute_status(saved_credentials)
            _rotate_saved_connection(dbx, owner.id, replacement)
            return status

    assert process_due_compute_attempt(
        settings=dbx.settings,
        session_factory=SessionLocal,
        connector_resolver=lambda _provider: RotateDuringPoll(),
    )
    with SessionLocal() as db:
        saved = db.get(DatabricksComputeAttempt, attempt_id)
        stored = db.scalar(
            select(UserSecret).where(
                UserSecret.user_id == owner.id, UserSecret.provider == "databricks"
            )
        )
        assert saved is not None and saved.status == "stale"
        assert saved.poll_count == 0
        assert stored is not None and stored.runtime_status == ""
        assert stored.validation_status == "untested"
    assert dbx.sql_calls == []


def test_cancelled_observation_is_not_resumed_by_an_inflight_start_response(
    access_app, make_user, minilake_connection: MiniLakeConnection
) -> None:
    dbx = minilake_connection
    owner = make_user("minilake.cancel-inflight-start@example.gov")
    dbx.client.warehouses.stop(id=dbx.credentials["compute_id"])

    class CancelDuringStart:
        def start_compute(self, credentials):
            result = dbx.connector.start_compute(credentials)
            with SessionLocal() as cancellation_db:
                attempt = cancellation_db.scalar(
                    select(DatabricksComputeAttempt).where(
                        DatabricksComputeAttempt.user_id == owner.id
                    )
                )
                assert attempt is not None
                assert cancel_compute_observation(
                    cancellation_db, user_id=owner.id, attempt_id=attempt.id
                )
            return result

    with SessionLocal() as db:
        store_user_credentials(
            db, dbx.settings, user=owner, provider="databricks", credentials=dbx.credentials
        )
        credentials = decrypt_user_credentials_for_run(
            db, dbx.settings, user=owner, provider="databricks"
        )
        with pytest.raises(ConnectorError) as cancelled:
            request_compute_start(
                db,
                settings=dbx.settings,
                user=owner,
                credentials=credentials,
                connector_resolver=lambda _provider: CancelDuringStart(),
            )
        assert cancelled.value.code is TransferErrorCode.COMPUTE_NOT_READY

    with SessionLocal() as db:
        saved = db.scalar(
            select(DatabricksComputeAttempt).where(DatabricksComputeAttempt.user_id == owner.id)
        )
        secret = db.scalar(
            select(UserSecret).where(
                UserSecret.user_id == owner.id, UserSecret.provider == "databricks"
            )
        )
        assert saved is not None and saved.status == "cancelled"
        assert saved.active_key is not None
        assert saved.next_poll_at is not None
        assert secret is not None and secret.runtime_status == "OBSERVATION_CANCELLED"
    assert dbx.sql_calls == []
