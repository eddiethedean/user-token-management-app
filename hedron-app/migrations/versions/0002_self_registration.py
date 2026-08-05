"""Add single-use self-registration verification capabilities."""

import sqlalchemy as sa
from alembic import op

revision = "0002_self_registration"
down_revision = "0001_initial_schema"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "registration_verifications",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("used_at", sa.DateTime(), nullable=True),
        sa.Column("requested_ip", sa.String(length=64), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_registration_verifications_user_active",
        "registration_verifications",
        ["user_id", "used_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_registration_verifications_token_hash"),
        "registration_verifications",
        ["token_hash"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_registration_verifications_token_hash"),
        table_name="registration_verifications",
    )
    op.drop_index(
        "ix_registration_verifications_user_active",
        table_name="registration_verifications",
    )
    op.drop_table("registration_verifications")
