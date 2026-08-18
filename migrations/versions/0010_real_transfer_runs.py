"""Add versioned pipeline locators, durable runs, and catalog cache."""

import sqlalchemy as sa
from alembic import op

revision = "0010_real_transfer_runs"
down_revision = "0009_csv_sources"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("pipeline_uploads") as batch:
        batch.add_column(sa.Column("storage_key", sa.String(length=240), nullable=True))

    with op.batch_alter_table("pipeline_definitions") as batch:
        batch.add_column(
            sa.Column("definition_version", sa.Integer(), server_default="2", nullable=False)
        )
        batch.add_column(
            sa.Column("source_locator_json", sa.Text(), server_default="", nullable=False)
        )
        batch.add_column(
            sa.Column("destination_locator_json", sa.Text(), server_default="", nullable=False)
        )
        batch.add_column(
            sa.Column("write_policy_json", sa.Text(), server_default="", nullable=False)
        )
        batch.add_column(
            sa.Column("source_schema_snapshot_json", sa.Text(), server_default="", nullable=False)
        )
        batch.add_column(
            sa.Column(
                "destination_schema_snapshot_json", sa.Text(), server_default="", nullable=False
            )
        )
        batch.add_column(
            sa.Column("legacy_unsupported", sa.Boolean(), server_default=sa.false(), nullable=False)
        )

    op.create_table(
        "pipeline_runs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("pipeline_definition_id", sa.String(length=36), nullable=True),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("definition_snapshot_json", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("stage", sa.String(length=40), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("queued_at", sa.DateTime(), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("cancel_requested_at", sa.DateTime(), nullable=True),
        sa.Column("worker_id", sa.String(length=120), nullable=True),
        sa.Column("lease_token", sa.String(length=64), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(), nullable=True),
        sa.Column("heartbeat_at", sa.DateTime(), nullable=True),
        sa.Column("source_rows", sa.BigInteger(), nullable=False),
        sa.Column("source_bytes", sa.BigInteger(), nullable=False),
        sa.Column("loaded_rows", sa.BigInteger(), nullable=False),
        sa.Column("loaded_bytes", sa.BigInteger(), nullable=False),
        sa.Column("source_manifest_json", sa.Text(), nullable=True),
        sa.Column("destination_manifest_json", sa.Text(), nullable=True),
        sa.Column("verification_json", sa.Text(), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("error_summary", sa.String(length=500), nullable=True),
        sa.Column("retryable", sa.Boolean(), nullable=False),
        sa.Column("idempotency_token", sa.String(length=64), nullable=True),
        sa.Column("parent_run_id", sa.String(length=36), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["pipeline_definition_id"], ["pipeline_definitions.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["parent_run_id"], ["pipeline_runs.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_pipeline_runs_user_id"), "pipeline_runs", ["user_id"], unique=False)
    op.create_index(op.f("ix_pipeline_runs_status"), "pipeline_runs", ["status"], unique=False)
    op.create_index(
        op.f("ix_pipeline_runs_lease_expires_at"),
        "pipeline_runs",
        ["lease_expires_at"],
        unique=False,
    )
    op.create_index(
        "ix_pipeline_runs_user_updated", "pipeline_runs", ["user_id", "updated_at"], unique=False
    )
    op.create_index(
        "ix_pipeline_runs_status_lease",
        "pipeline_runs",
        ["status", "lease_expires_at"],
        unique=False,
    )
    op.create_index(
        "ix_pipeline_runs_pipeline_created",
        "pipeline_runs",
        ["pipeline_definition_id", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_pipeline_runs_idempotency",
        "pipeline_runs",
        ["user_id", "idempotency_token"],
        unique=True,
    )

    op.create_table(
        "pipeline_run_events",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(), nullable=False),
        sa.Column("level", sa.String(length=20), nullable=False),
        sa.Column("stage", sa.String(length=40), nullable=False),
        sa.Column("message", sa.String(length=500), nullable=False),
        sa.Column("detail_json", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["pipeline_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id", "sequence", name="uq_pipeline_run_events_seq"),
    )
    op.create_index(
        op.f("ix_pipeline_run_events_run_id"), "pipeline_run_events", ["run_id"], unique=False
    )

    op.create_table(
        "pipeline_catalog_cache",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("namespace", sa.String(length=240), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("fetched_at", sa.DateTime(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "user_id", "provider", "namespace", name="uq_pipeline_catalog_cache_scope"
        ),
    )
    op.create_index(
        op.f("ix_pipeline_catalog_cache_user_id"),
        "pipeline_catalog_cache",
        ["user_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_pipeline_catalog_cache_expires_at"),
        "pipeline_catalog_cache",
        ["expires_at"],
        unique=False,
    )

    definitions = sa.table(
        "pipeline_definitions",
        sa.column("id", sa.String(length=36)),
        sa.column("source_provider", sa.String(length=32)),
        sa.column("destination_provider", sa.String(length=32)),
        sa.column("legacy_unsupported", sa.Boolean()),
    )
    op.execute(
        definitions.update()
        .where(definitions.c.source_provider.in_(("advana", "mongodb")))
        .values(legacy_unsupported=True)
    )
    op.execute(
        definitions.update()
        .where(definitions.c.destination_provider.in_(("advana", "mongodb")))
        .values(legacy_unsupported=True)
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_pipeline_catalog_cache_expires_at"), table_name="pipeline_catalog_cache"
    )
    op.drop_index(op.f("ix_pipeline_catalog_cache_user_id"), table_name="pipeline_catalog_cache")
    op.drop_table("pipeline_catalog_cache")
    op.drop_index(op.f("ix_pipeline_run_events_run_id"), table_name="pipeline_run_events")
    op.drop_table("pipeline_run_events")
    op.drop_index("ix_pipeline_runs_idempotency", table_name="pipeline_runs")
    op.drop_index("ix_pipeline_runs_pipeline_created", table_name="pipeline_runs")
    op.drop_index("ix_pipeline_runs_status_lease", table_name="pipeline_runs")
    op.drop_index("ix_pipeline_runs_user_updated", table_name="pipeline_runs")
    op.drop_index(op.f("ix_pipeline_runs_lease_expires_at"), table_name="pipeline_runs")
    op.drop_index(op.f("ix_pipeline_runs_status"), table_name="pipeline_runs")
    op.drop_index(op.f("ix_pipeline_runs_user_id"), table_name="pipeline_runs")
    op.drop_table("pipeline_runs")
    with op.batch_alter_table("pipeline_definitions") as batch:
        batch.drop_column("legacy_unsupported")
        batch.drop_column("destination_schema_snapshot_json")
        batch.drop_column("source_schema_snapshot_json")
        batch.drop_column("write_policy_json")
        batch.drop_column("destination_locator_json")
        batch.drop_column("source_locator_json")
        batch.drop_column("definition_version")
    with op.batch_alter_table("pipeline_uploads") as batch:
        batch.drop_column("storage_key")
