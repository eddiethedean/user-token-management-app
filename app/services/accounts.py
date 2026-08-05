from dataclasses import dataclass

from fastapi import Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import AuditEvent, RefreshSession, User, utcnow
from app.security.passwords import PasswordService, validate_password
from app.services.audit import record_event
from app.services.auth import revoke_all_sessions
from app.services.secrets import list_user_secrets


class CurrentPasswordError(ValueError):
    pass


@dataclass(frozen=True)
class ProfileValues:
    full_name: str = ""
    organization: str = ""
    job_title: str = ""
    phone: str = ""


def update_profile(
    db: Session,
    *,
    user: User,
    values: ProfileValues,
    request: Request | None = None,
) -> User:
    user.full_name = values.full_name.strip()[:160]
    user.organization = values.organization.strip()[:160]
    user.job_title = values.job_title.strip()[:160]
    user.phone = values.phone.strip()[:40]
    record_event(db, "profile.updated", request=request, actor=user, target=user)
    db.commit()
    return user


def change_password(
    db: Session,
    settings: Settings,
    *,
    user: User,
    current_password: str,
    new_password: str,
    request: Request | None = None,
) -> None:
    locked_user = db.scalar(
        select(User)
        .where(User.id == user.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if not locked_user:
        raise CurrentPasswordError("Current password is incorrect.")
    user = locked_user
    passwords = PasswordService(settings)
    if not passwords.verify(current_password, user.password_hash):
        raise CurrentPasswordError("Current password is incorrect.")
    validated = validate_password(
        new_password,
        email=user.email,
        blocklist_path=settings.password_blocklist_path,
    )
    user.password_hash = passwords.hash(validated)
    user.password_changed_at = utcnow()
    user.failed_login_attempts = 0
    user.locked_until = None
    user.security_version += 1
    revoke_all_sessions(db, user)
    record_event(db, "password.changed", request=request, actor=user, target=user)
    db.commit()


def security_page_values(db: Session, user: User, settings: Settings, **values) -> dict:
    """Assemble sessions, secrets, and recent activity for the security page."""
    now = utcnow()
    sessions = db.scalars(
        select(RefreshSession)
        .where(
            RefreshSession.user_id == user.id,
            RefreshSession.revoked_at.is_(None),
            RefreshSession.idle_expires_at > now,
            RefreshSession.absolute_expires_at > now,
        )
        .order_by(RefreshSession.last_seen_at.desc())
    ).all()
    events = db.scalars(
        select(AuditEvent)
        .where(AuditEvent.target_user_id == user.id)
        .order_by(AuditEvent.occurred_at.desc())
        .limit(12)
    ).all()
    return {
        "sessions": list(sessions),
        "secret_slots": list_user_secrets(db, user),
        "events": list(events),
        "local_password": settings.authentication_mode == "local_password",
        **values,
    }
