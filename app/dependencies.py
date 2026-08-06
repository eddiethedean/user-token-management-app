from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Form, Header, HTTPException, Request, Response, status
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.database import get_db
from app.models import RefreshSession, User, utcnow
from app.routing import cookie_path
from app.security.csrf import assert_csrf
from app.security.tokens import AccessTokenError, decode_access_token
from app.services.auth import SessionTokens, TokenFlowError, rotate_session

ACCESS_COOKIE = "access_registry_access"
REFRESH_COOKIE = "access_registry_refresh"

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
    access_token = bearer or request.cookies.get(ACCESS_COOKIE)
    if access_token:
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
                return AuthContext(user=user, session=session, via_bearer=bool(bearer))
        except AccessTokenError:
            if bearer:
                return None
    if bearer:
        return None
    raw_refresh = request.cookies.get(REFRESH_COOKIE)
    if not raw_refresh:
        return None
    try:
        rotated = rotate_session(db, settings, raw_refresh, request)
    except TokenFlowError:
        return None
    request.state.rotated_tokens = rotated
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
    assert_csrf(submitted, auth.session.csrf_token)


RequireCsrf = Annotated[None, Depends(enforce_session_csrf)]


def set_auth_cookies(
    response: Response, tokens: SessionTokens, settings: Settings, request: Request
) -> None:
    common = {
        "secure": settings.cookie_secure,
        "httponly": True,
        "samesite": "lax",
        "path": cookie_path(request, settings.cookie_path),
    }
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


def clear_auth_cookies(response: Response, settings: Settings, request: Request) -> None:
    path = cookie_path(request, settings.cookie_path)
    common = {
        "path": path,
        "secure": settings.cookie_secure,
        "httponly": True,
        "samesite": "lax",
    }
    response.delete_cookie(ACCESS_COOKIE, **common)
    response.delete_cookie(REFRESH_COOKIE, **common)
