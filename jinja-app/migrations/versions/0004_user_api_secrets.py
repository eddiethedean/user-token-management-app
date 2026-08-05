"""Add encrypted user-owned API token records."""

import sqlalchemy as sa
from alembic import op

revision = "0004_user_api_secrets"
down_revision = "0003_shared_rate_limits"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user_secrets",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("ciphertext", sa.Text(), nullable=False),
        sa.Column("nonce", sa.String(length=32), nullable=False),
        sa.Column("encrypted_data_key", sa.Text(), nullable=False),
        sa.Column("key_nonce", sa.String(length=32), nullable=False),
        sa.Column("master_key_id", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("last_used_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "provider", name="uq_user_secrets_owner_provider"),
    )
    op.create_index(op.f("ix_user_secrets_user_id"), "user_secrets", ["user_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_user_secrets_user_id"), table_name="user_secrets")
    op.drop_table("user_secrets")
