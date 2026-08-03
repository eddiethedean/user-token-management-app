"""Add aggregate API-token master-key usage accounting."""

import sqlalchemy as sa
from alembic import op

revision = "0006_api_key_usage"
down_revision = "0005_atomic_tokens_email"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "api_token_key_usage",
        sa.Column("key_id", sa.String(length=64), nullable=False),
        sa.Column("wrap_count", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("key_id"),
    )
    connection = op.get_bind()
    secrets = sa.table(
        "user_secrets",
        sa.column("master_key_id", sa.String(length=64)),
        sa.column("updated_at", sa.DateTime()),
    )
    usage = sa.table(
        "api_token_key_usage",
        sa.column("key_id", sa.String(length=64)),
        sa.column("wrap_count", sa.BigInteger()),
        sa.column("updated_at", sa.DateTime()),
    )
    connection.execute(
        usage.insert().from_select(
            ["key_id", "wrap_count", "updated_at"],
            sa.select(
                secrets.c.master_key_id,
                # Historical replacements cannot be reconstructed per key. Mark every key
                # with pre-counter ciphertext above the maximum configurable ceiling so it
                # remains decrypt-only and operators must select a fresh active key.
                sa.literal(100_000_001),
                sa.func.max(secrets.c.updated_at),
            ).group_by(secrets.c.master_key_id),
        )
    )


def downgrade() -> None:
    op.drop_table("api_token_key_usage")
