"""Remove the unused BCC recipient from the email outbox."""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0025_remove_email_bcc"
down_revision = "0024_email_bcc"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("email_outbox") as batch:
        batch.drop_column("bcc_recipient")


def downgrade() -> None:
    with op.batch_alter_table("email_outbox") as batch:
        batch.add_column(sa.Column("bcc_recipient", sa.String(length=320), nullable=True))
