"""Persist administrator live connection diagnostic history."""

import sqlalchemy as sa
from alembic import op

revision = "0028_live_diagnostic_runs"
down_revision = "0027_queue_notifications"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "live_diagnostic_runs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("app_version", sa.String(length=32), nullable=False, server_default=""),
        sa.Column("status", sa.String(length=24), nullable=False, server_default="queued"),
        sa.Column("worker_token", sa.String(length=36), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(), nullable=True),
        sa.Column("mode", sa.String(length=16), nullable=False, server_default="live"),
        sa.Column("checks_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_live_diagnostic_runs_user_id", "live_diagnostic_runs", ["user_id"])
    op.create_index("ix_live_diagnostic_runs_status", "live_diagnostic_runs", ["status"])
    op.create_index("ix_live_diagnostic_runs_created_at", "live_diagnostic_runs", ["created_at"])
    op.create_index(
        "ix_live_diagnostic_runs_owner_created",
        "live_diagnostic_runs",
        ["user_id", "created_at"],
    )
    op.create_index(
        "ix_live_diagnostic_runs_lease_expires_at",
        "live_diagnostic_runs",
        ["lease_expires_at"],
    )
    op.create_index(
        "uq_live_diagnostic_runs_one_active_per_user",
        "live_diagnostic_runs",
        ["user_id"],
        unique=True,
        sqlite_where=sa.text("status IN ('queued', 'running')"),
        postgresql_where=sa.text("status IN ('queued', 'running')"),
    )


def downgrade() -> None:
    op.drop_index("uq_live_diagnostic_runs_one_active_per_user", table_name="live_diagnostic_runs")
    op.drop_index("ix_live_diagnostic_runs_lease_expires_at", table_name="live_diagnostic_runs")
    op.drop_index("ix_live_diagnostic_runs_owner_created", table_name="live_diagnostic_runs")
    op.drop_index("ix_live_diagnostic_runs_created_at", table_name="live_diagnostic_runs")
    op.drop_index("ix_live_diagnostic_runs_status", table_name="live_diagnostic_runs")
    op.drop_index("ix_live_diagnostic_runs_user_id", table_name="live_diagnostic_runs")
    op.drop_table("live_diagnostic_runs")
