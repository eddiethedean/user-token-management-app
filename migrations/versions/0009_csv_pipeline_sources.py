"""Add owner-scoped CSV uploads for pipeline sources."""

import sqlalchemy as sa
from alembic import op

revision = "0009_csv_sources"
down_revision = "0008_catalogs_health"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "pipeline_uploads",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("filename", sa.String(length=180), nullable=False),
        sa.Column("content_type", sa.String(length=100), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("row_count", sa.Integer(), nullable=False),
        sa.Column("column_count", sa.Integer(), nullable=False),
        sa.Column("columns_json", sa.Text(), nullable=False),
        sa.Column("checksum_sha256", sa.String(length=64), nullable=False),
        sa.Column("content", sa.LargeBinary(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_pipeline_uploads_user_id"),
        "pipeline_uploads",
        ["user_id"],
        unique=False,
    )
    op.create_index(
        "ix_pipeline_uploads_user_created",
        "pipeline_uploads",
        ["user_id", "created_at"],
        unique=False,
    )
    with op.batch_alter_table("pipeline_definitions") as batch:
        batch.add_column(sa.Column("source_upload_id", sa.String(length=36), nullable=True))
        batch.create_foreign_key(
            "fk_pipeline_definitions_source_upload_id_pipeline_uploads",
            "pipeline_uploads",
            ["source_upload_id"],
            ["id"],
            ondelete="SET NULL",
        )


def downgrade() -> None:
    with op.batch_alter_table("pipeline_definitions") as batch:
        batch.drop_constraint(
            "fk_pipeline_definitions_source_upload_id_pipeline_uploads", type_="foreignkey"
        )
        batch.drop_column("source_upload_id")
    op.drop_index("ix_pipeline_uploads_user_created", table_name="pipeline_uploads")
    op.drop_index(op.f("ix_pipeline_uploads_user_id"), table_name="pipeline_uploads")
    op.drop_table("pipeline_uploads")
