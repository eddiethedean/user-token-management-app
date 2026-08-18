"""Development-only fake connection seeding for the explorable demo."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import User, UserSecret
from app.services.secrets import store_user_credentials, test_user_connection

DEMO_CONNECTION_CREDENTIALS: dict[str, dict[str, str]] = {
    "mss": {
        "endpoint": "https://mss.demo.invalid",
        "token": "fake-mss-token-for-demo-only",
        "dataset_rid": "ri.foundry.main.dataset.demo-operations",
        "branch": "master",
        "ca_profile": "system",
    },
    "mcscop": {
        "endpoint": "https://mcscop.demo.invalid",
        "token": "fake-mcscop-token-for-demo-only",
        "dataset_rid": "ri.foundry.main.dataset.demo-destination",
        "branch": "master",
        "ca_profile": "system",
    },
    "postgres": {
        "host": "postgres.demo.invalid",
        "port": "5432",
        "database": "readiness_demo",
        "username": "data_mover_demo",
        "password": "fake-postgres-password-for-demo-only",
        "sslmode": "require",
        "connect_timeout": "10",
        "application_name": "data-mover",
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
    from app.connectors.registry import load_builtin_connectors

    load_builtin_connectors(demo=settings.is_demo_mode)
    if settings.is_production:
        raise ValueError("Fake demo credentials cannot be seeded in production.")
    if not settings.is_demo_mode:
        raise ValueError("Fake demo credentials can only be seeded in demo mode.")

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
        test_user_connection(db, settings=settings, user=user, provider=provider)
        seeded.append(provider)

    return DemoConnectionSeedResult(tuple(seeded), tuple(skipped))
