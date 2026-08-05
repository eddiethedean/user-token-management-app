from dataclasses import dataclass
from datetime import timedelta

from fastapi import Request
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from access_registry.config import Settings
from access_registry.models import (
    Invitation,
    PasswordReset,
    RefreshSession,
    RefreshTokenHistory,
    RegistrationVerification,
    Role,
    User,
    UserStatus,
    utcnow,
)
from access_registry.security.client import is_trusted_direct_proxy
from access_registry.security.email import normalize_email
from access_registry.security.passwords import PasswordService, validate_password
from access_registry.security.tokens import create_access_token, hash_token, random_token
from access_registry.services.audit import client_ip, record_event
from access_registry.services.mailer import queue_email


class AuthenticationError(ValueError):
    pass


class AccountLockedError(AuthenticationError):
    pass


class TokenFlowError(ValueError):
    pass


class RegistrationPendingError(AuthenticationError):
    pass


@dataclass
class SessionTokens:
    access_token: str
    access_expires_in: int
    refresh_token: str
    session: RefreshSession


def ensure_default_roles(db: Session) -> None:
    defaults = {
        "user": "Standard account holder",
        "administrator": "Can manage users, invitations, roles, and audit records",
    }
    dialect = db.get_bind().dialect.name
    for name, description in defaults.items():
        values = {"name": name, "description": description}
        if dialect == "postgresql":
            statement = (
                postgresql_insert(Role)
                .values(**values)
                .on_conflict_do_nothing(index_elements=[Role.name])
            )
        elif dialect == "sqlite":
            statement = (
                sqlite_insert(Role)
                .values(**values)
                .on_conflict_do_nothing(index_elements=[Role.name])
            )
        else:
            if not db.scalar(select(Role.id).where(Role.name == name)):
                db.add(Role(**values))
            continue
        db.execute(statement)
    db.commit()


def _lock_role_catalog(db: Session) -> dict[str, Role]:
    """Serialize low-volume enrollment writes against stable role rows."""
    return {
        role.name: role
        for role in db.scalars(select(Role).order_by(Role.name).with_for_update()).all()
    }


def lock_administrator_action(db: Session, actor: User) -> bool:
    """Serialize administrator-account mutations and revalidate the acting administrator."""
    administrator = db.scalar(select(Role).where(Role.name == "administrator").with_for_update())
    db.refresh(actor)
    db.expire(actor, ["roles"])
    return bool(administrator and actor.is_active and administrator in actor.roles)


def authenticate_user(
    db: Session,
    settings: Settings,
    email: str,
    password: str,
    request: Request | None = None,
) -> User:
    canonical, _ = normalize_email(email, settings)
    user = db.scalar(select(User).where(User.email == canonical))
    now = utcnow()
    password_service = PasswordService(settings)
    valid = password_service.verify(password, user.password_hash if user else None)
    if user and user.failed_login_attempts >= 5:
        record_event(db, "auth.login", request=request, target=user, outcome="locked")
        db.commit()
        raise AccountLockedError("Unable to sign in with those credentials.")
    if user and valid and user.status == UserStatus.PENDING.value and user.email_verified_at:
        record_event(db, "auth.login", request=request, target=user, outcome="pending_approval")
        db.commit()
        raise RegistrationPendingError(
            "Your registration is awaiting administrator approval. You cannot sign in yet."
        )
    if not user or not valid or not user.is_active or not user.email_verified_at:
        if user and user.is_active and user.email_verified_at:
            attempts = db.scalar(
                update(User)
                .where(User.id == user.id, User.failed_login_attempts < 5)
                .values(
                    failed_login_attempts=User.failed_login_attempts + 1,
                    locked_until=None,
                )
                .returning(User.failed_login_attempts)
                .execution_options(synchronize_session=False)
            )
            outcome = "failure" if attempts is not None else "locked"
            record_event(db, "auth.login", request=request, target=user, outcome=outcome)
        db.commit()
        raise AuthenticationError("Unable to sign in with those credentials.")
    authenticated_user_id = db.scalar(
        update(User)
        .where(User.id == user.id, User.failed_login_attempts < 5)
        .values(failed_login_attempts=0, locked_until=None, last_login_at=now)
        .returning(User.id)
        .execution_options(synchronize_session=False)
    )
    if not authenticated_user_id:
        record_event(db, "auth.login", request=request, target=user, outcome="locked")
        db.commit()
        raise AccountLockedError("Unable to sign in with those credentials.")
    db.refresh(user)
    if user.password_hash and password_service.needs_rehash(user.password_hash):
        user.password_hash = password_service.hash(password)
    record_event(db, "auth.login", request=request, actor=user, target=user)
    db.commit()
    return user


def authenticate_trusted_identity(
    db: Session,
    settings: Settings,
    request: Request,
) -> User:
    if settings.authentication_mode != "trusted_header":
        raise AuthenticationError("Federated sign-in is not enabled.")
    if not is_trusted_direct_proxy(request, settings):
        record_event(db, "auth.federated", request=request, outcome="untrusted_proxy")
        db.commit()
        raise AuthenticationError("Federated identity could not be verified.")
    header_name = settings.trusted_identity_header.encode("ascii")
    raw_values = [
        value
        for name, value in request.scope.get("headers", [])
        if bytes(name).lower() == header_name
    ]
    if len(raw_values) != 1:
        record_event(db, "auth.federated", request=request, outcome="invalid_header")
        db.commit()
        raise AuthenticationError("Federated identity could not be verified.")
    try:
        asserted_email = bytes(raw_values[0]).decode("utf-8")
        canonical, _ = normalize_email(asserted_email, settings)
    except (UnicodeDecodeError, ValueError):
        record_event(db, "auth.federated", request=request, outcome="invalid_identity")
        db.commit()
        raise AuthenticationError("Federated identity could not be verified.") from None
    user = db.scalar(select(User).where(User.email == canonical))
    if not user or not user.is_active or not user.email_verified_at:
        record_event(
            db,
            "auth.federated",
            request=request,
            target=user,
            outcome="ineligible_account",
        )
        db.commit()
        raise AuthenticationError("Federated identity could not be verified.")
    user.last_login_at = utcnow()
    record_event(db, "auth.federated", request=request, actor=user, target=user)
    db.commit()
    return user


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


def create_session(
    db: Session, settings: Settings, user: User, request: Request | None = None
) -> SessionTokens:
    now = utcnow()
    raw_refresh = random_token()
    session = RefreshSession(
        user_id=user.id,
        refresh_token_hash=hash_token(raw_refresh, settings.session_pepper),
        csrf_token=random_token(24),
        created_at=now,
        last_seen_at=now,
        idle_expires_at=now + timedelta(minutes=settings.session_idle_minutes),
        absolute_expires_at=now + timedelta(hours=settings.refresh_token_hours),
        user_agent=(request.headers.get("user-agent", "")[:500] if request else ""),
        source_ip=client_ip(request),
    )
    db.add(session)
    db.flush()
    access_token, expires_in = create_access_token(user, session.id, settings)
    record_event(db, "auth.session.created", request=request, actor=user, target=user)
    db.commit()
    return SessionTokens(access_token, expires_in, raw_refresh, session)


def rotate_session(
    db: Session, settings: Settings, raw_refresh: str, request: Request | None = None
) -> SessionTokens:
    now = utcnow()
    token_hash = hash_token(raw_refresh, settings.session_pepper)
    replacement = random_token()
    replacement_hash = hash_token(replacement, settings.session_pepper)
    rotated_id = db.scalar(
        update(RefreshSession)
        .where(
            RefreshSession.refresh_token_hash == token_hash,
            RefreshSession.revoked_at.is_(None),
            RefreshSession.idle_expires_at > now,
            RefreshSession.absolute_expires_at > now,
        )
        .values(
            refresh_token_hash=replacement_hash,
            last_seen_at=now,
        )
        .returning(RefreshSession.id)
        .execution_options(synchronize_session=False)
    )
    if not rotated_id:
        replayed = db.scalar(
            select(RefreshTokenHistory).where(RefreshTokenHistory.token_hash == token_hash)
        )
        if replayed:
            replayed.session.revoked_at = replayed.session.revoked_at or now
            record_event(
                db,
                "auth.session.refresh_reuse",
                request=request,
                target=replayed.session.user,
                outcome="denied",
            )
            db.commit()
        else:
            db.rollback()
        raise TokenFlowError("Refresh session is invalid or expired.")
    session = db.get(RefreshSession, rotated_id, populate_existing=True)
    if not session or not session.user.is_active:
        db.rollback()
        raise TokenFlowError("Refresh session is invalid or expired.")
    session.idle_expires_at = min(
        now + timedelta(minutes=settings.session_idle_minutes), session.absolute_expires_at
    )
    db.add(
        RefreshTokenHistory(
            session_id=session.id,
            token_hash=token_hash,
            consumed_at=now,
        )
    )
    access_token, expires_in = create_access_token(session.user, session.id, settings)
    record_event(
        db, "auth.session.refreshed", request=request, actor=session.user, target=session.user
    )
    db.commit()
    return SessionTokens(access_token, expires_in, replacement, session)


def revoke_session(
    db: Session,
    session: RefreshSession,
    *,
    actor: User,
    request: Request | None = None,
) -> None:
    revoked = db.execute(
        update(RefreshSession)
        .where(RefreshSession.id == session.id, RefreshSession.revoked_at.is_(None))
        .values(revoked_at=utcnow())
        .execution_options(synchronize_session=False)
    )
    if revoked.rowcount == 1:
        record_event(db, "auth.session.revoked", request=request, actor=actor, target=session.user)
    db.commit()


def revoke_all_sessions(db: Session, user: User) -> None:
    db.execute(
        update(RefreshSession)
        .where(RefreshSession.user_id == user.id, RefreshSession.revoked_at.is_(None))
        .values(revoked_at=utcnow())
    )


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
    role = _lock_role_catalog(db).get(role_name)
    if db.scalar(select(User).where(User.email == canonical)):
        raise ValueError("An account already exists for that email address.")
    if not role:
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
    accept_url = f"{settings.public_base_url.rstrip('/')}/invitations/accept?token={raw_token}"
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
    if revoked.rowcount != 1:
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
    if consumed.rowcount != 1:
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
    if consumed.rowcount != 1:
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
