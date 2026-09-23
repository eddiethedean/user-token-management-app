"""Persist safe connection feedback codes and references."""

import sqlalchemy as sa
from alembic import op

revision = "0017_connection_feedback"
down_revision = "0016_email_cc_recipient"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("user_secrets") as batch:
        batch.add_column(sa.Column("validation_code", sa.String(length=64), nullable=True))
        batch.add_column(sa.Column("validation_reference", sa.String(length=64), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("user_secrets") as batch:
        batch.drop_column("validation_reference")
        batch.drop_column("validation_code")
