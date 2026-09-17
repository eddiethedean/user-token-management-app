"""Add optional carbon-copy recipients to outbound email."""

import sqlalchemy as sa
from alembic import op

revision = "0016_email_cc_recipient"
down_revision = "0015_pipeline_locator_text"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("email_outbox") as batch:
        batch.add_column(sa.Column("cc_recipient", sa.String(length=320), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("email_outbox") as batch:
        batch.drop_column("cc_recipient")
