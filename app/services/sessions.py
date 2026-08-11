"""Authentication and refresh-session lifecycle."""

from __future__ import annotations

from datetime import timedelta

from fastapi import Request
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.config import Settings
from app.db_compat import execute_dml, scalar_returning
from app.models import RefreshSession, RefreshTokenHistory, User, UserStatus, utcnow
from app.security.client import is_trusted_direct_proxy
from app.security.email import EmailPolicyError, normalize_email
from app.security.passwords import PasswordService
from app.security.tokens import create_access_token, hash_token, random_token
from app.services.audit import client_ip, record_event
from app.services.auth_common import (
    AccountLockedError,
    AuthenticationError,
    SessionTokens,
    TokenFlowError,
)

_GENERIC_AUTH_FAILURE = "Unable to sign in with those credentials."


def authenticate_user(
    db: Session,
    settings: Settings,
    email: str,
    password: str,
    request: Request | None = None,
) -> User:
    password_service = PasswordService(settings)
    try:
        canonical, _ = normalize_email(email, settings)
    except EmailPolicyError:
        password_service.verify(password, None)
        raise AuthenticationError(_GENERIC_AUTH_FAILURE) from None
    user = db.scalar(select(User).where(User.email == canonical))
    now = utcnow()
    valid = password_service.verify(password, user.password_hash if user else None)
    if user and user.failed_login_attempts >= 5:
        record_event(db, "auth.login", request=request, target=user, outcome="locked")
        db.commit()
        raise AccountLockedError(_GENERIC_AUTH_FAILURE)
    if user and valid and user.status == UserStatus.PENDING.value and user.email_verified_at:
        record_event(db, "auth.login", request=request, target=user, outcome="pending_approval")
        db.commit()
        raise AuthenticationError(_GENERIC_AUTH_FAILURE)
    if not user or not valid or not user.is_active or not user.email_verified_at:
        if user and user.is_active and user.email_verified_at:
            increment = (
                update(User)
                .where(User.id == user.id, User.failed_login_attempts < 5)
                .values(
                    failed_login_attempts=User.failed_login_attempts + 1,
                    locked_until=None,
                )
                .execution_options(synchronize_session=False)
            )

            def _read_attempts() -> int | None:
                db.refresh(user)
                return user.failed_login_attempts

            attempts = scalar_returning(
                db, increment, User.failed_login_attempts, fallback=_read_attempts
            )
            outcome = "failure" if attempts is not None else "locked"
            record_event(db, "auth.login", request=request, target=user, outcome=outcome)
        db.commit()
        raise AuthenticationError(_GENERIC_AUTH_FAILURE)
    clear_failures = (
        update(User)
        .where(User.id == user.id, User.failed_login_attempts < 5)
        .values(failed_login_attempts=0, locked_until=None, last_login_at=now)
        .execution_options(synchronize_session=False)
    )
    authenticated_user_id = scalar_returning(db, clear_failures, User.id, fallback=lambda: user.id)
    if not authenticated_user_id:
        record_event(db, "auth.login", request=request, target=user, outcome="locked")
        db.commit()
        raise AccountLockedError(_GENERIC_AUTH_FAILURE)
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
    rotate = (
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
        .execution_options(synchronize_session=False)
    )
    rotated_id = scalar_returning(
        db,
        rotate,
        RefreshSession.id,
        fallback=lambda: db.scalar(
            select(RefreshSession.id).where(RefreshSession.refresh_token_hash == replacement_hash)
        ),
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
    revoked = execute_dml(
        db,
        update(RefreshSession)
        .where(RefreshSession.id == session.id, RefreshSession.revoked_at.is_(None))
        .values(revoked_at=utcnow())
        .execution_options(synchronize_session=False),
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
