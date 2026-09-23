"""Persist authoritative pipeline safety and reconciliation state."""

import json

import sqlalchemy as sa
from alembic import op

revision = "0018_pipeline_reconcile"
down_revision = "0017_connection_feedback"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("pipeline_runs") as batch:
        batch.add_column(sa.Column("last_safe_stage", sa.String(length=40), nullable=True))
        batch.add_column(sa.Column("data_impact", sa.String(length=32), nullable=True))
        batch.add_column(
            sa.Column(
                "reconciliation_required", sa.Boolean(), nullable=False, server_default=sa.false()
            )
        )
        batch.add_column(sa.Column("reconciliation_reviewed_at", sa.DateTime(), nullable=True))
    # Older runs stored these safety facts only in verification_json.  Read
    # those facts in Python so this migration works consistently on SQLite and
    # PostgreSQL and so malformed legacy JSON is handled conservatively.
    pipeline_runs = sa.table(
        "pipeline_runs",
        sa.column("id", sa.String()),
        sa.column("status", sa.String()),
        sa.column("stage", sa.String()),
        sa.column("loaded_rows", sa.BigInteger()),
        sa.column("error_code", sa.String()),
        sa.column("verification_json", sa.Text()),
    )
    connection = op.get_bind()
    terminal_statuses = ("failed", "cancelled", "failed_needs_reconciliation")
    rows = connection.execute(
        sa.select(
            pipeline_runs.c.id,
            pipeline_runs.c.status,
            pipeline_runs.c.stage,
            pipeline_runs.c.loaded_rows,
            pipeline_runs.c.error_code,
            pipeline_runs.c.verification_json,
        ).where(pipeline_runs.c.status.in_(terminal_statuses))
    ).mappings()
    uncertain_codes = {
        "partial_write",
        "publish_uncertain",
        "verification_failed",
        "worker_lost",
    }
    known_safe_stages = {"authenticate", "inspect", "transfer", "verify"}
    for row in rows:
        facts_valid = True
        try:
            facts = json.loads(row["verification_json"] or "{}")
        except (TypeError, ValueError):
            facts = {}
            facts_valid = False
        if not isinstance(facts, dict):
            facts = {}
            facts_valid = False
        loaded_rows = int(row["loaded_rows"] or 0)
        status = str(row["status"] or "")
        error_code = str(
            row["error_code"] or facts.get("error_code") or facts.get("failure_code") or ""
        )
        fact_impact = str(facts.get("data_impact") or "").casefold()
        fact_reconciliation = str(facts.get("reconciliation_required") or "").casefold()
        safe_stage = str(facts.get("last_safe_stage") or "")
        if safe_stage not in known_safe_stages:
            safe_stage = str(row["stage"] or "")
        if safe_stage not in known_safe_stages:
            safe_stage = "transfer" if loaded_rows else "unknown"

        needs_reconciliation = (
            not facts_valid
            or status == "failed_needs_reconciliation"
            or fact_reconciliation in {"true", "1", "yes"}
            or fact_impact == "uncertain"
            or error_code in uncertain_codes
            or loaded_rows > 0
            or safe_stage in {"transfer", "verify"}
            or safe_stage == "unknown"
        )
        impact = "uncertain" if needs_reconciliation else "unchanged"
        connection.execute(
            sa.text(
                "UPDATE pipeline_runs SET last_safe_stage = :last_safe_stage, "
                "data_impact = :data_impact, reconciliation_required = :reconciliation_required "
                "WHERE id = :id"
            ),
            {
                "id": row["id"],
                "last_safe_stage": safe_stage,
                "data_impact": impact,
                "reconciliation_required": needs_reconciliation,
            },
        )


def downgrade() -> None:
    with op.batch_alter_table("pipeline_runs") as batch:
        batch.drop_column("reconciliation_reviewed_at")
        batch.drop_column("reconciliation_required")
        batch.drop_column("data_impact")
        batch.drop_column("last_safe_stage")
