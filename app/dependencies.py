from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Form, Header, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.database import get_db
from app.dev_trace import dev_trace
from app.models import RefreshSession, User, utcnow
from app.security.cookies import ACCESS_COOKIE, REFRESH_COOKIE, request_cookie_values
from app.security.csrf import assert_csrf
from app.security.tokens import AccessTokenError, decode_access_token, hash_token
from app.services.auth import SessionTokens, TokenFlowError, rotate_session

DbSession = Annotated[Session, Depends(get_db)]
SettingsDep = Annotated[Settings, Depends(get_settings)]


@dataclass
class AuthContext:
    user: User
    session: RefreshSession
    via_bearer: bool = False


def _bearer_token(request: Request) -> str | None:
    authorization = request.headers.get("authorization", "")
    scheme, _, token = authorization.partition(" ")
    if scheme.casefold() == "bearer" and token:
        return token
    return None


def get_optional_auth(
    request: Request,
    db: DbSession,
    settings: SettingsDep,
) -> AuthContext | None:
    bearer = _bearer_token(request)
    access_cookie_values = request_cookie_values(request, ACCESS_COOKIE)
    refresh_values = request_cookie_values(request, REFRESH_COOKIE)
    access_tokens = [bearer] if bearer else access_cookie_values
    dev_trace(
        "auth.resolve.start",
        bearer=bool(bearer),
        access_cookie_count=len(access_cookie_values),
        refresh_cookie_count=len(refresh_values),
    )
    for index, access_token in enumerate(access_tokens):
        if not access_token:
            continue
        try:
            payload = decode_access_token(access_token, settings)
            user = db.get(User, payload["sub"])
            session = db.get(RefreshSession, payload["sid"])
            if (
                user
                and session
                and session.user_id == user.id
                and user.is_active
                and user.security_version == payload.get("sv")
                and not session.revoked_at
                and session.idle_expires_at > utcnow()
                and session.absolute_expires_at > utcnow()
            ):
                dev_trace(
                    "auth.access.accepted",
                    source="bearer" if bearer else "cookie",
                    candidate_index=index,
                    candidate_count=len(access_tokens),
                )
                return AuthContext(user=user, session=session, via_bearer=bool(bearer))
        except AccessTokenError:
            continue
    if bearer:
        dev_trace("auth.anonymous", reason="invalid_bearer")
        return None
    if not refresh_values:
        dev_trace(
            "auth.anonymous",
            reason="no_session_cookies" if not access_tokens else "invalid_access_no_refresh",
        )
        return None
    now = utcnow()
    selected_refresh_index = 0
    for index, value in enumerate(refresh_values):
        active_session_id = db.scalar(
            select(RefreshSession.id).where(
                RefreshSession.refresh_token_hash == hash_token(value, settings.session_pepper),
                RefreshSession.revoked_at.is_(None),
                RefreshSession.idle_expires_at > now,
                RefreshSession.absolute_expires_at > now,
            )
        )
        if active_session_id:
            selected_refresh_index = index
            break
    raw_refresh = refresh_values[selected_refresh_index]
    try:
        rotated = rotate_session(db, settings, raw_refresh, request)
    except TokenFlowError:
        dev_trace(
            "auth.anonymous",
            reason="invalid_refresh",
            refresh_cookie_count=len(refresh_values),
        )
        return None
    request.state.rotated_tokens = rotated
    dev_trace(
        "auth.refresh.accepted",
        candidate_index=selected_refresh_index,
        candidate_count=len(refresh_values),
    )
    return AuthContext(user=rotated.session.user, session=rotated.session)


OptionalAuth = Annotated[AuthContext | None, Depends(get_optional_auth)]


def require_auth(context: OptionalAuth) -> AuthContext:
    if not context:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return context


Auth = Annotated[AuthContext, Depends(require_auth)]


def require_admin(context: Auth) -> AuthContext:
    if "administrator" not in context.user.role_names:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Administrator required")
    return context


AdminAuth = Annotated[AuthContext, Depends(require_admin)]


def enforce_session_csrf(
    auth: Auth,
    csrf_token: Annotated[str, Form(max_length=256)] = "",
    x_csrf_token: Annotated[str | None, Header()] = None,
) -> None:
    """Validate session CSRF from ``X-CSRF-Token`` or form ``csrf_token``."""
    submitted = (x_csrf_token or "").strip() or csrf_token
    try:
        assert_csrf(submitted, auth.session.csrf_token)
    except HTTPException:
        dev_trace(
            "csrf.session.rejected",
            submitted=bool(submitted),
            source="header" if (x_csrf_token or "").strip() else "form",
        )
        raise
    dev_trace(
        "csrf.session.accepted",
        source="header" if (x_csrf_token or "").strip() else "form",
    )


RequireCsrf = Annotated[None, Depends(enforce_session_csrf)]


def set_auth_cookies(
    response: Response, tokens: SessionTokens, settings: Settings, request: Request
) -> None:
    path = None if settings.cookie_path == "auto" else settings.cookie_path
    common = {
        "secure": settings.cookie_secure,
        "httponly": True,
        "samesite": "lax",
        "path": path,
    }
    if path != "/":
        legacy = {**common, "path": "/"}
        response.delete_cookie(ACCESS_COOKIE, **legacy)
        response.delete_cookie(REFRESH_COOKIE, **legacy)
    response.set_cookie(
        ACCESS_COOKIE,
        tokens.access_token,
        max_age=tokens.access_expires_in,
        **common,
    )
    refresh_remaining = int((tokens.session.absolute_expires_at - utcnow()).total_seconds())
    response.set_cookie(
        REFRESH_COOKIE,
        tokens.refresh_token,
        max_age=max(0, refresh_remaining),
        **common,
    )
    dev_trace(
        "auth.cookies.issued",
        cookie_path=path,
        secure=settings.cookie_secure,
        samesite="lax",
        legacy_root_cleanup=path != "/",
    )


def clear_auth_cookies(response: Response, settings: Settings, request: Request) -> None:
    path = None if settings.cookie_path == "auto" else settings.cookie_path
    common = {
        "path": path,
        "secure": settings.cookie_secure,
        "httponly": True,
        "samesite": "lax",
    }
    response.delete_cookie(ACCESS_COOKIE, **common)
    response.delete_cookie(REFRESH_COOKIE, **common)
    if path != "/":
        legacy = {**common, "path": "/"}
        response.delete_cookie(ACCESS_COOKIE, **legacy)
        response.delete_cookie(REFRESH_COOKIE, **legacy)
    dev_trace(
        "auth.cookies.cleared",
        cookie_path=path,
        legacy_root_cleanup=path != "/",
    )
