"""Persist each user's preferred light or dark color mode."""

import sqlalchemy as sa
from alembic import op

revision = "0011_user_color_mode"
down_revision = "0010_real_transfer_runs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.add_column(
            sa.Column(
                "preferred_color_mode",
                sa.String(length=10),
                server_default="light",
                nullable=False,
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.drop_column("preferred_color_mode")
