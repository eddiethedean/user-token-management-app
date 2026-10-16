"""Persist Databricks compute attempts and publication recovery evidence."""

import sqlalchemy as sa
from alembic import op

revision = "0022_databricks_ops"
down_revision = "0021_sensitive_data_guardrails"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "databricks_compute_attempts",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("workspace_id", sa.String(length=64), nullable=False),
        sa.Column("compute_key", sa.String(length=64), nullable=False),
        sa.Column("active_key", sa.String(length=64), nullable=True),
        sa.Column("compute_kind", sa.String(length=32), nullable=False),
        sa.Column("compute_id", sa.String(length=128), nullable=False),
        sa.Column("connection_generation", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False, server_default="starting"),
        sa.Column("last_state", sa.String(length=32), nullable=False, server_default="UNKNOWN"),
        sa.Column("poll_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("requested_at", sa.DateTime(), nullable=False),
        sa.Column("deadline_at", sa.DateTime(), nullable=False),
        sa.Column("next_poll_at", sa.DateTime(), nullable=False),
        sa.Column("checked_at", sa.DateTime(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("lease_token", sa.String(length=64), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("error_summary", sa.String(length=240), nullable=False, server_default=""),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("active_key", name="uq_databricks_compute_attempt_active_key"),
    )
    op.create_index(
        "ix_databricks_compute_attempts_user_id",
        "databricks_compute_attempts",
        ["user_id"],
    )
    op.create_index(
        "ix_databricks_compute_attempts_status",
        "databricks_compute_attempts",
        ["status"],
    )
    op.create_index(
        "ix_databricks_compute_attempts_next_poll_at",
        "databricks_compute_attempts",
        ["next_poll_at"],
    )
    op.create_index(
        "ix_databricks_compute_attempts_due",
        "databricks_compute_attempts",
        ["status", "next_poll_at"],
    )

    op.create_table(
        "databricks_publications",
        sa.Column("run_id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("workspace_id", sa.String(length=64), nullable=False),
        sa.Column("connection_generation", sa.String(length=64), nullable=False),
        sa.Column("target_key", sa.String(length=64), nullable=False),
        sa.Column("active_target_key", sa.String(length=64), nullable=True),
        sa.Column("target_locator_json", sa.Text(), nullable=False),
        sa.Column("write_mode", sa.String(length=24), nullable=False),
        sa.Column("staging_name", sa.String(length=520), nullable=False, server_default=""),
        sa.Column("state", sa.String(length=32), nullable=False, server_default="preparing"),
        sa.Column("submission_id", sa.String(length=36), nullable=False, server_default=""),
        sa.Column("staged_rows", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("staged_bytes", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("submitted_at", sa.DateTime(), nullable=True),
        sa.Column("confirmed_at", sa.DateTime(), nullable=True),
        sa.Column("cleanup_updated_at", sa.DateTime(), nullable=True),
        sa.Column("recovery_token", sa.String(length=64), nullable=True),
        sa.Column("recovery_expires_at", sa.DateTime(), nullable=True),
        sa.Column("last_error_code", sa.String(length=64), nullable=True),
        sa.Column("last_error_summary", sa.String(length=240), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["pipeline_runs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("run_id"),
        sa.UniqueConstraint("active_target_key", name="uq_databricks_publications_active_target"),
    )
    op.create_index("ix_databricks_publications_user_id", "databricks_publications", ["user_id"])
    op.create_index("ix_databricks_publications_state", "databricks_publications", ["state"])
    op.create_index(
        "ix_databricks_publications_recovery",
        "databricks_publications",
        ["state", "updated_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_databricks_publications_recovery", table_name="databricks_publications")
    op.drop_index("ix_databricks_publications_state", table_name="databricks_publications")
    op.drop_index("ix_databricks_publications_user_id", table_name="databricks_publications")
    op.drop_table("databricks_publications")
    op.drop_index("ix_databricks_compute_attempts_due", table_name="databricks_compute_attempts")
    op.drop_index(
        "ix_databricks_compute_attempts_next_poll_at", table_name="databricks_compute_attempts"
    )
    op.drop_index("ix_databricks_compute_attempts_status", table_name="databricks_compute_attempts")
    op.drop_index(
        "ix_databricks_compute_attempts_user_id", table_name="databricks_compute_attempts"
    )
    op.drop_table("databricks_compute_attempts")
