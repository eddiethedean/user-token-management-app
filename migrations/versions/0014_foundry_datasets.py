"""Add owner-scoped records for Foundry datasets created by Data Mover."""

import sqlalchemy as sa
from alembic import op

revision = "0014_foundry_datasets"
down_revision = "0013_pipeline_event_sequence"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "foundry_datasets",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("dataset_rid", sa.String(length=240), nullable=False),
        sa.Column("name", sa.String(length=240), nullable=False),
        sa.Column("parent_folder_rid", sa.String(length=240), nullable=False),
        sa.Column("branch", sa.String(length=80), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "user_id",
            "provider",
            "dataset_rid",
            name="uq_foundry_datasets_owner_provider_rid",
        ),
    )
    op.create_index(
        op.f("ix_foundry_datasets_user_id"),
        "foundry_datasets",
        ["user_id"],
        unique=False,
    )
    op.create_index(
        "ix_foundry_datasets_user_provider_created",
        "foundry_datasets",
        ["user_id", "provider", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_foundry_datasets_user_provider_created", table_name="foundry_datasets")
    op.drop_index(op.f("ix_foundry_datasets_user_id"), table_name="foundry_datasets")
    op.drop_table("foundry_datasets")
