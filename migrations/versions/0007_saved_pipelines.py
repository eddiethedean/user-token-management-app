"""Add user-owned saved pipeline definitions."""

import sqlalchemy as sa
from alembic import op

revision = "0007_saved_pipelines"
down_revision = "0006_api_key_usage"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "pipeline_definitions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("source_provider", sa.String(length=32), nullable=False),
        sa.Column("source_dataset", sa.String(length=80), nullable=False),
        sa.Column("destination_provider", sa.String(length=32), nullable=False),
        sa.Column("write_mode", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_pipeline_definitions_user_id"),
        "pipeline_definitions",
        ["user_id"],
        unique=False,
    )
    op.create_index(
        "ix_pipeline_definitions_user_updated",
        "pipeline_definitions",
        ["user_id", "updated_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_pipeline_definitions_user_updated", table_name="pipeline_definitions")
    op.drop_index(op.f("ix_pipeline_definitions_user_id"), table_name="pipeline_definitions")
    op.drop_table("pipeline_definitions")
