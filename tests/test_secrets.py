"""API-token secret storage and HTMX UI coverage for Hedron."""

from __future__ import annotations

import pytest
from hedron.testing import assert_fragment_body, assert_html_contains
from sqlalchemy import func, select

from app.config import get_settings
from app.database import SessionLocal
from app.models import ApiTokenKeyUsage, User, UserSecret
from app.services.secrets import (
    SecretStorageError,
    decrypt_user_credentials_for_run,
    decrypt_user_secret_for_run,
    store_user_secret,
)
from tests.helpers import (
    ADVANA_TOKEN,
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
    assert all(label in security.text for label in ("Advana", "MSS", "PostgreSQL", "MongoDB"))
    assert "Analytics API" not in security.text
    assert 'id="postgres-host"' in security.text
    assert 'id="postgres-username"' in security.text
    assert 'id="postgres-password"' in security.text
    assert 'id="mongodb-host"' in security.text
    assert 'id="mongodb-username"' in security.text
    assert 'id="mongodb-auth_database"' in security.text
    assert 'id="mongodb-tlsmode"' in security.text

    rejected = client.post(
        "/security/secrets/advana",
        data={"token": ADVANA_TOKEN},
    )
    assert rejected.status_code == 403

    csrf = csrf_from(security.text)
    saved = htmx.post(
        "/security/secrets/advana",
        data={"csrf_token": csrf, "token": ADVANA_TOKEN},
        headers={"HX-Target": "#secret-slot-advana"},
    )
    adapter = as_adapter(saved)
    assert_fragment_body(adapter, contains="secret-slot-advana")
    assert_html_contains(adapter, "Configured")
    assert ADVANA_TOKEN not in saved.text
    assert "ciphertext" not in saved.text

    deleted = htmx.post(
        "/security/secrets/advana/delete",
        data={"csrf_token": csrf_from(htmx.get("/security").text)},
        headers={"HX-Target": "#secret-slot-advana"},
    )
    assert_fragment_body(as_adapter(deleted), contains="secret-slot-advana")
    assert ADVANA_TOKEN not in deleted.text
    assert "Configured" not in deleted.text or "Not configured" in deleted.text


def test_postgres_credentials_are_validated_encrypted_and_available_at_run_boundary(
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
        assert all(value not in stored.ciphertext for value in credentials.values())
        assert stored.validation_status == "connected"
        assert stored.validated_at is not None
        assert "PostgreSQL 16 handshake succeeded" in stored.validation_message
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


def test_advana_connection_can_wake_its_databricks_cluster(client, make_user) -> None:
    user = make_user("databricks.cluster@example.gov")
    web_login(client, user.email, USER_PASSWORD)
    page = client.get("/security")
    saved = client.post(
        "/security/secrets/advana",
        data={"csrf_token": csrf_from(page.text), "token": ADVANA_TOKEN},
    )
    assert saved.status_code == 303

    status_page = client.get("/security")
    assert "Connection status" in status_page.text
    assert "Databricks" in status_page.text
    assert "Wake cluster" in status_page.text

    awakened = client.post(
        "/security/secrets/advana/wake",
        data={"csrf_token": csrf_from(status_page.text)},
        headers={
            "HX-Request": "true",
            "HX-Target": "connection-status-list",
            "Accept": "text/html",
        },
    )
    assert awakened.status_code == 200
    assert "Cluster running" in awakened.text

    retested = client.post(
        "/security/secrets/advana/test",
        data={"csrf_token": csrf_from(status_page.text)},
        headers={
            "HX-Request": "true",
            "HX-Target": "connection-status-list",
            "Accept": "text/html",
        },
    )
    assert retested.status_code == 200
    assert "Databricks handshake succeeded" in retested.text

    with SessionLocal() as db:
        stored = db.scalar(
            select(UserSecret).where(
                UserSecret.user_id == user.id,
                UserSecret.provider == "advana",
            )
        )
        assert stored is not None
        assert stored.runtime_status == "running"
        assert stored.runtime_updated_at is not None


def test_mongodb_credentials_are_validated_encrypted_and_available_at_run_boundary(
    client, make_user
) -> None:
    user = make_user("mongodb.credentials@example.gov")
    web_login(client, user.email, USER_PASSWORD)
    page = client.get("/security")
    credentials = {
        "host": "documents.internal.example",
        "port": "27017",
        "database": "operations",
        "username": "relay_documents",
        "password": "mongodb-password-42!",
        "auth_database": "admin",
        "tlsmode": "require",
    }

    saved = client.post(
        "/security/secrets/mongodb",
        data={"csrf_token": csrf_from(page.text), **credentials},
        headers={
            "HX-Request": "true",
            "HX-Target": "secret-slot-mongodb",
            "Accept": "text/html",
        },
    )
    assert saved.status_code == 200
    assert "MongoDB credentials saved" in saved.text

    with SessionLocal() as db:
        owner = db.get(User, user.id)
        stored = db.scalar(
            select(UserSecret).where(
                UserSecret.user_id == user.id,
                UserSecret.provider == "mongodb",
            )
        )
        assert owner is not None and stored is not None
        assert all(value not in stored.ciphertext for value in credentials.values())
        assert stored.validation_status == "connected"
        assert "MongoDB 8 handshake succeeded" in stored.validation_message
        assert (
            decrypt_user_credentials_for_run(
                db,
                get_settings(),
                user=owner,
                provider="mongodb",
            )
            == credentials
        )
        assert (
            decrypt_user_secret_for_run(
                db,
                get_settings(),
                user=owner,
                provider="mongodb",
            )
            == credentials["password"]
        )


def test_secret_is_encrypted_replaceable_and_wrap_limited(client, make_user) -> None:
    user = make_user("secrets.crypto@example.gov")
    settings = get_settings()

    with SessionLocal() as db:
        owner = db.get(User, user.id)
        assert owner is not None
        store_user_secret(
            db, settings, user=owner, provider="advana", token=ADVANA_TOKEN, request=None
        )
        stored = db.scalar(
            select(UserSecret).where(UserSecret.user_id == user.id, UserSecret.provider == "advana")
        )
        assert stored is not None
        assert ADVANA_TOKEN not in stored.ciphertext
        assert (
            decrypt_user_secret_for_run(db, settings, user=owner, provider="advana") == ADVANA_TOKEN
        )

    original_limit = settings.api_token_max_wraps_per_key
    settings.api_token_max_wraps_per_key = 1
    try:
        with SessionLocal() as db:
            owner = db.get(User, user.id)
            assert owner is not None
            with pytest.raises(SecretStorageError, match="rotate"):
                store_user_secret(
                    db,
                    settings,
                    user=owner,
                    provider="advana",
                    token="replacement-token-value-999",
                    request=None,
                )
            usage = db.get(ApiTokenKeyUsage, settings.api_token_active_key_id)
            assert usage is not None and usage.wrap_count == 1
            assert (
                decrypt_user_secret_for_run(db, settings, user=owner, provider="advana")
                == ADVANA_TOKEN
            )
    finally:
        settings.api_token_max_wraps_per_key = original_limit


def test_secret_validation_ownership_and_tampering(client, make_user) -> None:
    first = make_user("first.secret@example.gov")
    second = make_user("second.secret@example.gov")
    web_login(client, first.email, USER_PASSWORD)
    csrf = csrf_from(client.get("/security").text)

    whitespace = client.post(
        "/security/secrets/advana",
        data={"csrf_token": csrf, "token": " token-with-whitespace "},
    )
    assert whitespace.status_code == 400

    unknown = client.post(
        "/security/secrets/custom",
        data={"csrf_token": csrf, "token": "custom-secret-token"},
    )
    assert unknown.status_code == 422

    saved = client.post(
        "/security/secrets/mss",
        data={"csrf_token": csrf, "token": "first-user-mss-token"},
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
