from dataclasses import dataclass

from fastapi import Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import User, utcnow
from app.security.passwords import PasswordService, validate_password
from app.services.audit import record_event
from app.services.auth import revoke_all_sessions


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
