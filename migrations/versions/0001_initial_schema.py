"""Create the original application schema."""

import sqlalchemy as sa
from alembic import op

revision = "0001_initial_schema"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "roles",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("description", sa.String(length=240), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_roles_name"), "roles", ["name"], unique=True)
    op.create_table(
        "users",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("email_original", sa.String(length=320), nullable=False),
        sa.Column("email_verified_at", sa.DateTime(), nullable=True),
        sa.Column("full_name", sa.String(length=160), nullable=False),
        sa.Column("organization", sa.String(length=160), nullable=False),
        sa.Column("job_title", sa.String(length=160), nullable=False),
        sa.Column("phone", sa.String(length=40), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=True),
        sa.Column("password_changed_at", sa.DateTime(), nullable=True),
        sa.Column("failed_login_attempts", sa.Integer(), nullable=False),
        sa.Column("locked_until", sa.DateTime(), nullable=True),
        sa.Column("security_version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("last_login_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_users_email"), "users", ["email"], unique=True)
    op.create_index(op.f("ix_users_status"), "users", ["status"], unique=False)
    op.create_table(
        "user_roles",
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("role_id", sa.String(length=36), nullable=False),
        sa.ForeignKeyConstraint(["role_id"], ["roles.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id", "role_id"),
    )
    op.create_table(
        "invitations",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("email_original", sa.String(length=320), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("role_name", sa.String(length=64), nullable=False),
        sa.Column("invited_by_user_id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("accepted_at", sa.DateTime(), nullable=True),
        sa.Column("revoked_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["invited_by_user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_invitations_email_active",
        "invitations",
        ["email", "accepted_at", "revoked_at"],
        unique=False,
    )
    op.create_index(op.f("ix_invitations_email"), "invitations", ["email"], unique=False)
    op.create_index(op.f("ix_invitations_token_hash"), "invitations", ["token_hash"], unique=True)
    op.create_table(
        "password_resets",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("used_at", sa.DateTime(), nullable=True),
        sa.Column("requested_ip", sa.String(length=64), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_password_resets_token_hash"),
        "password_resets",
        ["token_hash"],
        unique=True,
    )
    op.create_table(
        "refresh_sessions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("refresh_token_hash", sa.String(length=64), nullable=False),
        sa.Column("csrf_token", sa.String(length=128), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(), nullable=False),
        sa.Column("idle_expires_at", sa.DateTime(), nullable=False),
        sa.Column("absolute_expires_at", sa.DateTime(), nullable=False),
        sa.Column("revoked_at", sa.DateTime(), nullable=True),
        sa.Column("user_agent", sa.String(length=500), nullable=False),
        sa.Column("source_ip", sa.String(length=64), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_refresh_sessions_refresh_token_hash"),
        "refresh_sessions",
        ["refresh_token_hash"],
        unique=True,
    )
    op.create_index(
        "ix_sessions_user_active",
        "refresh_sessions",
        ["user_id", "revoked_at"],
        unique=False,
    )
    op.create_table(
        "audit_events",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("occurred_at", sa.DateTime(), nullable=False),
        sa.Column("actor_user_id", sa.String(length=36), nullable=True),
        sa.Column("target_user_id", sa.String(length=36), nullable=True),
        sa.Column("event_type", sa.String(length=100), nullable=False),
        sa.Column("outcome", sa.String(length=20), nullable=False),
        sa.Column("request_id", sa.String(length=64), nullable=False),
        sa.Column("source_ip", sa.String(length=64), nullable=False),
        sa.Column("detail", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["target_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_audit_events_event_type"), "audit_events", ["event_type"], unique=False
    )
    op.create_index(
        op.f("ix_audit_events_occurred_at"), "audit_events", ["occurred_at"], unique=False
    )
    op.create_table(
        "email_outbox",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("recipient", sa.String(length=320), nullable=False),
        sa.Column("subject", sa.String(length=240), nullable=False),
        sa.Column("body_text", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("sent_at", sa.DateTime(), nullable=True),
        sa.Column("failed_at", sa.DateTime(), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_email_outbox_recipient"), "email_outbox", ["recipient"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_email_outbox_recipient"), table_name="email_outbox")
    op.drop_table("email_outbox")
    op.drop_index(op.f("ix_audit_events_occurred_at"), table_name="audit_events")
    op.drop_index(op.f("ix_audit_events_event_type"), table_name="audit_events")
    op.drop_table("audit_events")
    op.drop_index("ix_sessions_user_active", table_name="refresh_sessions")
    op.drop_index(op.f("ix_refresh_sessions_refresh_token_hash"), table_name="refresh_sessions")
    op.drop_table("refresh_sessions")
    op.drop_index(op.f("ix_password_resets_token_hash"), table_name="password_resets")
    op.drop_table("password_resets")
    op.drop_index(op.f("ix_invitations_token_hash"), table_name="invitations")
    op.drop_index(op.f("ix_invitations_email"), table_name="invitations")
    op.drop_index("ix_invitations_email_active", table_name="invitations")
    op.drop_table("invitations")
    op.drop_table("user_roles")
    op.drop_index(op.f("ix_users_status"), table_name="users")
    op.drop_index(op.f("ix_users_email"), table_name="users")
    op.drop_table("users")
    op.drop_index(op.f("ix_roles_name"), table_name="roles")
    op.drop_table("roles")
