from __future__ import annotations

from alembic import command
from sqlalchemy import create_engine, inspect, text

from app.schema import alembic_config


def _insert_user(connection, *, user_id: str, email: str, include_color_mode: bool) -> None:
    columns = (
        "id, email, email_original, full_name, organization, job_title, phone, status, "
        "failed_login_attempts, security_version, created_at, updated_at"
    )
    values = (
        ":id, :email, :email, '', '', '', '', 'active', 0, 1, "
        "'2026-08-26 00:00:00', '2026-08-26 00:00:00'"
    )
    parameters = {"id": user_id, "email": email}
    if include_color_mode:
        columns += ", preferred_color_mode"
        values += ", 'light'"
    connection.execute(text(f"INSERT INTO users ({columns}) VALUES ({values})"), parameters)


def test_dark_default_migration_preserves_existing_user_choice(tmp_path) -> None:
    database_url = f"sqlite:///{tmp_path / 'color-mode.db'}"
    config = alembic_config(database_url)
    command.upgrade(config, "0011_user_color_mode")
    engine = create_engine(database_url)

    with engine.begin() as connection:
        _insert_user(
            connection,
            user_id="existing-user",
            email="existing@example.gov",
            include_color_mode=True,
        )

    command.upgrade(config, "head")

    with engine.begin() as connection:
        _insert_user(
            connection,
            user_id="new-user",
            email="new@example.gov",
            include_color_mode=False,
        )
        choices = dict(
            connection.execute(text("SELECT id, preferred_color_mode FROM users ORDER BY id")).all()
        )

    color_column = next(
        column
        for column in inspect(engine).get_columns("users")
        if column["name"] == "preferred_color_mode"
    )
    engine.dispose()

    assert choices == {"existing-user": "light", "new-user": "dark"}
    assert str(color_column["default"]).strip("()'\"") == "dark"
