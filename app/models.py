from __future__ import annotations

import uuid
from datetime import UTC, datetime
from enum import StrEnum

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Table,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


def new_id() -> str:
    return str(uuid.uuid4())


def utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class UserStatus(StrEnum):
    ACTIVE = "active"
    DISABLED = "disabled"
    PENDING = "pending"


user_roles = Table(
    "user_roles",
    Base.metadata,
    Column("user_id", String(36), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
    Column("role_id", String(36), ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True),
)


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    email_original: Mapped[str] = mapped_column(String(320))
    email_verified_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    full_name: Mapped[str] = mapped_column(String(160), default="")
    organization: Mapped[str] = mapped_column(String(160), default="")
    job_title: Mapped[str] = mapped_column(String(160), default="")
    phone: Mapped[str] = mapped_column(String(40), default="")
    preferred_color_mode: Mapped[str] = mapped_column(
        String(10),
        default="dark",
        server_default="dark",
    )
    status: Mapped[str] = mapped_column(String(20), default=UserStatus.PENDING.value, index=True)
    password_hash: Mapped[str | None] = mapped_column(Text, nullable=True)
    password_changed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    failed_login_attempts: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    security_version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    roles: Mapped[list[Role]] = relationship(secondary=user_roles, back_populates="users")
    sessions: Mapped[list[RefreshSession]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )

    @property
    def is_active(self) -> bool:
        return self.status == UserStatus.ACTIVE.value

    @property
    def role_names(self) -> list[str]:
        return sorted(role.name for role in self.roles)


class Role(Base):
    __tablename__ = "roles"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    description: Mapped[str] = mapped_column(String(240), default="")
    users: Mapped[list[User]] = relationship(secondary=user_roles, back_populates="roles")


class Invitation(Base):
    __tablename__ = "invitations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    email: Mapped[str] = mapped_column(String(320), index=True)
    email_original: Mapped[str] = mapped_column(String(320))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    role_name: Mapped[str] = mapped_column(String(64), default="user")
    invited_by_user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    invited_by: Mapped[User] = relationship(foreign_keys=[invited_by_user_id])


class RegistrationVerification(Base):
    __tablename__ = "registration_verifications"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="CASCADE"))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    requested_ip: Mapped[str] = mapped_column(String(64), default="")

    user: Mapped[User] = relationship()


class PasswordReset(Base):
    __tablename__ = "password_resets"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="CASCADE"))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    requested_ip: Mapped[str] = mapped_column(String(64), default="")

    user: Mapped[User] = relationship()


class RefreshSession(Base):
    __tablename__ = "refresh_sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="CASCADE"))
    refresh_token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    csrf_token: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    idle_expires_at: Mapped[datetime] = mapped_column(DateTime)
    absolute_expires_at: Mapped[datetime] = mapped_column(DateTime)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    user_agent: Mapped[str] = mapped_column(String(500), default="")
    source_ip: Mapped[str] = mapped_column(String(64), default="")

    user: Mapped[User] = relationship(back_populates="sessions")


class RefreshTokenHistory(Base):
    __tablename__ = "refresh_token_history"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    session_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("refresh_sessions.id", ondelete="CASCADE"), index=True
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    consumed_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    session: Mapped[RefreshSession] = relationship()


class AuditEvent(Base):
    __tablename__ = "audit_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    occurred_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    actor_user_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    target_user_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    event_type: Mapped[str] = mapped_column(String(100), index=True)
    outcome: Mapped[str] = mapped_column(String(20), default="success")
    request_id: Mapped[str] = mapped_column(String(64), default="")
    source_ip: Mapped[str] = mapped_column(String(64), default="")
    detail: Mapped[str] = mapped_column(Text, default="")

    actor: Mapped[User | None] = relationship(foreign_keys=[actor_user_id])
    target: Mapped[User | None] = relationship(foreign_keys=[target_user_id])


class EmailOutbox(Base):
    __tablename__ = "email_outbox"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    recipient: Mapped[str] = mapped_column(String(320), index=True)
    subject: Mapped[str] = mapped_column(String(240))
    body_text: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    failed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str] = mapped_column(Text, default="")

    delivery_state: Mapped[EmailDeliveryState] = relationship(
        back_populates="message", cascade="all, delete-orphan", uselist=False
    )


class EmailDeliveryState(Base):
    __tablename__ = "email_delivery_state"

    message_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("email_outbox.id", ondelete="CASCADE"), primary_key=True
    )
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    claim_token: Mapped[str | None] = mapped_column(String(64), nullable=True, unique=True)
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    message: Mapped[EmailOutbox] = relationship(back_populates="delivery_state")


class RateLimitBucket(Base):
    __tablename__ = "rate_limit_buckets"

    scope: Mapped[str] = mapped_column(String(64), primary_key=True)
    key_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    window_started_at: Mapped[datetime] = mapped_column(DateTime, primary_key=True)
    count: Mapped[int] = mapped_column(Integer, default=1)
    expires_at: Mapped[datetime] = mapped_column(DateTime, index=True)


class UserSecret(Base):
    __tablename__ = "user_secrets"
    __table_args__ = (
        UniqueConstraint("user_id", "provider", name="uq_user_secrets_owner_provider"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    provider: Mapped[str] = mapped_column(String(32))
    ciphertext: Mapped[str] = mapped_column(Text)
    nonce: Mapped[str] = mapped_column(String(32))
    encrypted_data_key: Mapped[str] = mapped_column(Text)
    key_nonce: Mapped[str] = mapped_column(String(32))
    master_key_id: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    validation_status: Mapped[str] = mapped_column(String(20), default="untested")
    validated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    validation_message: Mapped[str] = mapped_column(String(240), default="")
    runtime_status: Mapped[str] = mapped_column(String(20), default="")
    runtime_updated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    user: Mapped[User] = relationship()


class PipelineUpload(Base):
    __tablename__ = "pipeline_uploads"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    filename: Mapped[str] = mapped_column(String(180))
    content_type: Mapped[str] = mapped_column(String(100), default="text/csv")
    size_bytes: Mapped[int] = mapped_column(Integer)
    row_count: Mapped[int] = mapped_column(Integer)
    column_count: Mapped[int] = mapped_column(Integer)
    columns_json: Mapped[str] = mapped_column(Text)
    checksum_sha256: Mapped[str] = mapped_column(String(64))
    content: Mapped[bytes] = mapped_column(LargeBinary)
    storage_key: Mapped[str | None] = mapped_column(String(240), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    user: Mapped[User] = relationship()


class PipelineDefinition(Base):
    __tablename__ = "pipeline_definitions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(120))
    source_provider: Mapped[str] = mapped_column(String(32))
    source_dataset: Mapped[str] = mapped_column(String(80))
    source_schema: Mapped[str] = mapped_column(String(80), default="")
    source_table: Mapped[str] = mapped_column(String(80), default="")
    source_upload_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("pipeline_uploads.id", ondelete="SET NULL"), nullable=True
    )
    destination_provider: Mapped[str] = mapped_column(String(32))
    destination_schema: Mapped[str] = mapped_column(String(80), default="")
    destination_table: Mapped[str] = mapped_column(String(80), default="")
    destination_create: Mapped[bool] = mapped_column(Boolean, default=False)
    write_mode: Mapped[str] = mapped_column(String(32))
    definition_version: Mapped[int] = mapped_column(Integer, default=2)
    source_locator_json: Mapped[str] = mapped_column(Text, default="")
    destination_locator_json: Mapped[str] = mapped_column(Text, default="")
    write_policy_json: Mapped[str] = mapped_column(Text, default="")
    source_schema_snapshot_json: Mapped[str] = mapped_column(Text, default="")
    destination_schema_snapshot_json: Mapped[str] = mapped_column(Text, default="")
    legacy_unsupported: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    user: Mapped[User] = relationship()
    source_upload: Mapped[PipelineUpload | None] = relationship()
    runs: Mapped[list[PipelineRun]] = relationship(back_populates="pipeline")


class PipelineRunStatus(StrEnum):
    QUEUED = "queued"
    VALIDATING = "validating"
    EXTRACTING = "extracting"
    LOADING = "loading"
    VERIFYING = "verifying"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    FAILED_NEEDS_RECONCILIATION = "failed_needs_reconciliation"


class PipelineRun(Base):
    __tablename__ = "pipeline_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    pipeline_definition_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("pipeline_definitions.id", ondelete="SET NULL"), nullable=True
    )
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    definition_snapshot_json: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(
        String(40), default=PipelineRunStatus.QUEUED.value, index=True
    )
    stage: Mapped[str] = mapped_column(String(40), default="queued")
    attempt: Mapped[int] = mapped_column(Integer, default=1)
    queued_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    cancel_requested_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    worker_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    lease_token: Mapped[str | None] = mapped_column(String(64), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    source_rows: Mapped[int] = mapped_column(BigInteger, default=0)
    source_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    loaded_rows: Mapped[int] = mapped_column(BigInteger, default=0)
    loaded_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    source_manifest_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    destination_manifest_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    verification_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_summary: Mapped[str | None] = mapped_column(String(500), nullable=True)
    retryable: Mapped[bool] = mapped_column(Boolean, default=False)
    idempotency_token: Mapped[str | None] = mapped_column(String(64), nullable=True)
    parent_run_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("pipeline_runs.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    user: Mapped[User] = relationship()
    pipeline: Mapped[PipelineDefinition | None] = relationship(back_populates="runs")
    events: Mapped[list[PipelineRunEvent]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )


class PipelineRunEvent(Base):
    __tablename__ = "pipeline_run_events"
    __table_args__ = (UniqueConstraint("run_id", "sequence", name="uq_pipeline_run_events_seq"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    run_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("pipeline_runs.id", ondelete="CASCADE"), index=True
    )
    sequence: Mapped[int] = mapped_column(Integer)
    occurred_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    level: Mapped[str] = mapped_column(String(20), default="info")
    stage: Mapped[str] = mapped_column(String(40), default="")
    message: Mapped[str] = mapped_column(String(500))
    detail_json: Mapped[str] = mapped_column(Text, default="")

    run: Mapped[PipelineRun] = relationship(back_populates="events")


class PipelineCatalogCache(Base):
    __tablename__ = "pipeline_catalog_cache"
    __table_args__ = (
        UniqueConstraint(
            "user_id", "provider", "namespace", name="uq_pipeline_catalog_cache_scope"
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    provider: Mapped[str] = mapped_column(String(32))
    namespace: Mapped[str] = mapped_column(String(240), default="")
    payload_json: Mapped[str] = mapped_column(Text)
    fetched_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime, index=True)

    user: Mapped[User] = relationship()


class ApiTokenKeyUsage(Base):
    __tablename__ = "api_token_key_usage"

    key_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    wrap_count: Mapped[int] = mapped_column(BigInteger, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


Index("ix_sessions_user_active", RefreshSession.user_id, RefreshSession.revoked_at)
Index(
    "ix_pipeline_definitions_user_updated",
    PipelineDefinition.user_id,
    PipelineDefinition.updated_at,
)
Index(
    "ix_pipeline_uploads_user_created",
    PipelineUpload.user_id,
    PipelineUpload.created_at,
)
Index(
    "ix_registration_verifications_user_active",
    RegistrationVerification.user_id,
    RegistrationVerification.used_at,
)
Index(
    "ix_invitations_email_active", Invitation.email, Invitation.accepted_at, Invitation.revoked_at
)
Index(
    "ix_pipeline_runs_user_updated",
    PipelineRun.user_id,
    PipelineRun.updated_at,
)
Index(
    "ix_pipeline_runs_status_lease",
    PipelineRun.status,
    PipelineRun.lease_expires_at,
)
Index(
    "ix_pipeline_runs_pipeline_created",
    PipelineRun.pipeline_definition_id,
    PipelineRun.created_at,
)
Index(
    "ix_pipeline_runs_idempotency",
    PipelineRun.user_id,
    PipelineRun.idempotency_token,
    unique=True,
)
