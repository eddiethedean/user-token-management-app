from dataclasses import dataclass

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.database import get_db
from app.models import RefreshSession, User, utcnow
from app.security.tokens import AccessTokenError, decode_access_token, hash_token
from app.services.auth import SessionTokens, TokenFlowError, rotate_session


ACCESS_COOKIE = "access_registry_access"
REFRESH_COOKIE = "access_registry_refresh"


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
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
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


def require_auth(context: AuthContext | None = Depends(get_optional_auth)) -> AuthContext:
    if not context:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return context


def require_admin(context: AuthContext = Depends(require_auth)) -> AuthContext:
    if "administrator" not in context.user.role_names:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Administrator required")
    return context


def get_session_by_refresh_token(
    db: Session, settings: Settings, raw_refresh: str
) -> RefreshSession | None:
    return db.scalar(
        select(RefreshSession).where(
            RefreshSession.refresh_token_hash == hash_token(raw_refresh, settings.session_pepper)
        )
    )


def set_auth_cookies(response, tokens: SessionTokens, settings: Settings) -> None:
    common = {
        "secure": settings.cookie_secure,
        "httponly": True,
        "samesite": "lax",
        "path": settings.cookie_path,
    }
    response.set_cookie(
        ACCESS_COOKIE,
        tokens.access_token,
        max_age=tokens.access_expires_in,
        **common,
    )
    response.set_cookie(
        REFRESH_COOKIE,
        tokens.refresh_token,
        max_age=settings.refresh_token_hours * 3600,
        **common,
    )


def clear_auth_cookies(response, settings: Settings) -> None:
    response.delete_cookie(ACCESS_COOKIE, path=settings.cookie_path)
    response.delete_cookie(REFRESH_COOKIE, path=settings.cookie_path)

