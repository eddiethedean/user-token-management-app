import re

import pytest
from sqlalchemy import func, select

from app.config import get_settings
from app.database import SessionLocal
from app.models import ApiTokenKeyUsage, AuditEvent, User, UserSecret
from app.services.secrets import SecretStorageError, decrypt_user_secret_for_run

ADMIN_EMAIL = "admin@example.gov"
ADMIN_PASSWORD = "River-Lantern-94!Blue"
ADVANA_TOKEN = "advana-secret-token-value-123456"


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def api_login(client, email: str, password: str) -> str:
    response = client.post("/api/v1/auth/token", json={"email": email, "password": password})
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


def csrf_from(html: str) -> str:
    match = re.search(r'name="csrf_token" value="([^"]+)"', html)
    assert match is not None
    return match.group(1)


def test_api_exposes_only_three_non_revealable_secret_slots(client) -> None:
    access_token = api_login(client, ADMIN_EMAIL, ADMIN_PASSWORD)
    headers = bearer(access_token)

    empty = client.get("/api/v1/me/secrets", headers=headers)
    assert empty.status_code == 200
    assert empty.headers["cache-control"] == "no-store"
    assert [(item["provider"], item["environment_variable"]) for item in empty.json()] == [
        ("advana", "ADVANA_API_TOKEN"),
        ("ade", "ADE_API_TOKEN"),
        ("mss", "MSS_API_TOKEN"),
    ]
    assert all(not item["configured"] for item in empty.json())

    saved = client.put("/api/v1/me/secrets/advana", json={"token": ADVANA_TOKEN}, headers=headers)
    assert saved.status_code == 200
    assert saved.headers["cache-control"] == "no-store"
    assert saved.json()["configured"] is True
    assert "token" not in saved.json()
    assert ADVANA_TOKEN not in saved.text

    listed = client.get("/api/v1/me/secrets", headers=headers)
    assert listed.json()[0]["configured"] is True
    assert ADVANA_TOKEN not in listed.text
    assert "ciphertext" not in listed.text


def test_secret_is_encrypted_replaceable_decryptable_only_at_run_boundary(client) -> None:
    access_token = api_login(client, ADMIN_EMAIL, ADMIN_PASSWORD)
    headers = bearer(access_token)
    assert (
        client.put(
            "/api/v1/me/secrets/advana", json={"token": ADVANA_TOKEN}, headers=headers
        ).status_code
        == 200
    )

    with SessionLocal() as db:
        stored = db.scalar(select(UserSecret).where(UserSecret.provider == "advana"))
        user = db.scalar(select(User).where(User.email == ADMIN_EMAIL))
        assert stored is not None and user is not None
        serialized = "|".join(
            (
                stored.ciphertext,
                stored.nonce,
                stored.encrypted_data_key,
                stored.key_nonce,
            )
        )
        assert ADVANA_TOKEN not in serialized
        assert stored.master_key_id == "test-v1"
        usage = db.get(ApiTokenKeyUsage, "test-v1")
        assert usage is not None and usage.wrap_count == 1
        assert (
            decrypt_user_secret_for_run(db, get_settings(), user=user, provider="advana")
            == ADVANA_TOKEN
        )
        assert stored.last_used_at is not None

    replacement = "replacement-advana-token-987654"
    assert (
        client.put(
            "/api/v1/me/secrets/advana", json={"token": replacement}, headers=headers
        ).status_code
        == 200
    )
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.email == ADMIN_EMAIL))
        assert user is not None
        assert db.scalar(select(func.count()).select_from(UserSecret)) == 1
        usage = db.get(ApiTokenKeyUsage, "test-v1")
        assert usage is not None and usage.wrap_count == 2
        assert (
            decrypt_user_secret_for_run(db, get_settings(), user=user, provider="advana")
            == replacement
        )
        event_types = db.scalars(
            select(AuditEvent.event_type).where(AuditEvent.event_type.like("api_token.%"))
        ).all()
        assert "api_token.created" in event_types
        assert "api_token.replaced" in event_types
        assert "api_token.used" in event_types


def test_master_key_wrap_limit_fails_closed_without_replacing_ciphertext(client) -> None:
    settings = get_settings()
    original_limit = settings.api_token_max_wraps_per_key
    access_token = api_login(client, ADMIN_EMAIL, ADMIN_PASSWORD)
    headers = bearer(access_token)
    try:
        settings.api_token_max_wraps_per_key = 1
        first = client.put(
            "/api/v1/me/secrets/advana", json={"token": ADVANA_TOKEN}, headers=headers
        )
        assert first.status_code == 200
        blocked = client.put(
            "/api/v1/me/secrets/advana",
            json={"token": "replacement-that-must-not-commit"},
            headers=headers,
        )
        assert blocked.status_code == 503
        assert "rotate the key" in blocked.json()["detail"]
        with SessionLocal() as db:
            user = db.scalar(select(User).where(User.email == ADMIN_EMAIL))
            usage = db.get(ApiTokenKeyUsage, "test-v1")
            assert user is not None and usage is not None and usage.wrap_count == 1
            assert (
                decrypt_user_secret_for_run(db, settings, user=user, provider="advana")
                == ADVANA_TOKEN
            )
    finally:
        settings.api_token_max_wraps_per_key = original_limit


def test_secret_ownership_provider_allowlist_validation_and_deletion(client, make_user) -> None:
    first = make_user("first.user@example.gov")
    second = make_user("second.user@example.gov")
    first_token = api_login(client, first.email, "Aspen-Compass-64!River")
    second_token = api_login(client, second.email, "Aspen-Compass-64!River")

    unsupported = client.put(
        "/api/v1/me/secrets/custom",
        json={"token": "custom-secret-token"},
        headers=bearer(first_token),
    )
    assert unsupported.status_code == 400
    whitespace = client.put(
        "/api/v1/me/secrets/ade",
        json={"token": " token-with-whitespace "},
        headers=bearer(first_token),
    )
    assert whitespace.status_code == 400

    assert (
        client.put(
            "/api/v1/me/secrets/mss",
            json={"token": "first-user-mss-token"},
            headers=bearer(first_token),
        ).status_code
        == 200
    )
    second_slots = client.get("/api/v1/me/secrets", headers=bearer(second_token)).json()
    assert all(not item["configured"] for item in second_slots)

    deleted = client.delete("/api/v1/me/secrets/mss", headers=bearer(first_token))
    assert deleted.status_code == 204
    assert deleted.headers["cache-control"] == "no-store"
    assert client.delete("/api/v1/me/secrets/mss", headers=bearer(first_token)).status_code == 404
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(UserSecret)) == 0


def test_cookie_secret_changes_require_csrf_and_web_ui_never_reveals_token(client) -> None:
    api_login(client, ADMIN_EMAIL, ADMIN_PASSWORD)
    rejected = client.put("/api/v1/me/secrets/advana", json={"token": ADVANA_TOKEN})
    assert rejected.status_code == 403

    security = client.get("/security")
    assert security.status_code == 200
    assert security.headers["cache-control"] == "no-store"
    assert all(label in security.text for label in ("Advana", "ADE", "MSS"))
    csrf = csrf_from(security.text)
    saved = client.post(
        "/security/secrets/advana",
        data={"csrf_token": csrf, "token": ADVANA_TOKEN},
        headers={"HX-Request": "true"},
    )
    assert saved.status_code == 200
    assert "Configured" in saved.text
    assert "cannot be revealed" in saved.text
    assert ADVANA_TOKEN not in saved.text

    refreshed = client.get("/security")
    assert ADVANA_TOKEN not in refreshed.text
    deleted = client.post(
        "/security/secrets/advana/delete",
        data={"csrf_token": csrf},
        headers={"HX-Request": "true"},
    )
    assert deleted.status_code == 200
    assert "Not configured" in deleted.text


def test_ciphertext_tampering_and_wrong_owner_context_are_rejected(client, make_user) -> None:
    other = make_user("context.user@example.gov")
    access_token = api_login(client, ADMIN_EMAIL, ADMIN_PASSWORD)
    assert (
        client.put(
            "/api/v1/me/secrets/ade",
            json={"token": "ade-secret-token-value"},
            headers=bearer(access_token),
        ).status_code
        == 200
    )

    with SessionLocal() as db:
        owner = db.scalar(select(User).where(User.email == ADMIN_EMAIL))
        other_user = db.get(User, other.id)
        stored = db.scalar(select(UserSecret).where(UserSecret.provider == "ade"))
        assert owner is not None and other_user is not None and stored is not None
        with pytest.raises(SecretStorageError, match="not configured"):
            decrypt_user_secret_for_run(db, get_settings(), user=other_user, provider="ade")
        stored.ciphertext = stored.ciphertext[:-2] + "AA"
        db.commit()
        with pytest.raises(SecretStorageError, match="could not be decrypted"):
            decrypt_user_secret_for_run(db, get_settings(), user=owner, provider="ade")
