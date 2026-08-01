import os
import tempfile
from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

TEST_DIRECTORY = tempfile.mkdtemp(prefix="access-registry-tests-")
os.environ.update(
    {
        "APP_ENV": "test",
        "DATABASE_URL": f"sqlite:///{TEST_DIRECTORY}/test.db",
        "ALLOWED_EMAIL_DOMAINS": "example.gov",
        "JWT_SECRET": "test-jwt-secret-that-is-at-least-thirty-two-bytes",
        "SESSION_PEPPER": "test-session-pepper-that-is-at-least-thirty-two-bytes",
        "CSRF_SECRET": "test-csrf-secret-that-is-at-least-thirty-two-bytes",
        "PASSWORD_HASH_SCHEME": "pbkdf2_sha256",
        "PBKDF2_ITERATIONS": "100000",
        "PUBLIC_BASE_URL": "https://registry.example.gov",
    }
)

from app.config import get_settings  # noqa: E402
from app.database import Base, SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Role, User, UserStatus, utcnow  # noqa: E402
from app.security.passwords import PasswordService  # noqa: E402
from app.services.auth import ensure_default_roles  # noqa: E402

ADMIN_EMAIL = "admin@example.gov"
ADMIN_PASSWORD = "River-Lantern-94!Blue"


def seed_admin() -> User:
    with SessionLocal() as db:
        administrator = db.scalar(select(Role).where(Role.name == "administrator"))
        user = User(
            email=ADMIN_EMAIL,
            email_original=ADMIN_EMAIL,
            email_verified_at=utcnow(),
            full_name="Registry Administrator",
            status=UserStatus.ACTIVE.value,
            password_hash=PasswordService(get_settings()).hash(ADMIN_PASSWORD),
            password_changed_at=utcnow(),
            roles=[administrator] if administrator else [],
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        return user


@pytest.fixture()
def client() -> Generator[TestClient, None, None]:
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    with SessionLocal() as db:
        ensure_default_roles(db)
    seed_admin()
    with TestClient(app, follow_redirects=False) as test_client:
        yield test_client


@pytest.fixture()
def make_user(client):
    def factory(
        email: str,
        *,
        password: str = "Aspen-Compass-64!River",
        roles: tuple[str, ...] = ("user",),
        status: str = UserStatus.ACTIVE.value,
        verified: bool = True,
    ) -> User:
        with SessionLocal() as db:
            assigned_roles = db.scalars(select(Role).where(Role.name.in_(roles))).all()
            user = User(
                email=email.casefold(),
                email_original=email,
                email_verified_at=utcnow() if verified else None,
                full_name=email.partition("@")[0].replace(".", " ").title(),
                status=status,
                password_hash=PasswordService(get_settings()).hash(password),
                password_changed_at=utcnow(),
                roles=list(assigned_roles),
            )
            db.add(user)
            db.commit()
            db.refresh(user)
            return user

    return factory
