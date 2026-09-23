"""Development-only fake connection seeding for the explorable demo."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.connectors.registry import ConnectorRegistry
from app.models import FoundryDataset, User, UserSecret
from app.services.secret_crypto import CredentialEnvelope, CredentialEnvelopeError
from app.services.secrets import store_user_credentials, test_user_connection


def restore_demo_foundry_datasets(
    db: Session, settings: Settings, registry: ConnectorRegistry
) -> int:
    """Recreate saved empty Foundry datasets in the process-local demo emulator."""
    from app.connectors.errors import ConnectorError
    from app.connectors.fake import FakeFoundryConnector

    restored = 0
    saved = db.execute(
        select(FoundryDataset, UserSecret).join(
            UserSecret,
            (UserSecret.user_id == FoundryDataset.user_id)
            & (UserSecret.provider == FoundryDataset.provider),
        )
    ).all()
    for dataset, secret in saved:
        if dataset.provider not in {"mss", "mcscop"}:
            continue
        try:
            credentials = CredentialEnvelope.decrypt(settings, secret)
            connector = registry.connector_for(dataset.provider)
            if not isinstance(connector, FakeFoundryConnector):
                continue
            connector.restore_dataset(
                credentials,
                dataset_rid=dataset.dataset_rid,
                branch=dataset.branch or "master",
            )
        except (CredentialEnvelopeError, ConnectorError):
            continue
        restored += 1
    return restored


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

# Credential payloads from demo databases created before structured provider
# fields were expanded. Keep these exact so a real user's credentials are
# never mistaken for demo state merely because they share some field values.
LEGACY_DEMO_CONNECTION_CREDENTIALS: dict[str, tuple[dict[str, str], ...]] = {
    "mss": (
        {
            "endpoint": "https://mss.demo.invalid",
            "username": "data_mover_demo",
            "token": "fake-mss-token-for-demo-only",
        },
        {
            "endpoint": "https://mss.demo.invalid",
            "token": "fake-mss-token-for-demo-only",
            "branch": "master",
            "ca_profile": "system",
        },
    ),
    "postgres": (
        {
            "host": "postgres.demo.invalid",
            "port": "5432",
            "database": "readiness_demo",
            "username": "data_mover_demo",
            "password": "fake-postgres-password-for-demo-only",
            "sslmode": "require",
        },
    ),
}


@dataclass(frozen=True)
class DemoConnectionSeedResult:
    seeded: tuple[str, ...]
    skipped: tuple[str, ...]
    refreshed: tuple[str, ...] = ()
    revalidated: tuple[str, ...] = ()


def _classify_demo_credentials(
    settings: Settings,
    stored: UserSecret,
    expected: dict[str, str],
) -> str:
    """Classify an encrypted bundle without guessing about user-owned data."""

    try:
        actual = CredentialEnvelope.decrypt(settings, stored)
    except CredentialEnvelopeError:
        # An unreadable or differently encrypted secret is user-owned state;
        # never replace it just because demo seeding is running.
        return "unknown"

    if actual == expected:
        return "current"
    if actual in LEGACY_DEMO_CONNECTION_CREDENTIALS.get(stored.provider, ()):
        return "legacy"
    return "unknown"


def seed_demo_connections(
    db: Session,
    settings: Settings,
    *,
    user: User,
    replace: bool = False,
) -> DemoConnectionSeedResult:
    """Seed fake bundles and repair only recognizable legacy demo credentials."""
    from app.connectors.registry import load_builtin_connectors

    load_builtin_connectors(demo=settings.is_demo_mode)
    if settings.is_production:
        raise ValueError("Fake demo credentials cannot be seeded in production.")
    if not settings.is_demo_mode:
        raise ValueError("Fake demo credentials can only be seeded in demo mode.")

    existing = {
        secret.provider: secret
        for secret in db.scalars(select(UserSecret).where(UserSecret.user_id == user.id)).all()
    }
    seeded: list[str] = []
    skipped: list[str] = []
    refreshed: list[str] = []
    revalidated: list[str] = []
    for provider, credentials in DEMO_CONNECTION_CREDENTIALS.items():
        stored = existing.get(provider)
        if stored is not None and not replace:
            classification = _classify_demo_credentials(settings, stored, credentials)
            if classification == "unknown":
                skipped.append(provider)
                continue
            if classification == "current" and stored.validation_status == "connected":
                skipped.append(provider)
                continue
            if classification == "current":
                test_user_connection(db, settings=settings, user=user, provider=provider)
                revalidated.append(provider)
                continue
        store_user_credentials(
            db,
            settings,
            user=user,
            provider=provider,
            credentials=credentials,
        )
        test_user_connection(db, settings=settings, user=user, provider=provider)
        (refreshed if stored is not None and not replace else seeded).append(provider)

    return DemoConnectionSeedResult(
        tuple(seeded), tuple(skipped), tuple(refreshed), tuple(revalidated)
    )
