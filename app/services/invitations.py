"""Administrator invitations and acceptance."""

from __future__ import annotations

from datetime import timedelta
from typing import Any, cast

from fastapi import Request
from sqlalchemy import select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import Invitation, Role, User, UserStatus, utcnow
from app.security.email import normalize_email
from app.security.passwords import PasswordService, validate_password
from app.security.tokens import hash_token, random_token
from app.services.audit import record_event
from app.services.auth_common import TokenFlowError, _lock_role
from app.services.links import public_url
from app.services.mailer import queue_email


def create_invitation(
    db: Session,
    settings: Settings,
    *,
    email: str,
    role_name: str,
    inviter: User,
    request: Request | None = None,
) -> tuple[Invitation, str]:
    canonical, original = normalize_email(email, settings)
    role = _lock_role(db, role_name)
    if db.scalar(select(User).where(User.email == canonical)):
        db.rollback()
        raise ValueError("An account already exists for that email address.")
    if not role:
        db.rollback()
        raise ValueError("Select a valid role.")
    now = utcnow()
    for prior in db.scalars(
        select(Invitation).where(
            Invitation.email == canonical,
            Invitation.accepted_at.is_(None),
            Invitation.revoked_at.is_(None),
        )
    ):
        prior.revoked_at = now
    raw_token = random_token()
    invitation = Invitation(
        email=canonical,
        email_original=original,
        token_hash=hash_token(raw_token, settings.session_pepper),
        role_name=role_name,
        invited_by_user_id=inviter.id,
        created_at=now,
        expires_at=now + timedelta(hours=48),
    )
    db.add(invitation)
    accept_url = public_url(
        settings,
        "/invitations/accept",
        query={"token": raw_token},
    )
    queue_email(
        db,
        original,
        f"Invitation to {settings.app_name}",
        "You have been invited to an approved government application.\n\n"
        f"Open this link on the approved network to continue:\n{accept_url}\n\n"
        "This invitation expires in 48 hours. If you did not expect it, contact the service desk.",
    )
    record_event(
        db,
        "invitation.created",
        request=request,
        actor=inviter,
        detail={"invitation_id": invitation.id, "role": role_name},
    )
    db.commit()
    return invitation, raw_token


def get_valid_invitation(db: Session, settings: Settings, raw_token: str) -> Invitation:
    invitation = db.scalar(
        select(Invitation).where(
            Invitation.token_hash == hash_token(raw_token, settings.session_pepper)
        )
    )
    now = utcnow()
    if (
        not invitation
        or invitation.accepted_at
        or invitation.revoked_at
        or invitation.expires_at <= now
    ):
        raise TokenFlowError("That invitation is invalid or expired.")
    return invitation


def revoke_invitation(
    db: Session,
    *,
    invitation: Invitation,
    administrator: User,
    request: Request | None = None,
) -> None:
    revoked = db.execute(
        update(Invitation)
        .where(
            Invitation.id == invitation.id,
            Invitation.accepted_at.is_(None),
            Invitation.revoked_at.is_(None),
        )
        .values(revoked_at=utcnow())
        .execution_options(synchronize_session=False)
    )
    if cast(CursorResult[Any], revoked).rowcount != 1:
        db.rollback()
        raise ValueError("Only a pending invitation can be revoked.")
    record_event(
        db,
        "invitation.revoked",
        request=request,
        actor=administrator,
        detail={"invitation_id": invitation.id},
    )
    db.commit()


def accept_invitation(
    db: Session,
    settings: Settings,
    *,
    raw_token: str,
    password: str,
    full_name: str,
    request: Request | None = None,
) -> User:
    invitation = get_valid_invitation(db, settings, raw_token)
    if db.scalar(select(User).where(User.email == invitation.email)):
        raise TokenFlowError("An account already exists for that email address.")
    validated = None
    if settings.authentication_mode == "local_password":
        validated = validate_password(
            password, email=invitation.email, blocklist_path=settings.password_blocklist_path
        )
    now = utcnow()
    role = db.scalar(select(Role).where(Role.name == invitation.role_name))
    if not role:
        raise TokenFlowError("The invitation role is no longer available.")
    consumed = db.execute(
        update(Invitation)
        .where(
            Invitation.id == invitation.id,
            Invitation.token_hash == hash_token(raw_token, settings.session_pepper),
            Invitation.accepted_at.is_(None),
            Invitation.revoked_at.is_(None),
            Invitation.expires_at > now,
        )
        .values(accepted_at=now)
        .execution_options(synchronize_session=False)
    )
    if cast(CursorResult[Any], consumed).rowcount != 1:
        db.rollback()
        raise TokenFlowError("That invitation is invalid or expired.")
    user = User(
        email=invitation.email,
        email_original=invitation.email_original,
        email_verified_at=now,
        full_name=full_name.strip()[:160],
        status=UserStatus.ACTIVE.value,
        password_hash=PasswordService(settings).hash(validated) if validated is not None else None,
        password_changed_at=now if validated is not None else None,
        roles=[role],
    )
    db.add(user)
    try:
        db.flush()
        record_event(db, "invitation.accepted", request=request, actor=user, target=user)
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise TokenFlowError("That invitation is invalid or expired.") from exc
    return user
