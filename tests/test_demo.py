"""Development demo connection seeding coverage."""

from __future__ import annotations

from sqlalchemy import select

from app.config import get_settings
from app.database import SessionLocal
from app.models import User, UserSecret
from app.services.demo import (
    DEMO_CONNECTION_CREDENTIALS,
    LEGACY_DEMO_CONNECTION_CREDENTIALS,
    seed_demo_connections,
)
from app.services.secret_crypto import CredentialEnvelope
from app.services.secrets import store_user_credentials


def test_demo_connection_seed_is_fake_connected_and_idempotent(access_app) -> None:
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.email == "admin@example.gov"))
        assert user is not None

        first = seed_demo_connections(db, get_settings(), user=user)
        second = seed_demo_connections(db, get_settings(), user=user)
        stored = list(
            db.scalars(
                select(UserSecret)
                .where(UserSecret.user_id == user.id)
                .order_by(UserSecret.provider)
            ).all()
        )

    assert set(first.seeded) == set(DEMO_CONNECTION_CREDENTIALS)
    assert first.skipped == ()
    assert second.seeded == ()
    assert second.refreshed == ()
    assert second.revalidated == ()
    assert set(second.skipped) == set(DEMO_CONNECTION_CREDENTIALS)
    assert {secret.provider for secret in stored} == set(DEMO_CONNECTION_CREDENTIALS)
    assert all(secret.validation_status == "connected" for secret in stored)
    assert all("fake-" not in secret.ciphertext for secret in stored)


def test_demo_seed_repairs_legacy_fake_credentials_without_replacing_real_values(
    access_app,
) -> None:
    settings = get_settings()
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.email == "admin@example.gov"))
        assert user is not None
        legacy_mss = LEGACY_DEMO_CONNECTION_CREDENTIALS["mss"][0]
        stored = store_user_credentials(
            db,
            settings,
            user=user,
            provider="mss",
            credentials=DEMO_CONNECTION_CREDENTIALS["mss"],
        )
        envelope = CredentialEnvelope.encrypt(
            settings,
            user_id=user.id,
            secret_id=stored.id,
            provider="mss",
            plaintext=CredentialEnvelope.serialize(legacy_mss),
            key_id=settings.api_token_active_key_id,
        )
        stored.ciphertext = envelope.ciphertext
        stored.nonce = envelope.nonce
        stored.encrypted_data_key = envelope.encrypted_data_key
        stored.key_nonce = envelope.key_nonce
        stored.master_key_id = envelope.master_key_id
        stored.validation_status = "connected"
        db.commit()

        result = seed_demo_connections(db, settings, user=user)
        repaired = db.scalar(
            select(UserSecret).where(
                UserSecret.user_id == user.id,
                UserSecret.provider == "mss",
            )
        )
        assert repaired is not None
        assert CredentialEnvelope.decrypt(settings, repaired) == DEMO_CONNECTION_CREDENTIALS["mss"]
        assert repaired.validation_status == "connected"

        real_credentials = {
            "endpoint": "https://mss.example.gov",
            "token": "real-user-token",
            "dataset_rid": "ri.foundry.main.dataset.real",
            "branch": "master",
            "ca_profile": "system",
        }
        store_user_credentials(
            db,
            settings,
            user=user,
            provider="mss",
            credentials=real_credentials,
        )
        preserved = seed_demo_connections(db, settings, user=user)
        current = db.scalar(
            select(UserSecret).where(
                UserSecret.user_id == user.id,
                UserSecret.provider == "mss",
            )
        )
        assert current is not None
        assert CredentialEnvelope.decrypt(settings, current) == real_credentials

    assert "mss" in result.refreshed
    assert "mss" in preserved.skipped


def test_demo_seed_revalidates_current_fake_credentials_without_reencrypting(access_app) -> None:
    settings = get_settings()
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.email == "admin@example.gov"))
        assert user is not None
        seed_demo_connections(db, settings, user=user)
        stored = db.scalar(
            select(UserSecret).where(
                UserSecret.user_id == user.id,
                UserSecret.provider == "mss",
            )
        )
        assert stored is not None
        ciphertext = stored.ciphertext
        stored.validation_status = "untested"
        db.commit()

        result = seed_demo_connections(db, settings, user=user)
        revalidated = db.scalar(
            select(UserSecret).where(
                UserSecret.user_id == user.id,
                UserSecret.provider == "mss",
            )
        )
        assert revalidated is not None
        assert revalidated.ciphertext == ciphertext
        assert revalidated.validation_status == "connected"

    assert result.revalidated == ("mss",)
