"""Shared pytest fixtures for Data Mover."""

from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient
from pytest_mongo import factories
from sqlalchemy import create_engine, event, select

from tests.mongo_support import mongod_executable, mongodb_available, use_external_mongo

mongo_proc = factories.mongo_proc(executable=mongod_executable())
mongodb_from_proc = factories.mongodb("mongo_proc")
mongodb_from_noproc = factories.mongodb("mongo_noproc")


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
        **dbmod._engine_options(settings.database_url, settings),
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
    monkeypatch.delenv("POSIT_PRODUCT", raising=False)
    monkeypatch.setenv("EMAIL_BACKEND", "console")
    monkeypatch.setenv("PASSWORD_HASH_SCHEME", "pbkdf2_sha256")
    monkeypatch.setenv("PBKDF2_ITERATIONS", "100000")
    monkeypatch.setenv("DATA_MOVER_MODE", "demo")
    monkeypatch.setenv("TRUSTED_PROXY_IPS", "127.0.0.1")

    _rebind_database()
    from app.schema import upgrade_schema

    upgrade_schema()
    from app.config import get_settings as _get_settings
    from app.connectors.registry import load_builtin_connectors

    load_builtin_connectors(demo=_get_settings().is_demo_mode)

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
def demo_connections(access_app):
    """Seed all fake, connected providers for pipeline tests that need a route."""
    from app.config import get_settings
    from app.database import SessionLocal
    from app.models import User
    from app.services.demo import seed_demo_connections

    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.email == "admin@example.gov"))
        assert user is not None
        return seed_demo_connections(db, get_settings(), user=user)


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


@pytest.fixture(scope="session")
def postgresql_factory():
    """Cache one initdb tree so connector tests do not re-run initdb per case."""
    from tests.postgres_support import postgres_available, postgres_binaries

    if not postgres_available():
        pytest.skip("PostgreSQL server binaries (initdb, postgres) are not available")
    from testing.postgresql import PostgresqlFactory

    factory = PostgresqlFactory(cache_initialized_db=True, **postgres_binaries())
    yield factory
    factory.clear_cache()


@pytest.fixture()
def postgresql(postgresql_factory):
    instance = postgresql_factory()
    try:
        yield instance
    finally:
        instance.stop()


@pytest.fixture()
def postgres_credentials(postgresql):
    from tests.postgres_support import credentials_from

    return credentials_from(postgresql)


@pytest.fixture
def mongodb(request):
    """pytest-mongo client: ephemeral mongod, or PYTEST_MONGO_NOPROC=1 for CI."""
    if not mongodb_available():
        pytest.skip("mongod is not on PATH; set PYTEST_MONGO_NOPROC=1 to use a running instance")
    fixture = "mongodb_from_noproc" if use_external_mongo() else "mongodb_from_proc"
    return request.getfixturevalue(fixture)
