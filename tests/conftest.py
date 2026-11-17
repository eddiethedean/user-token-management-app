"""Shared pytest fixtures for Data Mover."""

from __future__ import annotations

from contextlib import ExitStack
from pathlib import Path
from shutil import copyfile

import pytest
from fastapi.testclient import TestClient
from pytest_mongo import factories
from sqlalchemy import select

from tests.mongo_support import mongod_executable, mongodb_available, use_external_mongo

mongo_proc = factories.mongo_proc(executable=mongod_executable())
mongodb_from_proc = factories.mongodb("mongo_proc")
mongodb_from_noproc = factories.mongodb("mongo_noproc")


def pytest_configure(config: pytest.Config) -> None:
    """Keep the workspace dotenv out of app settings, including collection-time imports."""
    from app.config import Settings, get_settings

    patch = pytest.MonkeyPatch()
    patch.setitem(Settings.model_config, "env_file", None)
    get_settings.cache_clear()
    config.add_cleanup(get_settings.cache_clear)
    config.add_cleanup(patch.undo)


@pytest.fixture(scope="session")
def sqlite_database_template(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Migrate an empty SQLite database once per pytest worker."""
    from app.schema import upgrade_schema

    path = tmp_path_factory.mktemp("sqlite-template") / "schema.db"
    upgrade_schema(database_url=f"sqlite:///{path}")
    return path


@pytest.fixture()
def access_app(tmp_path, sqlite_database_template):
    """Boot a fresh app against an isolated SQLite database."""
    from app import database as dbmod
    from app.cli import create_admin
    from app.config import get_settings
    from app.connectors import registry as registry_module
    from app.infrastructure.runtime import ExecutionRuntime
    from tests.helpers import ADMIN_EMAIL, ADMIN_PASSWORD

    db_path = tmp_path / "test.db"
    copyfile(sqlite_database_template, db_path)
    with ExitStack() as cleanup:
        cleanup.callback(get_settings.cache_clear)
        patch = cleanup.enter_context(pytest.MonkeyPatch.context())
        patch.setenv("APP_ENV", "test")
        patch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
        patch.setenv("PUBLIC_BASE_URL", "http://testserver")
        patch.setenv("JWT_SECRET", "test-jwt-secret-at-least-32-bytes-long!!")
        patch.setenv("SESSION_PEPPER", "test-session-pepper-at-least-32-bytes!!")
        patch.setenv("CSRF_SECRET", "test-csrf-secret-at-least-32-bytes-long!")
        patch.setenv(
            "API_TOKEN_ENCRYPTION_KEYS",
            '{"development-v1":"AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="}',
        )
        patch.setenv("API_TOKEN_ACTIVE_KEY_ID", "development-v1")
        patch.setenv("ALLOWED_EMAIL_DOMAINS", "example.gov")
        patch.setenv("AUTHENTICATION_MODE", "local_password")
        patch.setenv("COOKIE_SECURE", "false")
        patch.delenv("POSIT_PRODUCT", raising=False)
        patch.setenv("DIRECTORY_LOOKUP_URL", "")
        patch.setenv("DIRECTORY_LOOKUP_REQUIRED", "false")
        patch.setenv("EMAIL_BACKEND", "console")
        patch.setenv("EMAIL_REDACT_SENT_BODIES", "false")
        patch.setenv("PASSWORD_HASH_SCHEME", "pbkdf2_sha256")
        patch.setenv("PBKDF2_ITERATIONS", "100000")
        patch.setenv("DATA_MOVER_MODE", "demo")
        patch.setenv("TRUSTED_PROXY_IPS", "127.0.0.1")

        get_settings.cache_clear()
        settings = get_settings()
        database = dbmod.create_runtime(settings)
        cleanup.callback(database.dispose)
        # The CLI and some fixtures still use the compatibility sessionmaker.
        patch.setattr(dbmod, "engine", database.engine)
        patch.setitem(dbmod.SessionLocal.kw, "bind", database.engine)

        registry = registry_module.ConnectorRegistry()
        patch.setattr(registry_module, "_DEFAULT_REGISTRY", registry)
        registry_module.load_builtin_connectors(demo=True, registry=registry, settings=settings)

        from app.main import app

        assert create_admin(ADMIN_EMAIL, password=ADMIN_PASSWORD) == 0

        patch.setattr(app.state, "runtime_lifecycle", "fixture", raising=False)
        patch.setattr(
            app.state,
            "execution",
            ExecutionRuntime(settings, database.sessions, registry),
            raising=False,
        )
        patch.setattr(app.state, "settings", settings, raising=False)
        patch.setattr(app.state, "ready", True, raising=False)
        try:
            yield app
        finally:
            app.state.execution.begin_shutdown()


@pytest.fixture()
def client(access_app):
    # ``access_app`` supplies a fixture-owned runtime.  Do not enter TestClient's
    # lifespan here: doing so replaces that runtime with the process entrypoint's
    # accepting generation before a test can configure its fixture settings.
    test_client = TestClient(access_app, follow_redirects=False, client=("127.0.0.1", 50000))
    try:
        yield test_client
    finally:
        test_client.close()


@pytest.fixture()
def request_settings_override(access_app):
    """Deliberately replace request settings for tests of mutable policy values."""

    from app.config import get_settings

    original = access_app.dependency_overrides.get(get_settings)

    def install(settings):
        access_app.dependency_overrides[get_settings] = lambda: settings

    yield install

    if original is None:
        access_app.dependency_overrides.pop(get_settings, None)
    else:
        access_app.dependency_overrides[get_settings] = original


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
