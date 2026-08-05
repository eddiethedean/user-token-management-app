"""Self-registration request, verification, and admin approval."""

from __future__ import annotations

from datetime import timedelta

from fastapi import Request
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import (
    Invitation,
    RegistrationVerification,
    User,
    UserStatus,
    utcnow,
)
from app.security.email import normalize_email
from app.security.passwords import PasswordService, validate_password
from app.security.tokens import hash_token, random_token
from app.services.audit import client_ip, record_event
from app.services.auth_common import TokenFlowError, _lock_role_catalog
from app.services.mailer import queue_email
from app.services.sessions import revoke_all_sessions


def request_self_registration(
    db: Session,
    settings: Settings,
    *,
    email: str,
    full_name: str,
    request: Request | None = None,
) -> None:
    canonical, original = normalize_email(email, settings)
    role = _lock_role_catalog(db).get("user")
    if not role:
        raise RuntimeError("The default user role is unavailable.")
    user = db.scalar(select(User).where(User.email == canonical))
    now = utcnow()

    if user:
        if user.status != UserStatus.PENDING.value or user.email_verified_at:
            return
        db.execute(
            update(Invitation)
            .where(
                Invitation.email == canonical,
                Invitation.accepted_at.is_(None),
                Invitation.revoked_at.is_(None),
            )
            .values(revoked_at=now)
        )
        recent = db.scalar(
            select(RegistrationVerification)
            .where(
                RegistrationVerification.user_id == user.id,
                RegistrationVerification.used_at.is_(None),
                RegistrationVerification.expires_at > now,
                RegistrationVerification.created_at > now - timedelta(minutes=10),
            )
            .order_by(RegistrationVerification.created_at.desc())
        )
        if recent:
            db.commit()
            return
    else:
        db.execute(
            update(Invitation)
            .where(
                Invitation.email == canonical,
                Invitation.accepted_at.is_(None),
                Invitation.revoked_at.is_(None),
            )
            .values(revoked_at=now)
        )
        user = User(
            email=canonical,
            email_original=original,
            full_name=full_name.strip()[:160],
            status=UserStatus.PENDING.value,
            roles=[role] if role else [],
        )
        db.add(user)
        db.flush()
        record_event(db, "registration.requested", request=request, target=user)

    db.execute(
        update(RegistrationVerification)
        .where(
            RegistrationVerification.user_id == user.id,
            RegistrationVerification.used_at.is_(None),
        )
        .values(used_at=now)
    )

    raw_token = random_token()
    verification = RegistrationVerification(
        user_id=user.id,
        token_hash=hash_token(raw_token, settings.session_pepper),
        created_at=now,
        expires_at=now + timedelta(hours=24),
        requested_ip=client_ip(request),
    )
    db.add(verification)
    verification_url = (
        f"{settings.public_base_url.rstrip('/')}/registration/verify?token={raw_token}"
    )
    queue_email(
        db,
        user.email_original,
        f"Verify your {settings.app_name} registration",
        "A request was made to register this government email address.\n\n"
        f"Open this link on the approved network to verify the address and choose a password:\n"
        f"{verification_url}\n\n"
        "The link expires in 24 hours. After verification, an administrator must approve the "
        "request before you can sign in. If you did not request this account, do not open the link "
        "and contact the service desk.",
    )
    db.commit()


def get_valid_registration_verification(
    db: Session, settings: Settings, raw_token: str
) -> RegistrationVerification:
    verification = db.scalar(
        select(RegistrationVerification).where(
            RegistrationVerification.token_hash == hash_token(raw_token, settings.session_pepper)
        )
    )
    if (
        not verification
        or verification.used_at
        or verification.expires_at <= utcnow()
        or verification.user.status != UserStatus.PENDING.value
        or verification.user.email_verified_at
    ):
        raise TokenFlowError("That registration verification link is invalid or expired.")
    return verification


def complete_self_registration(
    db: Session,
    settings: Settings,
    *,
    raw_token: str,
    password: str,
    request: Request | None = None,
) -> User:
    verification = get_valid_registration_verification(db, settings, raw_token)
    user = verification.user
    validated = None
    if settings.authentication_mode == "local_password":
        validated = validate_password(
            password, email=user.email, blocklist_path=settings.password_blocklist_path
        )
    now = utcnow()
    consumed = db.execute(
        update(RegistrationVerification)
        .where(
            RegistrationVerification.id == verification.id,
            RegistrationVerification.used_at.is_(None),
            RegistrationVerification.expires_at > now,
        )
        .values(used_at=now)
    )
    if consumed.rowcount != 1:
        db.rollback()
        raise TokenFlowError("That registration verification link is invalid or expired.")
    if validated is not None:
        user.password_hash = PasswordService(settings).hash(validated)
        user.password_changed_at = now
    user.email_verified_at = now
    db.execute(
        update(RegistrationVerification)
        .where(
            RegistrationVerification.user_id == user.id,
            RegistrationVerification.used_at.is_(None),
        )
        .values(used_at=now)
    )
    record_event(db, "registration.email_verified", request=request, actor=user, target=user)
    queue_email(
        db,
        user.email_original,
        f"Your {settings.app_name} request is awaiting approval",
        "Your government email address has been verified and your registration request is now "
        "awaiting administrator approval. You cannot sign in until an administrator approves it. "
        "You will receive another email when access is approved.",
    )
    db.commit()
    return user


def approve_self_registration(
    db: Session,
    settings: Settings,
    *,
    user: User,
    administrator: User,
    request: Request | None = None,
) -> None:
    if user.status != UserStatus.PENDING.value:
        raise ValueError("Only pending registrations can be approved.")
    password_ready = settings.authentication_mode != "local_password" or bool(user.password_hash)
    if not user.email_verified_at or not password_ready:
        raise ValueError("The user must verify their government email before approval.")
    activated = db.execute(
        update(User)
        .where(
            User.id == user.id,
            User.status == UserStatus.PENDING.value,
            User.email_verified_at.is_not(None),
        )
        .values(status=UserStatus.ACTIVE.value)
    )
    if activated.rowcount != 1:
        db.rollback()
        raise ValueError("This registration is no longer pending approval.")
    record_event(
        db,
        "registration.approved",
        request=request,
        actor=administrator,
        target=user,
    )
    queue_email(
        db,
        user.email_original,
        f"Your {settings.app_name} access was approved",
        (
            "An administrator approved your registration. You can now sign in using your "
            "government email address and the password you created."
            if settings.authentication_mode == "local_password"
            else "An administrator approved your registration. Continue through the approved "
            "identity-aware proxy using your federated credential."
        ),
    )
    db.commit()


def deny_self_registration(
    db: Session,
    settings: Settings,
    *,
    user: User,
    administrator: User,
    request: Request | None = None,
) -> None:
    if user.status != UserStatus.PENDING.value:
        raise ValueError("Only pending registrations can be denied.")
    denied = db.execute(
        update(User)
        .where(User.id == user.id, User.status == UserStatus.PENDING.value)
        .values(
            status=UserStatus.DISABLED.value,
            security_version=User.security_version + 1,
        )
    )
    if denied.rowcount != 1:
        db.rollback()
        raise ValueError("This registration is no longer pending approval.")
    revoke_all_sessions(db, user)
    record_event(
        db,
        "registration.denied",
        request=request,
        actor=administrator,
        target=user,
    )
    queue_email(
        db,
        user.email_original,
        f"Your {settings.app_name} registration was not approved",
        "An administrator did not approve your registration request. You cannot sign in. "
        "Contact the application service desk if you believe this decision was made in error.",
    )
    db.commit()
