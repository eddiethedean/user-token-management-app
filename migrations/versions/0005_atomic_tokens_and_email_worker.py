"""Add refresh replay history and claimable email delivery state."""

import sqlalchemy as sa
from alembic import op

revision = "0005_atomic_tokens_email"
down_revision = "0004_user_api_secrets"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "refresh_token_history",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("session_id", sa.String(length=36), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("consumed_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["session_id"], ["refresh_sessions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_refresh_token_history_session_id"),
        "refresh_token_history",
        ["session_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_refresh_token_history_token_hash"),
        "refresh_token_history",
        ["token_hash"],
        unique=True,
    )
    op.create_table(
        "email_delivery_state",
        sa.Column("message_id", sa.String(length=36), nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(), nullable=False),
        sa.Column("claim_token", sa.String(length=64), nullable=True),
        sa.Column("claimed_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["message_id"], ["email_outbox.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("message_id"),
        sa.UniqueConstraint("claim_token"),
    )
    op.create_index(
        op.f("ix_email_delivery_state_next_attempt_at"),
        "email_delivery_state",
        ["next_attempt_at"],
        unique=False,
    )
    connection = op.get_bind()
    outbox = sa.table(
        "email_outbox",
        sa.column("id", sa.String(length=36)),
        sa.column("created_at", sa.DateTime()),
    )
    state = sa.table(
        "email_delivery_state",
        sa.column("message_id", sa.String(length=36)),
        sa.column("next_attempt_at", sa.DateTime()),
    )
    connection.execute(
        state.insert().from_select(
            ["message_id", "next_attempt_at"],
            sa.select(outbox.c.id, outbox.c.created_at),
        )
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_email_delivery_state_next_attempt_at"),
        table_name="email_delivery_state",
    )
    op.drop_table("email_delivery_state")
    op.drop_index(
        op.f("ix_refresh_token_history_token_hash"),
        table_name="refresh_token_history",
    )
    op.drop_index(
        op.f("ix_refresh_token_history_session_id"),
        table_name="refresh_token_history",
    )
    op.drop_table("refresh_token_history")
