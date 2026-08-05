"""Password reset request and completion."""

from __future__ import annotations

from datetime import timedelta
from typing import Any, cast

from fastapi import Request
from sqlalchemy import select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import PasswordReset, User, utcnow
from app.security.email import normalize_email
from app.security.passwords import PasswordService, validate_password
from app.security.tokens import hash_token, random_token
from app.services.audit import client_ip, record_event
from app.services.auth_common import TokenFlowError
from app.services.mailer import queue_email
from app.services.sessions import revoke_all_sessions


def request_password_reset(
    db: Session,
    settings: Settings,
    email: str,
    request: Request | None = None,
) -> None:
    try:
        canonical, _ = normalize_email(email, settings)
    except ValueError:
        return
    user = db.scalar(select(User).where(User.email == canonical).with_for_update())
    if not user or not user.is_active:
        return
    now = utcnow()
    for prior in db.scalars(
        select(PasswordReset).where(
            PasswordReset.user_id == user.id, PasswordReset.used_at.is_(None)
        )
    ):
        prior.used_at = now
    raw_token = random_token()
    reset = PasswordReset(
        user_id=user.id,
        token_hash=hash_token(raw_token, settings.session_pepper),
        created_at=now,
        expires_at=now + timedelta(minutes=30),
        requested_ip=client_ip(request),
    )
    db.add(reset)
    reset_url = f"{settings.public_base_url.rstrip('/')}/password/reset?token={raw_token}"
    queue_email(
        db,
        user.email_original,
        f"Reset your {settings.app_name} password",
        "A password reset was requested for your account.\n\n"
        f"Open this link on the approved network to continue:\n{reset_url}\n\n"
        "The link expires in 30 minutes. Opening it does not change your password. "
        "If you did not request this, contact the service desk.",
    )
    record_event(db, "password.reset.requested", request=request, target=user)
    db.commit()


def get_valid_password_reset(db: Session, settings: Settings, raw_token: str) -> PasswordReset:
    reset = db.scalar(
        select(PasswordReset).where(
            PasswordReset.token_hash == hash_token(raw_token, settings.session_pepper)
        )
    )
    if not reset or reset.used_at or reset.expires_at <= utcnow() or not reset.user.is_active:
        raise TokenFlowError("That password reset link is invalid or expired.")
    return reset


def complete_password_reset(
    db: Session,
    settings: Settings,
    *,
    raw_token: str,
    password: str,
    request: Request | None = None,
) -> User:
    reset = get_valid_password_reset(db, settings, raw_token)
    validated = validate_password(
        password, email=reset.user.email, blocklist_path=settings.password_blocklist_path
    )
    user = reset.user
    now = utcnow()
    consumed = db.execute(
        update(PasswordReset)
        .where(
            PasswordReset.id == reset.id,
            PasswordReset.token_hash == hash_token(raw_token, settings.session_pepper),
            PasswordReset.used_at.is_(None),
            PasswordReset.expires_at > now,
        )
        .values(used_at=now)
        .execution_options(synchronize_session=False)
    )
    if cast(CursorResult[Any], consumed).rowcount != 1:
        db.rollback()
        raise TokenFlowError("That password reset link is invalid or expired.")
    user.password_hash = PasswordService(settings).hash(validated)
    user.password_changed_at = now
    user.security_version += 1
    user.failed_login_attempts = 0
    user.locked_until = None
    db.execute(
        update(PasswordReset)
        .where(PasswordReset.user_id == user.id, PasswordReset.used_at.is_(None))
        .values(used_at=now)
    )
    revoke_all_sessions(db, user)
    record_event(db, "password.reset.completed", request=request, actor=user, target=user)
    queue_email(
        db,
        user.email_original,
        f"Your {settings.app_name} password was changed",
        "Your password was changed and existing sessions were revoked. "
        "If you did not perform this action, contact the service desk immediately.",
    )
    db.commit()
    return user
