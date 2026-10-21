"""Support blind-copy recipients in queued email."""

import sqlalchemy as sa
from alembic import op

revision = "0024_email_bcc"
down_revision = "0023_dbx_run_startup"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("email_outbox") as batch:
        batch.add_column(sa.Column("bcc_recipient", sa.String(length=320), nullable=True))
        batch.drop_column("cc_recipient")


def downgrade() -> None:
    with op.batch_alter_table("email_outbox") as batch:
        batch.add_column(sa.Column("cc_recipient", sa.String(length=320), nullable=True))
        batch.drop_column("bcc_recipient")
