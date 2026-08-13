"""Development-only fake connection seeding for the explorable demo."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import User, UserSecret
from app.services.secrets import (
    store_user_credentials,
    wake_provider_runtime,
)

DEMO_CONNECTION_CREDENTIALS: dict[str, dict[str, str]] = {
    "advana": {
        "endpoint": "https://advana.demo.invalid",
        "username": "data_mover_demo",
        "token": "fake-advana-token-for-demo-only",
    },
    "mss": {
        "endpoint": "https://mss.demo.invalid",
        "username": "data_mover_demo",
        "token": "fake-mss-token-for-demo-only",
    },
    "postgres": {
        "host": "postgres.demo.invalid",
        "port": "5432",
        "database": "readiness_demo",
        "username": "data_mover_demo",
        "password": "fake-postgres-password-for-demo-only",
        "sslmode": "require",
    },
    "mongodb": {
        "host": "mongodb.demo.invalid",
        "port": "27017",
        "database": "operations_demo",
        "username": "data_mover_demo",
        "password": "fake-mongodb-password-for-demo-only",
        "auth_database": "admin",
        "tlsmode": "require",
    },
}


@dataclass(frozen=True)
class DemoConnectionSeedResult:
    seeded: tuple[str, ...]
    skipped: tuple[str, ...]


def seed_demo_connections(
    db: Session,
    settings: Settings,
    *,
    user: User,
    replace: bool = False,
) -> DemoConnectionSeedResult:
    """Store fake provider bundles without overwriting existing credentials by default."""
    if settings.is_production:
        raise ValueError("Fake demo credentials cannot be seeded in production.")

    existing = set(
        db.scalars(select(UserSecret.provider).where(UserSecret.user_id == user.id)).all()
    )
    seeded: list[str] = []
    skipped: list[str] = []
    for provider, credentials in DEMO_CONNECTION_CREDENTIALS.items():
        if provider in existing and not replace:
            skipped.append(provider)
            continue
        store_user_credentials(
            db,
            settings,
            user=user,
            provider=provider,
            credentials=credentials,
        )
        seeded.append(provider)

    if "advana" in seeded:
        wake_provider_runtime(db, user=user, provider="advana")
    return DemoConnectionSeedResult(tuple(seeded), tuple(skipped))
