from __future__ import annotations

from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import Engine, inspect

from app.config import BASE_DIR, get_settings
from app.database import Base

CORE_TABLES = {
    "roles",
    "users",
    "user_roles",
    "invitations",
    "password_resets",
    "refresh_sessions",
    "audit_events",
    "email_outbox",
}
REGISTRATION_TABLE = "registration_verifications"
RATE_LIMIT_TABLE = "rate_limit_buckets"
USER_SECRET_TABLE = "user_secrets"
ATOMIC_SUPPORT_TABLES = {"refresh_token_history", "email_delivery_state"}
KEY_USAGE_TABLE = "api_token_key_usage"


def alembic_config(database_url: str | None = None) -> Config:
    # Configure Alembic programmatically so the installed console command does not
    # depend on an alembic.ini file outside the Python packages.
    config = Config()
    config.set_main_option("script_location", str(BASE_DIR / "migrations"))
    url = database_url or get_settings().database_url
    config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    return config


def current_revision(db_engine: Engine | None = None) -> str | None:
    from app.database import engine as live_engine

    target = db_engine if db_engine is not None else live_engine
    with target.connect() as connection:
        return MigrationContext.configure(connection).get_current_revision()


def head_revision() -> str:
    head = ScriptDirectory.from_config(alembic_config()).get_current_head()
    if head is None:
        raise RuntimeError("No Alembic head revision is configured.")
    return head


def assert_schema_current(db_engine: Engine | None = None) -> None:
    from app.database import engine as live_engine

    target = db_engine if db_engine is not None else live_engine
    current = current_revision(target)
    head = head_revision()
    if current != head:
        raise RuntimeError(
            f"Database schema is at {current or 'no Alembic revision'}; expected {head}. "
            "Run `python -m app migrate` before starting the application."
        )


def adopt_existing_schema(db_engine: Engine | None = None) -> str:
    from app.database import engine as live_engine

    target = db_engine if db_engine is not None else live_engine
    inspector = inspect(target)
    tables = set(inspector.get_table_names())
    if "alembic_version" in tables:
        raise RuntimeError("Database is already managed by Alembic.")
    missing = CORE_TABLES - tables
    if missing:
        raise RuntimeError(
            "Existing database does not match the baseline schema; missing tables: "
            + ", ".join(sorted(missing))
        )
    if RATE_LIMIT_TABLE in tables and REGISTRATION_TABLE not in tables:
        raise RuntimeError(
            "Existing schema has shared rate limits without the preceding self-registration schema."
        )
    if USER_SECRET_TABLE in tables and RATE_LIMIT_TABLE not in tables:
        raise RuntimeError(
            "Existing schema has user API secrets without the preceding shared rate-limit schema."
        )
    present_atomic_tables = ATOMIC_SUPPORT_TABLES & tables
    if present_atomic_tables and present_atomic_tables != ATOMIC_SUPPORT_TABLES:
        raise RuntimeError("Existing schema has only part of the atomic-token/email-worker schema.")
    if present_atomic_tables and USER_SECRET_TABLE not in tables:
        raise RuntimeError(
            "Existing schema has atomic-token/email-worker tables without user API secrets."
        )
    if KEY_USAGE_TABLE in tables and not ATOMIC_SUPPORT_TABLES <= tables:
        raise RuntimeError(
            "Existing schema has API-token key accounting without the preceding atomic schema."
        )
    known_existing_tables = CORE_TABLES | {
        table_name
        for table_name in (
            REGISTRATION_TABLE,
            RATE_LIMIT_TABLE,
            USER_SECRET_TABLE,
            *sorted(ATOMIC_SUPPORT_TABLES),
            KEY_USAGE_TABLE,
        )
        if table_name in tables
    }
    for table_name in known_existing_tables:
        expected = {column.name for column in Base.metadata.tables[table_name].columns}
        actual = {column["name"] for column in inspector.get_columns(table_name)}
        if expected != actual:
            raise RuntimeError(f"Existing table {table_name!r} does not match the baseline schema.")
    if KEY_USAGE_TABLE in tables:
        revision = "0006_api_key_usage"
    elif ATOMIC_SUPPORT_TABLES <= tables:
        revision = "0005_atomic_tokens_email"
    elif USER_SECRET_TABLE in tables:
        revision = "0004_user_api_secrets"
    elif RATE_LIMIT_TABLE in tables:
        revision = "0003_shared_rate_limits"
    elif REGISTRATION_TABLE in tables:
        revision = "0002_self_registration"
    else:
        revision = "0001_initial_schema"
    command.stamp(alembic_config(str(target.url)), revision)
    return revision


def upgrade_schema(*, adopt_existing: bool = False, database_url: str | None = None) -> None:
    from app.database import engine as live_engine

    config = alembic_config(database_url)
    if adopt_existing:
        target_engine = live_engine
        if database_url and database_url != str(live_engine.url):
            from sqlalchemy import create_engine

            target_engine = create_engine(database_url)
        adopt_existing_schema(target_engine)
    command.upgrade(config, "head")
