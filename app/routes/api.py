from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, EmailStr
from sqlalchemy import or_, select
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
from app.models import AuditEvent, Invitation, RefreshSession, Role, User, UserStatus
from app.schemas import (
    AdminUserUpdate,
    InvitationRequest,
    PasswordChange,
    ProfileUpdate,
    RegistrationRequest,
    SecretSlotView,
    SecretTokenRequest,
    SessionView,
    TokenRequest,
    TokenResponse,
    UserView,
)
from app.security.csrf import require_csrf
from app.security.passwords import PasswordPolicyError
from app.services.accounts import (
    CurrentPasswordError,
    ProfileValues,
    update_profile,
)
from app.services.accounts import (
    change_password as change_account_password,
)
from app.services.audit import record_event
from app.services.auth import (
    AuthenticationError,
    TokenFlowError,
    approve_self_registration,
    authenticate_trusted_identity,
    authenticate_user,
    complete_password_reset,
    create_invitation,
    create_session,
    deny_self_registration,
    request_password_reset,
    request_self_registration,
    revoke_all_sessions,
    revoke_invitation,
    revoke_session,
    rotate_session,
)
from app.services.directory import (
    DirectoryEligibilityError,
    DirectoryUnavailableError,
    validate_directory_email,
)
from app.services.rate_limit import check_rate_limit
from app.services.secrets import (
    delete_user_secret,
    list_user_secrets,
    require_secret_provider,
    store_user_secret,
)

router = APIRouter(prefix="/api/v1")


class EmailRequest(BaseModel):
    email: EmailStr


class ResetRequest(BaseModel):
    token: str
    new_password: str


async def _csrf_if_cookie(request: Request, auth: AuthContext) -> None:
    if not auth.via_bearer:
        await require_csrf(request, auth.session.csrf_token)


@router.post("/auth/register", status_code=status.HTTP_202_ACCEPTED)
async def register(
    payload: RegistrationRequest,
    request: Request,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict[str, str]:
    check_rate_limit(
        db,
        settings,
        request,
        scope="registration",
        source_limit=settings.rate_limit_registration_per_source,
        account_limit=settings.rate_limit_registration_per_account,
        account_key=str(payload.email),
    )
    try:
        await validate_directory_email(str(payload.email), settings)
        request_self_registration(
            db,
            settings,
            email=str(payload.email),
            full_name=payload.full_name,
            request=request,
        )
    except DirectoryUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except (DirectoryEligibilityError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "message": (
            "If the address is eligible, a verification email has been sent. Email verification "
            "and administrator approval are both required before sign-in."
        )
    }


@router.post("/auth/token", response_model=TokenResponse)
def issue_token(
    payload: TokenRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> TokenResponse:
    if settings.authentication_mode != "local_password":
        raise HTTPException(status_code=403, detail="Password token issuance is disabled")
    check_rate_limit(
        db,
        settings,
        request,
        scope="login",
        source_limit=settings.rate_limit_login_per_source,
        account_limit=settings.rate_limit_login_per_account,
        account_key=str(payload.email),
    )
    try:
        user = authenticate_user(db, settings, str(payload.email), payload.password, request)
    except AuthenticationError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc
    tokens = create_session(db, settings, user, request)
    set_auth_cookies(response, tokens, settings, request)
    return TokenResponse(access_token=tokens.access_token, expires_in=tokens.access_expires_in)


@router.post("/auth/federated", response_model=TokenResponse)
def issue_federated_token(
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> TokenResponse:
    check_rate_limit(
        db,
        settings,
        request,
        scope="federated_login",
        source_limit=settings.rate_limit_login_per_source,
    )
    try:
        user = authenticate_trusted_identity(db, settings, request)
    except AuthenticationError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
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
    check_rate_limit(
        db,
        settings,
        request,
        scope="password_reset_request",
        source_limit=settings.rate_limit_reset_per_source,
        account_limit=settings.rate_limit_reset_per_account,
        account_key=str(payload.email),
    )
    request_password_reset(db, settings, str(payload.email), request)
    return {"message": "If an eligible account exists, instructions have been sent."}


@router.post("/auth/reset-password", status_code=status.HTTP_204_NO_CONTENT)
def reset_password(
    payload: ResetRequest,
    request: Request,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> None:
    check_rate_limit(
        db,
        settings,
        request,
        scope="password_reset_complete",
        source_limit=settings.rate_limit_reset_per_source,
    )
    try:
        complete_password_reset(
            db, settings, raw_token=payload.token, password=payload.new_password, request=request
        )
    except (TokenFlowError, PasswordPolicyError) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


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
    update_profile(
        db,
        user=auth.user,
        values=ProfileValues(**payload.model_dump()),
        request=request,
    )
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
    try:
        change_account_password(
            db,
            settings,
            user=auth.user,
            current_password=payload.current_password,
            new_password=payload.new_password,
            request=request,
        )
    except (CurrentPasswordError, PasswordPolicyError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/me/secrets", response_model=list[SecretSlotView])
def get_secret_slots(
    response: Response,
    auth: AuthContext = Depends(require_auth),
    db: Session = Depends(get_db),
) -> list[SecretSlotView]:
    response.headers["Cache-Control"] = "no-store"
    return [
        SecretSlotView(
            provider=provider.name,
            label=provider.label,
            environment_variable=provider.environment_variable,
            configured=stored is not None,
            updated_at=stored.updated_at if stored else None,
            last_used_at=stored.last_used_at if stored else None,
        )
        for provider, stored in list_user_secrets(db, auth.user)
    ]


@router.put("/me/secrets/{provider}", response_model=SecretSlotView)
async def put_secret(
    provider: str,
    payload: SecretTokenRequest,
    request: Request,
    response: Response,
    auth: AuthContext = Depends(require_auth),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> SecretSlotView:
    await _csrf_if_cookie(request, auth)
    try:
        specification = require_secret_provider(provider)
        stored = store_user_secret(
            db,
            settings,
            user=auth.user,
            provider=provider,
            token=payload.token,
            request=request,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    response.headers["Cache-Control"] = "no-store"
    return SecretSlotView(
        provider=specification.name,
        label=specification.label,
        environment_variable=specification.environment_variable,
        configured=True,
        updated_at=stored.updated_at,
        last_used_at=stored.last_used_at,
    )


@router.delete("/me/secrets/{provider}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_secret(
    provider: str,
    request: Request,
    response: Response,
    auth: AuthContext = Depends(require_auth),
    db: Session = Depends(get_db),
) -> None:
    await _csrf_if_cookie(request, auth)
    try:
        deleted = delete_user_secret(db, user=auth.user, provider=provider, request=request)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not deleted:
        raise HTTPException(status_code=404, detail="API token is not configured.")
    response.headers["Cache-Control"] = "no-store"


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
    q: str = "",
    status_filter: str = Query(default="", alias="status"),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
    _: AuthContext = Depends(require_admin),
    db: Session = Depends(get_db),
) -> list[UserView]:
    statement = select(User)
    cleaned_query = q.strip()[:160]
    if cleaned_query:
        pattern = f"%{cleaned_query}%"
        statement = statement.where(
            or_(
                User.email.ilike(pattern),
                User.email_original.ilike(pattern),
                User.full_name.ilike(pattern),
                User.organization.ilike(pattern),
            )
        )
    if status_filter in {item.value for item in UserStatus}:
        statement = statement.where(User.status == status_filter)
    return [
        UserView.from_user(user)
        for user in db.scalars(statement.order_by(User.email).offset(offset).limit(limit)).all()
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
        await validate_directory_email(str(payload.email), settings)
        invitation, _ = create_invitation(
            db,
            settings,
            email=str(payload.email),
            role_name=payload.role,
            inviter=auth.user,
            request=request,
        )
    except DirectoryUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except (DirectoryEligibilityError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
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
        if user.status == UserStatus.PENDING.value and payload.status == UserStatus.ACTIVE.value:
            raise HTTPException(
                status_code=400,
                detail="Use the registration approval endpoint for pending accounts",
            )
        if payload.status == UserStatus.ACTIVE.value and (
            not user.email_verified_at or not user.password_hash
        ):
            raise HTTPException(
                status_code=400,
                detail="The government email must be verified before activation",
            )
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


@router.delete("/admin/invitations/{invitation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def admin_revoke_invitation(
    invitation_id: str,
    request: Request,
    auth: AuthContext = Depends(require_admin),
    db: Session = Depends(get_db),
) -> None:
    await _csrf_if_cookie(request, auth)
    invitation = db.get(Invitation, invitation_id)
    if not invitation:
        raise HTTPException(status_code=404, detail="Invitation not found")
    try:
        revoke_invitation(
            db,
            invitation=invitation,
            administrator=auth.user,
            request=request,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/admin/users/{user_id}/approve", response_model=UserView)
async def admin_approve_registration(
    user_id: str,
    request: Request,
    auth: AuthContext = Depends(require_admin),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> UserView:
    await _csrf_if_cookie(request, auth)
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    try:
        approve_self_registration(
            db,
            settings,
            user=user,
            administrator=auth.user,
            request=request,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return UserView.from_user(user)


@router.post("/admin/users/{user_id}/deny", response_model=UserView)
async def admin_deny_registration(
    user_id: str,
    request: Request,
    auth: AuthContext = Depends(require_admin),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> UserView:
    await _csrf_if_cookie(request, auth)
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    try:
        deny_self_registration(
            db,
            settings,
            user=user,
            administrator=auth.user,
            request=request,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return UserView.from_user(user)


@router.get("/admin/audit")
def admin_audit(
    event_type: str = "",
    outcome: str = "",
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=200),
    _: AuthContext = Depends(require_admin),
    db: Session = Depends(get_db),
) -> list[dict]:
    statement = select(AuditEvent)
    if event_type.strip():
        statement = statement.where(AuditEvent.event_type == event_type.strip()[:100])
    if outcome.strip():
        statement = statement.where(AuditEvent.outcome == outcome.strip()[:20])
    events = db.scalars(
        statement.order_by(AuditEvent.occurred_at.desc()).offset(offset).limit(limit)
    ).all()
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
