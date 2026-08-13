"""Add connection health and schema-aware pipeline objects."""

import sqlalchemy as sa
from alembic import op

revision = "0008_catalogs_health"
down_revision = "0007_saved_pipelines"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("user_secrets") as batch:
        batch.add_column(
            sa.Column(
                "validation_status",
                sa.String(length=20),
                server_default="untested",
                nullable=False,
            )
        )
        batch.add_column(sa.Column("validated_at", sa.DateTime(), nullable=True))
        batch.add_column(
            sa.Column(
                "validation_message",
                sa.String(length=240),
                server_default="",
                nullable=False,
            )
        )
        batch.add_column(
            sa.Column(
                "runtime_status",
                sa.String(length=20),
                server_default="",
                nullable=False,
            )
        )
        batch.add_column(sa.Column("runtime_updated_at", sa.DateTime(), nullable=True))

    with op.batch_alter_table("pipeline_definitions") as batch:
        batch.add_column(
            sa.Column(
                "source_schema",
                sa.String(length=80),
                server_default="operations",
                nullable=False,
            )
        )
        batch.add_column(
            sa.Column(
                "source_table",
                sa.String(length=80),
                server_default="readiness_events",
                nullable=False,
            )
        )
        batch.add_column(
            sa.Column(
                "destination_schema",
                sa.String(length=80),
                server_default="analytics",
                nullable=False,
            )
        )
        batch.add_column(
            sa.Column(
                "destination_table",
                sa.String(length=80),
                server_default="readiness_events",
                nullable=False,
            )
        )
        batch.add_column(
            sa.Column(
                "destination_create",
                sa.Boolean(),
                server_default=sa.false(),
                nullable=False,
            )
        )

    pipelines = sa.table(
        "pipeline_definitions",
        sa.column("source_dataset", sa.String(length=80)),
        sa.column("source_table", sa.String(length=80)),
        sa.column("destination_table", sa.String(length=80)),
    )
    op.execute(
        pipelines.update().values(
            source_table=pipelines.c.source_dataset,
            destination_table=pipelines.c.source_dataset,
        )
    )


def downgrade() -> None:
    with op.batch_alter_table("pipeline_definitions") as batch:
        batch.drop_column("destination_create")
        batch.drop_column("destination_table")
        batch.drop_column("destination_schema")
        batch.drop_column("source_table")
        batch.drop_column("source_schema")

    with op.batch_alter_table("user_secrets") as batch:
        batch.drop_column("runtime_updated_at")
        batch.drop_column("runtime_status")
        batch.drop_column("validation_message")
        batch.drop_column("validated_at")
        batch.drop_column("validation_status")
