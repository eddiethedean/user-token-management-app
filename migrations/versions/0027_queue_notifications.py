"""Wake PostgreSQL workers when durable queue state changes."""

from alembic import op

revision = "0027_queue_notifications"
down_revision = "0026_token_expiry"
branch_labels = None
depends_on = None

_QUEUES = {
    "pipeline_runs": ("status", "next_attempt_at"),
    "databricks_compute_attempts": ("status", "next_poll_at", "active_key", "lease_expires_at"),
    "databricks_publications": ("state", "recovery_expires_at"),
}


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute(
        """
        CREATE FUNCTION datamover_queue_notify() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            PERFORM pg_catalog.pg_notify('datamover_queue_changed', '');
            RETURN NULL;
        END;
        $$
        """
    )
    for table, columns in _QUEUES.items():
        old = ", ".join(f"OLD.{column}" for column in columns)
        new = ", ".join(f"NEW.{column}" for column in columns)
        op.execute(
            f"CREATE TRIGGER dm_queue_insert AFTER INSERT ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION datamover_queue_notify()"
        )
        op.execute(
            f"CREATE TRIGGER dm_queue_update AFTER UPDATE OF {', '.join(columns)} ON {table} "
            f"FOR EACH ROW WHEN (({old}) IS DISTINCT FROM ({new})) "
            "EXECUTE FUNCTION datamover_queue_notify()"
        )
        # Deletes can remove pending deadlines. Retention deletes of completed
        # runs should not turn hourly maintenance into a queue scan.
        pending = (
            "OLD.status IN ('queued','validating','extracting','loading','verifying')"
            if table == "pipeline_runs"
            else "OLD.status = 'starting'"
            if table == "databricks_compute_attempts"
            else "OLD.state IN ('preparing','staging','cleanup_pending','publishing','uncertain')"
        )
        op.execute(
            f"CREATE TRIGGER dm_queue_delete AFTER DELETE ON {table} "
            f"FOR EACH ROW WHEN ({pending}) EXECUTE FUNCTION datamover_queue_notify()"
        )
    # A queued run belonging to a disabled owner becomes claimable on activation.
    op.execute(
        "CREATE TRIGGER dm_queue_owner AFTER UPDATE OF status ON users "
        "FOR EACH ROW WHEN (OLD.status IS DISTINCT FROM NEW.status) "
        "EXECUTE FUNCTION datamover_queue_notify()"
    )


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute("DROP TRIGGER dm_queue_owner ON users")
    for table in _QUEUES:
        for suffix in ("insert", "update", "delete"):
            op.execute(f"DROP TRIGGER dm_queue_{suffix} ON {table}")
    op.execute("DROP FUNCTION datamover_queue_notify()")
