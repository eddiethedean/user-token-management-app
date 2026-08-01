from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, EmailStr
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.database import get_db
from app.dependencies import (
    REFRESH_COOKIE,
    AuthContext,
    clear_auth_cookies,
    require_admin,
    require_auth,
    set_auth_cookies,
)
from app.models import AuditEvent, RefreshSession, Role, User, UserStatus, utcnow
from app.schemas import (
    AdminUserUpdate,
    InvitationRequest,
    PasswordChange,
    ProfileUpdate,
    SessionView,
    TokenRequest,
    TokenResponse,
    UserView,
)
from app.security.csrf import require_csrf
from app.security.passwords import PasswordPolicyError, PasswordService, validate_password
from app.services.audit import record_event
from app.services.auth import (
    AuthenticationError,
    TokenFlowError,
    authenticate_user,
    complete_password_reset,
    create_invitation,
    create_session,
    request_password_reset,
    revoke_all_sessions,
    revoke_session,
    rotate_session,
)
from app.services.mailer import deliver_pending

router = APIRouter(prefix="/api/v1")


class EmailRequest(BaseModel):
    email: EmailStr


class ResetRequest(BaseModel):
    token: str
    new_password: str


async def _csrf_if_cookie(request: Request, auth: AuthContext) -> None:
    if not auth.via_bearer:
        await require_csrf(request, auth.session.csrf_token)


@router.post("/auth/token", response_model=TokenResponse)
def issue_token(
    payload: TokenRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> TokenResponse:
    try:
        user = authenticate_user(db, settings, str(payload.email), payload.password, request)
    except AuthenticationError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc
    tokens = create_session(db, settings, user, request)
    set_auth_cookies(response, tokens, settings, request)
    return TokenResponse(access_token=tokens.access_token, expires_in=tokens.access_expires_in)


@router.post("/auth/refresh", response_model=TokenResponse)
def refresh_token(
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> TokenResponse | JSONResponse:
    raw_refresh = request.cookies.get(REFRESH_COOKIE, "")
    try:
        tokens = rotate_session(db, settings, raw_refresh, request)
    except TokenFlowError as exc:
        error_response = JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED,
            content={"detail": str(exc)},
        )
        clear_auth_cookies(error_response, settings, request)
        return error_response
    set_auth_cookies(response, tokens, settings, request)
    return TokenResponse(access_token=tokens.access_token, expires_in=tokens.access_expires_in)


@router.post("/auth/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    request: Request,
    response: Response,
    auth: AuthContext = Depends(require_auth),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> None:
    await _csrf_if_cookie(request, auth)
    revoke_session(db, auth.session, actor=auth.user, request=request)
    clear_auth_cookies(response, settings, request)


@router.post("/auth/logout-all", status_code=status.HTTP_204_NO_CONTENT)
async def logout_all(
    request: Request,
    response: Response,
    auth: AuthContext = Depends(require_auth),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> None:
    await _csrf_if_cookie(request, auth)
    revoke_all_sessions(db, auth.user)
    record_event(
        db, "auth.sessions.revoked_all", request=request, actor=auth.user, target=auth.user
    )
    db.commit()
    clear_auth_cookies(response, settings, request)


@router.post("/auth/forgot-password", status_code=status.HTTP_202_ACCEPTED)
def forgot_password(
    payload: EmailRequest,
    request: Request,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict[str, str]:
    request_password_reset(db, settings, str(payload.email), request)
    deliver_pending(db, settings)
    return {"message": "If an eligible account exists, instructions have been sent."}


@router.post("/auth/reset-password", status_code=status.HTTP_204_NO_CONTENT)
def reset_password(
    payload: ResetRequest,
    request: Request,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> None:
    try:
        complete_password_reset(
            db, settings, raw_token=payload.token, password=payload.new_password, request=request
        )
    except (TokenFlowError, PasswordPolicyError) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    deliver_pending(db, settings)


@router.get("/me", response_model=UserView)
def get_me(auth: AuthContext = Depends(require_auth)) -> UserView:
    return UserView.from_user(auth.user)


@router.patch("/me", response_model=UserView)
async def update_me(
    payload: ProfileUpdate,
    request: Request,
    auth: AuthContext = Depends(require_auth),
    db: Session = Depends(get_db),
) -> UserView:
    await _csrf_if_cookie(request, auth)
    for field, value in payload.model_dump().items():
        setattr(auth.user, field, value.strip())
    record_event(db, "profile.updated", request=request, actor=auth.user, target=auth.user)
    db.commit()
    return UserView.from_user(auth.user)


@router.post("/me/password", status_code=status.HTTP_204_NO_CONTENT)
async def change_password(
    payload: PasswordChange,
    request: Request,
    auth: AuthContext = Depends(require_auth),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> None:
    await _csrf_if_cookie(request, auth)
    passwords = PasswordService(settings)
    if not passwords.verify(payload.current_password, auth.user.password_hash):
        raise HTTPException(status_code=400, detail="Current password is incorrect.")
    try:
        validated = validate_password(payload.new_password, email=auth.user.email)
    except PasswordPolicyError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    auth.user.password_hash = passwords.hash(validated)
    auth.user.password_changed_at = utcnow()
    auth.user.security_version += 1
    revoke_all_sessions(db, auth.user)
    record_event(db, "password.changed", request=request, actor=auth.user, target=auth.user)
    db.commit()


@router.get("/me/sessions", response_model=list[SessionView])
def list_sessions(
    auth: AuthContext = Depends(require_auth), db: Session = Depends(get_db)
) -> list[SessionView]:
    sessions = db.scalars(
        select(RefreshSession)
        .where(RefreshSession.user_id == auth.user.id, RefreshSession.revoked_at.is_(None))
        .order_by(RefreshSession.last_seen_at.desc())
    ).all()
    return [
        SessionView(
            id=item.id,
            created_at=item.created_at,
            last_seen_at=item.last_seen_at,
            absolute_expires_at=item.absolute_expires_at,
            user_agent=item.user_agent,
            source_ip=item.source_ip,
            current=item.id == auth.session.id,
        )
        for item in sessions
    ]


@router.delete("/me/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_session(
    session_id: str,
    request: Request,
    auth: AuthContext = Depends(require_auth),
    db: Session = Depends(get_db),
) -> None:
    await _csrf_if_cookie(request, auth)
    session = db.get(RefreshSession, session_id)
    if not session or session.user_id != auth.user.id:
        raise HTTPException(status_code=404, detail="Session not found")
    revoke_session(db, session, actor=auth.user, request=request)


@router.get("/admin/users", response_model=list[UserView])
def admin_users(
    _: AuthContext = Depends(require_admin), db: Session = Depends(get_db)
) -> list[UserView]:
    return [
        UserView.from_user(user) for user in db.scalars(select(User).order_by(User.email)).all()
    ]


@router.post("/admin/invitations", status_code=status.HTTP_201_CREATED)
async def admin_invite(
    payload: InvitationRequest,
    request: Request,
    auth: AuthContext = Depends(require_admin),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict[str, str]:
    await _csrf_if_cookie(request, auth)
    try:
        invitation, _ = create_invitation(
            db,
            settings,
            email=str(payload.email),
            role_name=payload.role,
            inviter=auth.user,
            request=request,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    deliver_pending(db, settings)
    return {"id": invitation.id, "status": "pending"}


@router.patch("/admin/users/{user_id}", response_model=UserView)
async def admin_update_user(
    user_id: str,
    payload: AdminUserUpdate,
    request: Request,
    auth: AuthContext = Depends(require_admin),
    db: Session = Depends(get_db),
) -> UserView:
    await _csrf_if_cookie(request, auth)
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if payload.status is not None:
        if payload.status not in {item.value for item in UserStatus}:
            raise HTTPException(status_code=400, detail="Invalid status")
        if user.id == auth.user.id and payload.status != UserStatus.ACTIVE.value:
            raise HTTPException(status_code=400, detail="You cannot disable your own account")
        user.status = payload.status
        if payload.status != UserStatus.ACTIVE.value:
            user.security_version += 1
            revoke_all_sessions(db, user)
    if payload.roles is not None:
        roles = db.scalars(select(Role).where(Role.name.in_(payload.roles))).all()
        if len(roles) != len(set(payload.roles)):
            raise HTTPException(status_code=400, detail="One or more roles are invalid")
        if user.id == auth.user.id and "administrator" not in payload.roles:
            raise HTTPException(
                status_code=400, detail="You cannot remove your own administrator role"
            )
        user.roles = list(roles)
        user.security_version += 1
    record_event(
        db,
        "admin.user.updated",
        request=request,
        actor=auth.user,
        target=user,
        detail={"status": payload.status, "roles": payload.roles},
    )
    db.commit()
    return UserView.from_user(user)


@router.get("/admin/audit")
def admin_audit(
    _: AuthContext = Depends(require_admin), db: Session = Depends(get_db)
) -> list[dict]:
    events = db.scalars(select(AuditEvent).order_by(AuditEvent.occurred_at.desc()).limit(200)).all()
    return [
        {
            "id": event.id,
            "occurred_at": event.occurred_at,
            "event_type": event.event_type,
            "outcome": event.outcome,
            "actor_user_id": event.actor_user_id,
            "target_user_id": event.target_user_id,
            "request_id": event.request_id,
            "detail": event.detail,
        }
        for event in events
    ]
