"""Allocate pipeline run event sequences atomically."""

import sqlalchemy as sa
from alembic import op

revision = "0013_pipeline_event_sequence"
down_revision = "0012_dark_color_mode_default"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("pipeline_runs") as batch:
        batch.add_column(
            sa.Column("next_event_sequence", sa.Integer(), server_default="0", nullable=False)
        )
    op.execute(
        sa.text(
            """
            UPDATE pipeline_runs
            SET next_event_sequence = COALESCE(
                (
                    SELECT MAX(pipeline_run_events.sequence)
                    FROM pipeline_run_events
                    WHERE pipeline_run_events.run_id = pipeline_runs.id
                ),
                0
            )
            """
        )
    )


def downgrade() -> None:
    with op.batch_alter_table("pipeline_runs") as batch:
        batch.drop_column("next_event_sequence")
