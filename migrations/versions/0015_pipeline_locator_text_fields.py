"""Allow flexible pipeline locator display values."""

import sqlalchemy as sa
from alembic import op

revision = "0015_pipeline_locator_text"
down_revision = "0014_foundry_datasets"
branch_labels = None
depends_on = None


_PIPELINE_COLUMNS = (
    "source_dataset",
    "source_schema",
    "source_table",
    "destination_schema",
    "destination_table",
)


def upgrade() -> None:
    with op.batch_alter_table("pipeline_definitions") as batch:
        for column_name in _PIPELINE_COLUMNS:
            batch.alter_column(
                column_name,
                existing_type=sa.String(length=80),
                type_=sa.Text(),
                existing_nullable=False,
            )


def downgrade() -> None:
    bind = op.get_bind()
    too_long = bind.execute(
        sa.text(
            """
            SELECT COUNT(*)
            FROM pipeline_definitions
            WHERE length(source_dataset) > 80
               OR length(source_schema) > 80
               OR length(source_table) > 80
               OR length(destination_schema) > 80
               OR length(destination_table) > 80
            """
        )
    ).scalar_one()
    if too_long:
        raise RuntimeError(
            "Cannot downgrade pipeline locator fields while values exceed 80 characters."
        )
    with op.batch_alter_table("pipeline_definitions") as batch:
        for column_name in _PIPELINE_COLUMNS:
            batch.alter_column(
                column_name,
                existing_type=sa.Text(),
                type_=sa.String(length=80),
                existing_nullable=False,
            )
