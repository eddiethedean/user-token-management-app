"""Persist pipeline guardrail decisions and run findings."""

import json

import sqlalchemy as sa
from alembic import op

revision = "0021_sensitive_data_guardrails"
down_revision = "0020_pipeline_metric_provenance"
branch_labels = None
depends_on = None


def _strip_schema_examples(value: object) -> tuple[object, bool]:
    if not isinstance(value, dict):
        return value, False
    changed = False
    for schema_key in ("schema", "schema_before"):
        schema = value.get(schema_key)
        if not isinstance(schema, dict):
            continue
        columns = schema.get("columns")
        if not isinstance(columns, list):
            continue
        for column in columns:
            if isinstance(column, dict) and "example" in column:
                column.pop("example", None)
                changed = True
    return value, changed


def _scrub_persisted_schema_examples() -> None:
    connection = op.get_bind()
    pipeline_runs = sa.table(
        "pipeline_runs",
        sa.column("id", sa.String()),
        sa.column("source_manifest_json", sa.Text()),
        sa.column("destination_manifest_json", sa.Text()),
    )
    for row in connection.execute(
        sa.select(
            pipeline_runs.c.id,
            pipeline_runs.c.source_manifest_json,
            pipeline_runs.c.destination_manifest_json,
        )
    ).mappings():
        updates = {}
        for column_name in ("source_manifest_json", "destination_manifest_json"):
            raw = row[column_name]
            try:
                payload = json.loads(raw) if raw else None
            except (TypeError, ValueError):
                continue
            sanitized, changed = _strip_schema_examples(payload)
            if changed:
                updates[column_name] = json.dumps(sanitized, separators=(",", ":"))
        if updates:
            connection.execute(
                pipeline_runs.update()
                .where(pipeline_runs.c.id == row["id"])
                .values(**updates)
            )

    cache = sa.table(
        "pipeline_catalog_cache",
        sa.column("id", sa.String()),
        sa.column("namespace", sa.Text()),
        sa.column("payload_json", sa.Text()),
    )
    for row in connection.execute(
        sa.select(cache.c.id, cache.c.payload_json).where(cache.c.namespace.like("schema:%"))
    ).mappings():
        try:
            payload = json.loads(row["payload_json"] or "{}")
        except (TypeError, ValueError):
            continue
        if not isinstance(payload, dict) or not isinstance(payload.get("columns"), list):
            continue
        changed = False
        for column in payload["columns"]:
            if isinstance(column, dict) and "example" in column:
                column.pop("example", None)
                changed = True
        if changed:
            connection.execute(
                cache.update()
                .where(cache.c.id == row["id"])
                .values(payload_json=json.dumps(payload, separators=(",", ":")))
            )


def upgrade() -> None:
    with op.batch_alter_table("pipeline_definitions") as batch:
        batch.add_column(
            sa.Column("guardrail_actions_json", sa.Text(), nullable=False, server_default="[]")
        )
    with op.batch_alter_table("pipeline_runs") as batch:
        batch.add_column(sa.Column("guardrail_json", sa.Text(), nullable=True))
    _scrub_persisted_schema_examples()


def downgrade() -> None:
    with op.batch_alter_table("pipeline_runs") as batch:
        batch.drop_column("guardrail_json")
    with op.batch_alter_table("pipeline_definitions") as batch:
        batch.drop_column("guardrail_actions_json")
