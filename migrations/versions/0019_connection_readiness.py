"""Persist normalized connection validation mode and scope."""

import sqlalchemy as sa
from alembic import op

revision = "0019_connection_readiness"
down_revision = "0018_pipeline_reconcile"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("user_secrets") as batch:
        batch.add_column(
            sa.Column(
                "validation_mode", sa.String(length=20), nullable=False, server_default="unknown"
            )
        )
        batch.add_column(
            sa.Column("validation_scope", sa.String(length=160), nullable=False, server_default="")
        )

    user_secrets = sa.table(
        "user_secrets",
        sa.column("validated_at", sa.DateTime()),
        sa.column("validation_scope", sa.String()),
    )
    op.get_bind().execute(
        sa.update(user_secrets)
        .where(user_secrets.c.validated_at.is_not(None))
        .values(validation_scope="Provider connectivity")
    )


def downgrade() -> None:
    with op.batch_alter_table("user_secrets") as batch:
        batch.drop_column("validation_scope")
        batch.drop_column("validation_mode")
