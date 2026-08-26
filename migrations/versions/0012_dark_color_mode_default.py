"""Make dark mode the default for newly created users."""

import sqlalchemy as sa
from alembic import op

revision = "0012_dark_color_mode_default"
down_revision = "0011_user_color_mode"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.alter_column(
            "preferred_color_mode",
            existing_type=sa.String(length=10),
            existing_nullable=False,
            server_default="dark",
        )


def downgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.alter_column(
            "preferred_color_mode",
            existing_type=sa.String(length=10),
            existing_nullable=False,
            server_default="light",
        )
