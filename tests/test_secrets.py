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
    assert all(label in security.text for label in ("Advana", "ADE", "MSS"))

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
        "/security/secrets/ade",
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
