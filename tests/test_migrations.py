import pytest
from alembic import command
from sqlalchemy import create_engine, inspect, select
from sqlalchemy.orm import Session

from app.database import Base
from app.models import Role
from app.schema import (
    CORE_TABLES,
    adopt_existing_schema,
    alembic_config,
    assert_schema_current,
    current_revision,
    head_revision,
    upgrade_schema,
)
from app.services.auth import ensure_default_roles


def sqlite_url(path) -> str:
    return f"sqlite:///{path}"


def test_migrations_upgrade_empty_database_and_match_metadata(tmp_path) -> None:
    url = sqlite_url(tmp_path / "migrated.db")
    upgrade_schema(database_url=url)
    migrated_engine = create_engine(url)

    assert current_revision(migrated_engine) == head_revision()
    assert set(Base.metadata.tables) <= set(inspect(migrated_engine).get_table_names())
    assert_schema_current(migrated_engine)

    with Session(migrated_engine) as db:
        ensure_default_roles(db)
        assert db.scalars(select(Role.name).order_by(Role.name)).all() == [
            "administrator",
            "user",
        ]

    # Alembic's autogenerate comparison must see no model/schema drift.
    command.check(alembic_config(url))


def test_migrations_downgrade_cleanly_to_base(tmp_path) -> None:
    url = sqlite_url(tmp_path / "downgraded.db")
    upgrade_schema(database_url=url)
    command.downgrade(alembic_config(url), "base")
    remaining = set(inspect(create_engine(url)).get_table_names())
    assert remaining <= {"alembic_version"}


def test_existing_pre_alembic_schema_is_explicitly_adopted_then_upgraded(tmp_path) -> None:
    url = sqlite_url(tmp_path / "adopted.db")
    adopted_engine = create_engine(url)
    Base.metadata.create_all(
        adopted_engine,
        tables=[Base.metadata.tables[name] for name in sorted(CORE_TABLES)],
    )

    assert adopt_existing_schema(adopted_engine) == "0001_initial_schema"
    assert current_revision(adopted_engine) == "0001_initial_schema"
    upgrade_schema(database_url=url)
    assert current_revision(adopted_engine) == head_revision()
    assert "registration_verifications" in inspect(adopted_engine).get_table_names()
    assert "rate_limit_buckets" in inspect(adopted_engine).get_table_names()
    assert "user_secrets" in inspect(adopted_engine).get_table_names()


def test_adoption_refuses_an_unknown_partial_schema(tmp_path) -> None:
    url = sqlite_url(tmp_path / "partial.db")
    partial_engine = create_engine(url)
    Base.metadata.tables["roles"].create(partial_engine)
    try:
        adopt_existing_schema(partial_engine)
    except RuntimeError as exc:
        assert "missing tables" in str(exc)
    else:
        raise AssertionError("partial schema must not be stamped")


def test_adoption_refuses_malformed_optional_schema(tmp_path) -> None:
    url = sqlite_url(tmp_path / "malformed-optional.db")
    malformed_engine = create_engine(url)
    Base.metadata.create_all(
        malformed_engine,
        tables=[Base.metadata.tables[name] for name in sorted(CORE_TABLES)],
    )
    with malformed_engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TABLE registration_verifications (id VARCHAR(36) PRIMARY KEY)"
        )

    with pytest.raises(RuntimeError, match="does not match"):
        adopt_existing_schema(malformed_engine)
