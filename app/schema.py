from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import Engine, inspect

from app.config import BASE_DIR, get_settings
from app.database import Base, engine

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


def alembic_config(database_url: str | None = None) -> Config:
    # Configure Alembic programmatically so the installed console command does not
    # depend on an alembic.ini file outside the Python packages.
    config = Config()
    config.set_main_option("script_location", str(BASE_DIR / "migrations"))
    url = database_url or get_settings().database_url
    config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    return config


def current_revision(db_engine: Engine = engine) -> str | None:
    with db_engine.connect() as connection:
        return MigrationContext.configure(connection).get_current_revision()


def head_revision() -> str:
    return ScriptDirectory.from_config(alembic_config()).get_current_head()


def assert_schema_current(db_engine: Engine = engine) -> None:
    current = current_revision(db_engine)
    head = head_revision()
    if current != head:
        raise RuntimeError(
            f"Database schema is at {current or 'no Alembic revision'}; expected {head}. "
            "Run `python -m app migrate` before starting the application."
        )


def adopt_existing_schema(db_engine: Engine = engine) -> str:
    inspector = inspect(db_engine)
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
    known_existing_tables = CORE_TABLES | {
        table_name for table_name in (REGISTRATION_TABLE, RATE_LIMIT_TABLE) if table_name in tables
    }
    for table_name in known_existing_tables:
        expected = {column.name for column in Base.metadata.tables[table_name].columns}
        actual = {column["name"] for column in inspector.get_columns(table_name)}
        if expected != actual:
            raise RuntimeError(f"Existing table {table_name!r} does not match the baseline schema.")
    if RATE_LIMIT_TABLE in tables:
        revision = "0003_shared_rate_limits"
    elif REGISTRATION_TABLE in tables:
        revision = "0002_self_registration"
    else:
        revision = "0001_initial_schema"
    command.stamp(alembic_config(str(db_engine.url)), revision)
    return revision


def upgrade_schema(*, adopt_existing: bool = False, database_url: str | None = None) -> None:
    config = alembic_config(database_url)
    if adopt_existing:
        target_engine = engine
        if database_url and database_url != str(engine.url):
            from sqlalchemy import create_engine

            target_engine = create_engine(database_url)
        adopt_existing_schema(target_engine)
    command.upgrade(config, "head")
