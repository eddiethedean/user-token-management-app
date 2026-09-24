"""Track whether pipeline row counters were measured."""

import sqlalchemy as sa
from alembic import op

revision = "0019_pipeline_metric_provenance"
down_revision = "0018_pipeline_reconcile"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("pipeline_runs") as batch:
        batch.add_column(
            sa.Column(
                "source_rows_measured", sa.Boolean(), nullable=False, server_default=sa.false()
            )
        )
        batch.add_column(
            sa.Column(
                "loaded_rows_measured", sa.Boolean(), nullable=False, server_default=sa.false()
            )
        )

    pipeline_runs = sa.table(
        "pipeline_runs",
        sa.column("source_rows", sa.BigInteger()),
        sa.column("source_manifest_json", sa.Text()),
        sa.column("source_rows_measured", sa.Boolean()),
        sa.column("loaded_rows", sa.BigInteger()),
        sa.column("destination_manifest_json", sa.Text()),
        sa.column("loaded_rows_measured", sa.Boolean()),
    )
    connection = op.get_bind()
    connection.execute(
        sa.update(pipeline_runs)
        .where(
            sa.or_(
                pipeline_runs.c.source_rows > 0,
                pipeline_runs.c.source_manifest_json.is_not(None),
            )
        )
        .values(source_rows_measured=True)
    )
    connection.execute(
        sa.update(pipeline_runs)
        .where(
            sa.or_(
                pipeline_runs.c.loaded_rows > 0,
                pipeline_runs.c.destination_manifest_json.is_not(None),
            )
        )
        .values(loaded_rows_measured=True)
    )


def downgrade() -> None:
    with op.batch_alter_table("pipeline_runs") as batch:
        batch.drop_column("loaded_rows_measured")
        batch.drop_column("source_rows_measured")
