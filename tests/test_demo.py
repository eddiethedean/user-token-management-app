"""Development demo connection seeding coverage."""

from __future__ import annotations

from sqlalchemy import select

from app.config import get_settings
from app.database import SessionLocal
from app.models import User, UserSecret
from app.services.demo import DEMO_CONNECTION_CREDENTIALS, seed_demo_connections


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
    assert set(second.skipped) == set(DEMO_CONNECTION_CREDENTIALS)
    assert {secret.provider for secret in stored} == set(DEMO_CONNECTION_CREDENTIALS)
    assert all(secret.validation_status == "connected" for secret in stored)
    assert (
        next(secret for secret in stored if secret.provider == "advana").runtime_status == "running"
    )
    assert all("fake-" not in secret.ciphertext for secret in stored)
