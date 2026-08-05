"""Shared pytest fixtures for Access Registry."""

from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, select


def _rebind_database() -> None:
    """Point the process-wide engine/SessionLocal at the current DATABASE_URL."""
    from app import database as dbmod
    from app import schema as schemamod
    from app.config import get_settings

    get_settings.cache_clear()
    settings = get_settings()
    dbmod.engine.dispose()
    new_engine = create_engine(
        settings.database_url,
        **dbmod._engine_options(settings.database_url),
    )
    if new_engine.dialect.name == "sqlite":

        @event.listens_for(new_engine, "connect")
        def _enable_sqlite_foreign_keys(dbapi_connection, _) -> None:
            cursor = dbapi_connection.cursor()
            try:
                cursor.execute("PRAGMA foreign_keys=ON")
            finally:
                cursor.close()

    dbmod.engine = new_engine
    dbmod.SessionLocal.configure(bind=new_engine)
    schemamod.engine = new_engine


@pytest.fixture()
def access_app(tmp_path, monkeypatch):
    """Boot a fresh app against an isolated SQLite database."""
    db_path = tmp_path / "test.db"
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    monkeypatch.setenv("PUBLIC_BASE_URL", "http://testserver")
    monkeypatch.setenv("JWT_SECRET", "test-jwt-secret-at-least-32-bytes-long!!")
    monkeypatch.setenv("SESSION_PEPPER", "test-session-pepper-at-least-32-bytes!!")
    monkeypatch.setenv("CSRF_SECRET", "test-csrf-secret-at-least-32-bytes-long!")
    monkeypatch.setenv(
        "API_TOKEN_ENCRYPTION_KEYS",
        '{"development-v1":"AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="}',
    )
    monkeypatch.setenv("API_TOKEN_ACTIVE_KEY_ID", "development-v1")
    monkeypatch.setenv("ALLOWED_EMAIL_DOMAINS", "example.gov")
    monkeypatch.setenv("AUTHENTICATION_MODE", "local_password")
    monkeypatch.setenv("COOKIE_SECURE", "false")
    monkeypatch.setenv("EMAIL_BACKEND", "console")
    monkeypatch.setenv("PASSWORD_HASH_SCHEME", "pbkdf2_sha256")
    monkeypatch.setenv("PBKDF2_ITERATIONS", "100000")
    monkeypatch.setenv("TRUSTED_PROXY_IPS", "127.0.0.1")

    _rebind_database()
    from app.schema import upgrade_schema

    upgrade_schema()

    from app.cli import create_admin
    from app.main import app

    os.environ["ADMIN_BOOTSTRAP_PASSWORD"] = "Tr0pic-Maple!River92"
    assert create_admin("admin@example.gov", password="Tr0pic-Maple!River92") == 0

    yield app

    from app.config import get_settings

    get_settings.cache_clear()


@pytest.fixture()
def client(access_app):
    with TestClient(access_app, follow_redirects=False, client=("127.0.0.1", 50000)) as test_client:
        yield test_client


@pytest.fixture()
def page(access_app):
    """Hedron fastapi_fixture — full-page GETs/POSTs with cookie jar."""
    from hedron.testing import fastapi_fixture

    return fastapi_fixture(access_app)


@pytest.fixture()
def htmx(access_app):
    """Hedron fragment_client — HTMX headers, follows redirects by default."""
    from hedron.testing import fragment_client

    return fragment_client(access_app)


@pytest.fixture()
def make_user(access_app):
    from app.config import get_settings
    from app.database import SessionLocal
    from app.models import Role, User, UserStatus, utcnow
    from app.security.passwords import PasswordService

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
