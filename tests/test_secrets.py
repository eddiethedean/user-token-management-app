"""API-token secret storage and HTMX UI coverage for Hedron."""

from __future__ import annotations

import pytest
from hedron.testing import assert_fragment_body, assert_html_contains
from sqlalchemy import func, select

from app.config import get_settings
from app.database import SessionLocal
from app.models import ApiTokenKeyUsage, DatabricksComputeAttempt, User, UserSecret, utcnow
from app.services.secrets import (
    SecretStorageError,
    decrypt_user_credentials_for_run,
    decrypt_user_secret_for_run,
    store_user_credentials,
)
from tests.helpers import (
    MSS_TOKEN,
    USER_PASSWORD,
    as_adapter,
    csrf_from,
    web_login,
)


def test_secret_slots_render_and_htmx_never_reveals_token(client, htmx, make_user) -> None:
    user = make_user("secrets.htmx@example.gov")
    web_login(client, user.email, USER_PASSWORD)
    # Seed cookies onto the HTMX client
    for key, value in client.cookies.items():
        htmx.cookies.set(key, value)

    security = htmx.get("/security")
    assert security.status_code == 200
    assert all(label in security.text for label in ("MSS", "MCS-COP", "PostgreSQL"))
    assert "Analytics API" not in security.text
    assert 'id="postgres-host"' in security.text
    assert 'id="postgres-username"' in security.text
    assert 'id="postgres-password"' in security.text
    assert 'id="mcscop-endpoint"' in security.text
    assert 'id="mss-token"' in security.text

    rejected = client.post(
        "/security/secrets/mss",
        data={"token": MSS_TOKEN},
    )
    assert rejected.status_code == 403

    csrf = csrf_from(security.text)
    saved = htmx.post(
        "/security/secrets/mss",
        data={
            "csrf_token": csrf,
            "endpoint": "https://mss.example",
            "token": MSS_TOKEN,
        },
        headers={"HX-Target": "#secret-slot-mss"},
    )
    adapter = as_adapter(saved)
    assert_fragment_body(adapter, contains="secret-slot-mss")
    assert_html_contains(adapter, "Configured")
    assert MSS_TOKEN not in saved.text
    assert "ciphertext" not in saved.text
    assert saved.text.count('id="connection-status-list"') == 1

    deleted = htmx.post(
        "/security/secrets/mss/delete",
        data={"csrf_token": csrf_from(htmx.get("/security").text)},
        headers={"HX-Target": "#secret-slot-mss"},
    )
    assert_fragment_body(as_adapter(deleted), contains="secret-slot-mss")
    assert MSS_TOKEN not in deleted.text
    assert "Configured" not in deleted.text or "Not configured" in deleted.text


def test_postgres_credentials_are_saved_tested_and_available_at_run_boundary(
    client, make_user
) -> None:
    user = make_user("postgres.credentials@example.gov")
    web_login(client, user.email, USER_PASSWORD)
    page = client.get("/security")
    credentials = {
        "host": "warehouse.internal.example",
        "port": "5432",
        "database": "readiness",
        "username": "relay_service",
        "password": "database-password-42!",
        "sslmode": "verify-full",
        "connect_timeout": "10",
        "application_name": "data-mover",
    }

    incomplete = client.post(
        "/security/secrets/postgres",
        data={"csrf_token": csrf_from(page.text), "host": credentials["host"]},
    )
    assert incomplete.status_code == 400
    assert "Port is required" in incomplete.text

    saved = client.post(
        "/security/secrets/postgres",
        data={"csrf_token": csrf_from(page.text), **credentials},
        headers={
            "HX-Request": "true",
            "HX-Target": "secret-slot-postgres",
            "Accept": "text/html",
        },
    )
    assert saved.status_code == 200
    assert "PostgreSQL credentials saved" in saved.text

    with SessionLocal() as db:
        owner = db.get(User, user.id)
        stored = db.scalar(
            select(UserSecret).where(
                UserSecret.user_id == user.id,
                UserSecret.provider == "postgres",
            )
        )
        assert owner is not None and stored is not None
        assert credentials["password"] not in stored.ciphertext
        assert credentials["host"] not in stored.ciphertext
        assert stored.validation_status == "connected"
        assert stored.validated_at is not None
        assert "Emulated connection only" in stored.validation_message
        assert (
            decrypt_user_credentials_for_run(
                db,
                get_settings(),
                user=owner,
                provider="postgres",
            )
            == credentials
        )
        assert (
            decrypt_user_secret_for_run(
                db,
                get_settings(),
                user=owner,
                provider="postgres",
            )
            == credentials["password"]
        )


def test_unchanged_connection_is_not_reencrypted_or_retested(
    client, make_user, monkeypatch
) -> None:
    import app.ui.routes.security as security_routes

    user = make_user("unchanged.credentials@example.gov")
    web_login(client, user.email, USER_PASSWORD)
    credentials = {
        "host": "warehouse.internal.example",
        "port": "5432",
        "database": "readiness",
        "username": "relay_service",
        "password": "database-password-42!",
        "sslmode": "verify-full",
        "connect_timeout": "10",
        "application_name": "data-mover",
    }
    original_test = security_routes.test_user_connection
    calls = 0

    def counted_test(*args, **kwargs):
        nonlocal calls
        calls += 1
        return original_test(*args, **kwargs)

    monkeypatch.setattr(security_routes, "test_user_connection", counted_test)
    page = client.get("/security")
    first = client.post(
        "/security/secrets/postgres",
        data={"csrf_token": csrf_from(page.text), **credentials},
        headers={"HX-Request": "true", "HX-Target": "secret-slot-postgres"},
    )
    assert first.status_code == 200
    assert calls == 1
    with SessionLocal() as db:
        stored = db.scalar(
            select(UserSecret).where(
                UserSecret.user_id == user.id,
                UserSecret.provider == "postgres",
            )
        )
        assert stored is not None
        original_ciphertext = stored.ciphertext
        original_updated_at = stored.updated_at
        original_validated_at = stored.validated_at

    unchanged = client.post(
        "/security/secrets/postgres",
        data={"csrf_token": csrf_from(page.text), **credentials},
        headers={"HX-Request": "true", "HX-Target": "secret-slot-postgres"},
    )
    assert unchanged.status_code == 200
    assert "No credential changes detected" in unchanged.text
    assert calls == 1
    with SessionLocal() as db:
        stored = db.scalar(
            select(UserSecret).where(
                UserSecret.user_id == user.id,
                UserSecret.provider == "postgres",
            )
        )
        assert stored is not None
        assert stored.ciphertext == original_ciphertext
        assert stored.updated_at == original_updated_at
        assert stored.validated_at == original_validated_at

    changed = client.post(
        "/security/secrets/postgres",
        data={
            "csrf_token": csrf_from(page.text),
            **credentials,
            "password": "replacement-database-password-43!",
        },
        headers={"HX-Request": "true", "HX-Target": "secret-slot-postgres"},
    )
    assert changed.status_code == 200
    assert calls == 2
    with SessionLocal() as db:
        stored = db.scalar(
            select(UserSecret).where(
                UserSecret.user_id == user.id,
                UserSecret.provider == "postgres",
            )
        )
        assert stored is not None
        assert stored.ciphertext != original_ciphertext
        assert stored.validation_status == "connected"


def test_mss_connection_without_dataset_remains_untested(client, make_user) -> None:
    user = make_user("mss.cluster@example.gov")
    web_login(client, user.email, USER_PASSWORD)
    page = client.get("/security")
    saved = client.post(
        "/security/secrets/mss",
        data={
            "csrf_token": csrf_from(page.text),
            "endpoint": "https://mss.example",
            "token": MSS_TOKEN,
        },
    )
    assert saved.status_code == 303

    status_page = client.get("/security")
    assert "Connection status" in status_page.text
    assert "Palantir Foundry" in status_page.text
    assert "Wake cluster" not in status_page.text

    retested = client.post(
        "/security/secrets/mss/test",
        data={"csrf_token": csrf_from(status_page.text)},
        headers={
            "HX-Request": "true",
            "HX-Target": "connection-status-list",
            "Accept": "text/html",
        },
    )
    assert retested.status_code == 200
    assert "Needs setup" in retested.text
    assert "default dataset RID" in retested.text
    assert 'id="security-activity"' not in retested.text

    with SessionLocal() as db:
        stored = db.scalar(
            select(UserSecret).where(
                UserSecret.user_id == user.id,
                UserSecret.provider == "mss",
            )
        )
        assert stored is not None
        assert stored.validation_status == "untested"
        assert stored.validated_at is not None


def test_databricks_startup_history_is_owner_scoped(client, make_user) -> None:
    owner = make_user("databricks.audit.owner@example.gov")
    other = make_user("databricks.audit.other@example.gov")
    now = utcnow()
    with SessionLocal() as db:
        active_attempt = DatabricksComputeAttempt(
            user_id=owner.id,
            workspace_id="owner-workspace",
            compute_key="d" * 64,
            active_key="d" * 64,
            compute_kind="warehouse",
            compute_id="active-owner-warehouse",
            connection_generation="owner-generation",
            status="starting",
            last_state="STARTING",
            poll_count=1,
            requested_at=now,
            deadline_at=now,
            next_poll_at=now,
        )
        db.add_all(
            [
                DatabricksComputeAttempt(
                    user_id=owner.id,
                    workspace_id="owner-workspace",
                    compute_key="a" * 64,
                    compute_kind="warehouse",
                    compute_id="owner-warehouse-id",
                    connection_generation="owner-generation",
                    status="failed",
                    last_state="STOPPED",
                    poll_count=3,
                    requested_at=now,
                    deadline_at=now,
                    next_poll_at=now,
                    error_code="compute_start_failed",
                ),
                DatabricksComputeAttempt(
                    user_id=other.id,
                    workspace_id="other-workspace",
                    compute_key="b" * 64,
                    compute_kind="classic_cluster",
                    compute_id="other-private-cluster-id",
                    connection_generation="other-generation",
                    status="ready",
                    last_state="RUNNING",
                    poll_count=2,
                    requested_at=now,
                    deadline_at=now,
                    next_poll_at=now,
                ),
                active_attempt,
            ]
        )
        db.commit()
        active_attempt_id = active_attempt.id

    web_login(client, owner.email, USER_PASSWORD)
    response = client.get("/security")

    assert response.status_code == 200
    assert "Databricks startup history" in response.text
    assert "owner-warehouse-id" in response.text
    assert "other-private-cluster-id" not in response.text
    assert "Stop readiness checks" in response.text

    stopped = client.post(
        f"/security/secrets/databricks/start/{active_attempt_id}/cancel",
        data={"csrf_token": csrf_from(response.text)},
        headers={
            "HX-Request": "true",
            "HX-Target": "connection-status-list",
            "Accept": "text/html",
        },
    )
    assert stopped.status_code == 200
    assert "Databricks compute was not stopped" in stopped.text
    assert "Resume readiness checks" in stopped.text
    assert "other-private-cluster-id" not in stopped.text
    with SessionLocal() as db:
        saved = db.get(DatabricksComputeAttempt, active_attempt_id)
        assert saved is not None
        assert saved.status == "cancelled"


def test_databricks_startup_consent_requires_contract_and_resets_on_compute_change(
    make_user,
) -> None:
    user = make_user("databricks.startup-consent@example.gov")
    settings = get_settings().model_copy(
        update={
            "pipeline_enable_databricks": True,
            "pipeline_databricks_startup_contract_verified": False,
            "pipeline_allowed_https_hosts": "dbc-12345678.cloud.databricks.com",
        }
    )
    credentials = {
        "workspace_url": "https://dbc-12345678.cloud.databricks.com",
        "token": "synthetic-api-token",
        "compute_kind": "warehouse",
        "compute_id": "warehouse-1",
        "http_path": "sql/1.0/warehouses/warehouse-1",
        "startup_policy": "allow_start",
    }

    with SessionLocal() as db:
        with pytest.raises(ValueError, match="provider contract is accepted"):
            store_user_credentials(
                db,
                settings,
                user=user,
                provider="databricks",
                credentials=credentials,
            )

        approved = settings.model_copy(
            update={"pipeline_databricks_startup_contract_verified": True}
        )
        store_user_credentials(
            db,
            approved,
            user=user,
            provider="databricks",
            credentials=credentials,
        )
        saved = decrypt_user_credentials_for_run(db, approved, user=user, provider="databricks")
        assert saved["startup_policy"] == "allow_start"

        changed = {
            **credentials,
            "compute_id": "warehouse-2",
            "http_path": "sql/1.0/warehouses/warehouse-2",
        }
        store_user_credentials(
            db,
            approved,
            user=user,
            provider="databricks",
            credentials=changed,
        )
        saved = decrypt_user_credentials_for_run(db, approved, user=user, provider="databricks")
        assert saved["startup_policy"] == "manual"

        reconsented = {**changed, "startup_policy": "allow_start"}
        store_user_credentials(
            db,
            approved,
            user=user,
            provider="databricks",
            credentials=reconsented,
        )
        rotated = {**reconsented, "token": "rotated-synthetic-api-token"}
        store_user_credentials(
            db,
            approved,
            user=user,
            provider="databricks",
            credentials=rotated,
        )
        saved = decrypt_user_credentials_for_run(db, approved, user=user, provider="databricks")
        assert saved["startup_policy"] == "manual"


def test_native_connection_test_failure_stays_visible_with_status_and_reference(
    client, make_user, monkeypatch
) -> None:
    user = make_user("native.connection.failure@example.gov")
    web_login(client, user.email, USER_PASSWORD)
    page = client.get("/security")
    client.post(
        "/security/secrets/mss",
        data={
            "csrf_token": csrf_from(page.text),
            "endpoint": "https://mss.example",
            "token": MSS_TOKEN,
        },
    )

    def fail_connection(*args, **kwargs):
        raise SecretStorageError("credential storage is unavailable")

    monkeypatch.setattr("app.ui.routes.security.test_user_connection", fail_connection)
    response = client.post(
        "/security/secrets/mss/test",
        data={"csrf_token": csrf_from(client.get("/security").text)},
    )

    assert response.status_code == 503
    assert "connection test could not be completed" in response.text.casefold()
    assert "ref-" in response.text


def test_mcscop_credentials_are_saved_tested_and_available_at_run_boundary(
    client, make_user
) -> None:
    user = make_user("mcscop.credentials@example.gov")
    web_login(client, user.email, USER_PASSWORD)
    page = client.get("/security")
    credentials = {
        "endpoint": "https://mcscop.example",
        "token": "mcscop-secret-token-value-123456",
        "dataset_rid": "ri.foundry.main.dataset.demo-destination",
        "branch": "master",
        "ca_profile": "system",
    }

    saved = client.post(
        "/security/secrets/mcscop",
        data={"csrf_token": csrf_from(page.text), **credentials},
        headers={
            "HX-Request": "true",
            "HX-Target": "secret-slot-mcscop",
            "Accept": "text/html",
        },
    )
    assert saved.status_code == 200
    assert "MCS-COP credentials saved" in saved.text

    with SessionLocal() as db:
        owner = db.get(User, user.id)
        stored = db.scalar(
            select(UserSecret).where(
                UserSecret.user_id == user.id,
                UserSecret.provider == "mcscop",
            )
        )
        assert owner is not None and stored is not None
        assert all(value not in stored.ciphertext for value in credentials.values())
        assert stored.validation_status == "connected"
        assert stored.validated_at is not None
        assert (
            decrypt_user_credentials_for_run(
                db,
                get_settings(),
                user=owner,
                provider="mcscop",
            )
            == credentials
        )
        assert (
            decrypt_user_secret_for_run(
                db,
                get_settings(),
                user=owner,
                provider="mcscop",
            )
            == credentials["token"]
        )


def test_secret_is_encrypted_replaceable_and_wrap_limited(client, make_user) -> None:
    user = make_user("secrets.crypto@example.gov")
    settings = get_settings()

    with SessionLocal() as db:
        owner = db.get(User, user.id)
        assert owner is not None
        store_user_credentials(
            db,
            settings,
            user=owner,
            provider="mss",
            credentials={"endpoint": "https://mss.example", "token": MSS_TOKEN},
            request=None,
        )
        stored = db.scalar(
            select(UserSecret).where(UserSecret.user_id == user.id, UserSecret.provider == "mss")
        )
        assert stored is not None
        assert MSS_TOKEN not in stored.ciphertext
        assert decrypt_user_secret_for_run(db, settings, user=owner, provider="mss") == MSS_TOKEN

    original_limit = settings.api_token_max_wraps_per_key
    settings.api_token_max_wraps_per_key = 1
    try:
        with SessionLocal() as db:
            owner = db.get(User, user.id)
            assert owner is not None
            with pytest.raises(SecretStorageError, match="rotate"):
                store_user_credentials(
                    db,
                    settings,
                    user=owner,
                    provider="mss",
                    credentials={
                        "endpoint": "https://mss.example",
                        "token": "replacement-token-value-999",
                    },
                    request=None,
                )
            usage = db.get(ApiTokenKeyUsage, settings.api_token_active_key_id)
            assert usage is not None and usage.wrap_count == 1
            assert (
                decrypt_user_secret_for_run(db, settings, user=owner, provider="mss") == MSS_TOKEN
            )
    finally:
        settings.api_token_max_wraps_per_key = original_limit


def test_secret_validation_ownership_and_tampering(client, make_user) -> None:
    first = make_user("first.secret@example.gov")
    second = make_user("second.secret@example.gov")
    web_login(client, first.email, USER_PASSWORD)
    csrf = csrf_from(client.get("/security").text)

    whitespace = client.post(
        "/security/secrets/mss",
        data={
            "csrf_token": csrf,
            "endpoint": "https://mss.example",
            "token": " token-with-whitespace ",
        },
    )
    assert whitespace.status_code == 400
    assert "API tokens must contain" in whitespace.text

    unknown = client.post(
        "/security/secrets/custom",
        data={"csrf_token": csrf, "token": "custom-secret-token"},
    )
    assert unknown.status_code == 422

    saved = client.post(
        "/security/secrets/mss",
        data={
            "csrf_token": csrf,
            "endpoint": "https://mss.example",
            "token": "first-user-mss-token",
        },
    )
    assert saved.status_code == 303

    client.cookies.clear()
    web_login(client, second.email, USER_PASSWORD)
    with SessionLocal() as db:
        assert (
            db.scalar(
                select(func.count()).select_from(UserSecret).where(UserSecret.user_id == second.id)
            )
            == 0
        )
        owner = db.get(User, first.id)
        other = db.get(User, second.id)
        stored = db.scalar(
            select(UserSecret).where(UserSecret.user_id == first.id, UserSecret.provider == "mss")
        )
        assert owner is not None and other is not None and stored is not None
        settings = get_settings()
        with pytest.raises(SecretStorageError):
            decrypt_user_secret_for_run(db, settings, user=other, provider="mss")
        stored.ciphertext = stored.ciphertext[:-4] + "dead"
        db.commit()
        with pytest.raises(SecretStorageError):
            decrypt_user_secret_for_run(db, settings, user=owner, provider="mss")
