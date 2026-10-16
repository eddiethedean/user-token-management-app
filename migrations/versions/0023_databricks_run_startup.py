"""Defer opted-in runs while durable Databricks compute startup completes."""

import sqlalchemy as sa
from alembic import op

revision = "0023_dbx_run_startup"
down_revision = "0022_databricks_ops"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("pipeline_runs") as batch:
        batch.add_column(sa.Column("next_attempt_at", sa.DateTime(), nullable=True))
        batch.add_column(sa.Column("databricks_compute_attempt_id", sa.String(length=36)))
        batch.add_column(sa.Column("databricks_compute_start_deadline_at", sa.DateTime()))
        batch.create_foreign_key(
            "fk_pipeline_runs_databricks_compute_attempt",
            "databricks_compute_attempts",
            ["databricks_compute_attempt_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch.create_index("ix_pipeline_runs_next_attempt_at", ["next_attempt_at"])


def downgrade() -> None:
    with op.batch_alter_table("pipeline_runs") as batch:
        batch.drop_index("ix_pipeline_runs_next_attempt_at")
        batch.drop_constraint("fk_pipeline_runs_databricks_compute_attempt", type_="foreignkey")
        batch.drop_column("databricks_compute_start_deadline_at")
        batch.drop_column("databricks_compute_attempt_id")
        batch.drop_column("next_attempt_at")
