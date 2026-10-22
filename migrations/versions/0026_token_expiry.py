"""Record advisory expiry metadata without exposing stored API tokens."""

import sqlalchemy as sa
from alembic import op

revision = "0026_token_expiry"
down_revision = "0025_remove_email_bcc"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("user_secrets") as batch:
        batch.add_column(sa.Column("token_expires_at", sa.DateTime(), nullable=True))
        batch.add_column(
            sa.Column(
                "token_expiry_source", sa.String(24), server_default="unknown", nullable=False
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("user_secrets") as batch:
        batch.drop_column("token_expiry_source")
        batch.drop_column("token_expires_at")
