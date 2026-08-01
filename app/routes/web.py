from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.database import get_db
from app.dependencies import (
    AuthContext,
    clear_auth_cookies,
    get_optional_auth,
    require_admin,
    require_auth,
    set_auth_cookies,
)
from app.models import AuditEvent, Invitation, RefreshSession, Role, User, UserStatus, utcnow
from app.routing import app_path
from app.security.csrf import require_csrf
from app.security.passwords import PasswordPolicyError, PasswordService, validate_password
from app.services.audit import record_event
from app.services.auth import (
    AuthenticationError,
    TokenFlowError,
    accept_invitation,
    authenticate_user,
    complete_password_reset,
    create_invitation,
    create_session,
    get_valid_invitation,
    get_valid_password_reset,
    request_password_reset,
    revoke_all_sessions,
    revoke_session,
)
from app.services.mailer import deliver_pending
from app.templating import template_context, templates

router = APIRouter(include_in_schema=False)


def _safe_next(value: str) -> str:
    is_local_path = (
        value.startswith("/")
        and not value.startswith("//")
        and "\\" not in value
        and not any(ord(character) < 32 for character in value)
    )
    return value if is_local_path else "/profile"


@router.get("/", response_class=HTMLResponse)
def home(
    request: Request, auth: AuthContext | None = Depends(get_optional_auth)
) -> RedirectResponse:
    return RedirectResponse(app_path(request, "/profile" if auth else "/login"), status_code=303)


@router.get("/login", response_class=HTMLResponse, response_model=None)
def login_page(
    request: Request,
    next: str = "/profile",
    auth: AuthContext | None = Depends(get_optional_auth),
) -> HTMLResponse | RedirectResponse:
    if auth:
        return RedirectResponse(app_path(request, _safe_next(next)), status_code=303)
    return templates.TemplateResponse(
        request=request,
        name="auth/login.html",
        context=template_context(request, page_title="Sign in", next=_safe_next(next)),
    )


@router.post("/login", response_class=HTMLResponse, response_model=None)
def login_submit(
    request: Request,
    email: str = Form(),
    password: str = Form(),
    next: str = Form(default="/profile"),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> HTMLResponse | RedirectResponse:
    try:
        user = authenticate_user(db, settings, email, password, request)
    except (AuthenticationError, ValueError) as exc:
        return templates.TemplateResponse(
            request=request,
            name="auth/login.html",
            status_code=400,
            context=template_context(
                request,
                page_title="Sign in",
                error=str(exc),
                email=email,
                next=_safe_next(next),
            ),
        )
    tokens = create_session(db, settings, user, request)
    response = RedirectResponse(app_path(request, _safe_next(next)), status_code=303)
    set_auth_cookies(response, tokens, settings, request)
    return response


@router.post("/logout")
async def logout_submit(
    request: Request,
    auth: AuthContext = Depends(require_auth),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> RedirectResponse:
    await require_csrf(request, auth.session.csrf_token)
    revoke_session(db, auth.session, actor=auth.user, request=request)
    response = RedirectResponse(app_path(request, "/login"), status_code=303)
    clear_auth_cookies(response, settings, request)
    return response


@router.get("/password/forgot", response_class=HTMLResponse)
def forgot_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request=request,
        name="auth/forgot_password.html",
        context=template_context(request, page_title="Reset password"),
    )


@router.post("/password/forgot", response_class=HTMLResponse)
def forgot_submit(
    request: Request,
    email: str = Form(),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> HTMLResponse:
    request_password_reset(db, settings, email, request)
    deliver_pending(db, settings)
    return templates.TemplateResponse(
        request=request,
        name="auth/forgot_password.html",
        context=template_context(
            request,
            page_title="Reset password",
            success="If an eligible account exists, instructions have been sent.",
        ),
    )


@router.get("/password/reset", response_class=HTMLResponse)
def reset_page(
    request: Request,
    token: str,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> HTMLResponse:
    error = ""
    try:
        get_valid_password_reset(db, settings, token)
    except TokenFlowError as exc:
        error = str(exc)
    return templates.TemplateResponse(
        request=request,
        name="auth/reset_password.html",
        status_code=400 if error else 200,
        context=template_context(
            request, page_title="Choose a new password", token=token, error=error
        ),
    )


@router.post("/password/reset", response_class=HTMLResponse, response_model=None)
def reset_submit(
    request: Request,
    token: str = Form(),
    password: str = Form(),
    password_confirm: str = Form(),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> HTMLResponse | RedirectResponse:
    error = ""
    if password != password_confirm:
        error = "Passwords do not match."
    else:
        try:
            complete_password_reset(
                db, settings, raw_token=token, password=password, request=request
            )
            deliver_pending(db, settings)
            return RedirectResponse(app_path(request, "/login?reset=complete"), status_code=303)
        except (TokenFlowError, PasswordPolicyError) as exc:
            error = str(exc)
    return templates.TemplateResponse(
        request=request,
        name="auth/reset_password.html",
        status_code=400,
        context=template_context(
            request, page_title="Choose a new password", token=token, error=error
        ),
    )


@router.get("/invitations/accept", response_class=HTMLResponse)
def invitation_page(
    request: Request,
    token: str,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> HTMLResponse:
    invitation = None
    error = ""
    try:
        invitation = get_valid_invitation(db, settings, token)
    except TokenFlowError as exc:
        error = str(exc)
    return templates.TemplateResponse(
        request=request,
        name="auth/accept_invitation.html",
        status_code=400 if error else 200,
        context=template_context(
            request,
            page_title="Accept invitation",
            token=token,
            invitation=invitation,
            error=error,
        ),
    )


@router.post("/invitations/accept", response_class=HTMLResponse, response_model=None)
def invitation_submit(
    request: Request,
    token: str = Form(),
    full_name: str = Form(),
    password: str = Form(),
    password_confirm: str = Form(),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> HTMLResponse | RedirectResponse:
    error = ""
    invitation = None
    try:
        invitation = get_valid_invitation(db, settings, token)
        if password != password_confirm:
            raise PasswordPolicyError("Passwords do not match.")
        accept_invitation(
            db,
            settings,
            raw_token=token,
            password=password,
            full_name=full_name,
            request=request,
        )
        return RedirectResponse(app_path(request, "/login?invitation=accepted"), status_code=303)
    except (TokenFlowError, PasswordPolicyError) as exc:
        error = str(exc)
    return templates.TemplateResponse(
        request=request,
        name="auth/accept_invitation.html",
        status_code=400,
        context=template_context(
            request,
            page_title="Accept invitation",
            token=token,
            invitation=invitation,
            full_name=full_name,
            error=error,
        ),
    )


@router.get("/profile", response_class=HTMLResponse)
def profile_page(request: Request, auth: AuthContext = Depends(require_auth)) -> HTMLResponse:
    return templates.TemplateResponse(
        request=request,
        name="profile/index.html",
        context=template_context(request, auth=auth, page_title="Your profile"),
    )


@router.post("/profile", response_class=HTMLResponse)
async def profile_submit(
    request: Request,
    full_name: str = Form(default=""),
    organization: str = Form(default=""),
    job_title: str = Form(default=""),
    phone: str = Form(default=""),
    auth: AuthContext = Depends(require_auth),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    await require_csrf(request, auth.session.csrf_token)
    auth.user.full_name = full_name.strip()[:160]
    auth.user.organization = organization.strip()[:160]
    auth.user.job_title = job_title.strip()[:160]
    auth.user.phone = phone.strip()[:40]
    record_event(db, "profile.updated", request=request, actor=auth.user, target=auth.user)
    db.commit()
    return templates.TemplateResponse(
        request=request,
        name="partials/profile_form.html",
        context=template_context(request, auth=auth, success="Your profile has been updated."),
    )


@router.get("/security", response_class=HTMLResponse)
def security_page(
    request: Request,
    auth: AuthContext = Depends(require_auth),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    sessions = db.scalars(
        select(RefreshSession)
        .where(RefreshSession.user_id == auth.user.id, RefreshSession.revoked_at.is_(None))
        .order_by(RefreshSession.last_seen_at.desc())
    ).all()
    events = db.scalars(
        select(AuditEvent)
        .where(AuditEvent.target_user_id == auth.user.id)
        .order_by(AuditEvent.occurred_at.desc())
        .limit(12)
    ).all()
    return templates.TemplateResponse(
        request=request,
        name="profile/security.html",
        context=template_context(
            request, auth=auth, page_title="Security", sessions=sessions, events=events
        ),
    )


@router.post("/security/password", response_class=HTMLResponse)
async def password_change_submit(
    request: Request,
    current_password: str = Form(),
    new_password: str = Form(),
    new_password_confirm: str = Form(),
    auth: AuthContext = Depends(require_auth),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> HTMLResponse:
    await require_csrf(request, auth.session.csrf_token)
    error = ""
    passwords = PasswordService(settings)
    if not passwords.verify(current_password, auth.user.password_hash):
        error = "Current password is incorrect."
    elif new_password != new_password_confirm:
        error = "New passwords do not match."
    else:
        try:
            validated = validate_password(new_password, email=auth.user.email)
            auth.user.password_hash = passwords.hash(validated)
            auth.user.password_changed_at = utcnow()
            auth.user.security_version += 1
            revoke_all_sessions(db, auth.user)
            record_event(db, "password.changed", request=request, actor=auth.user, target=auth.user)
            db.commit()
        except PasswordPolicyError as exc:
            error = str(exc)
    return templates.TemplateResponse(
        request=request,
        name="partials/password_form.html",
        status_code=400 if error else 200,
        context=template_context(
            request,
            auth=auth,
            error=error,
            success="Password changed. Sign in again with your new password." if not error else "",
        ),
    )


@router.post("/security/sessions/{session_id}/revoke", response_class=HTMLResponse)
async def revoke_session_submit(
    session_id: str,
    request: Request,
    auth: AuthContext = Depends(require_auth),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    await require_csrf(request, auth.session.csrf_token)
    session = db.get(RefreshSession, session_id)
    if not session or session.user_id != auth.user.id:
        raise HTTPException(status_code=404, detail="Session not found")
    revoke_session(db, session, actor=auth.user, request=request)
    remaining = db.scalars(
        select(RefreshSession)
        .where(RefreshSession.user_id == auth.user.id, RefreshSession.revoked_at.is_(None))
        .order_by(RefreshSession.last_seen_at.desc())
    ).all()
    return templates.TemplateResponse(
        request=request,
        name="partials/session_list.html",
        context=template_context(request, auth=auth, sessions=remaining),
    )


@router.get("/admin/users", response_class=HTMLResponse)
def users_page(
    request: Request,
    auth: AuthContext = Depends(require_admin),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    users = db.scalars(select(User).order_by(User.created_at.desc())).all()
    invitations = db.scalars(
        select(Invitation).order_by(Invitation.created_at.desc()).limit(25)
    ).all()
    roles = db.scalars(select(Role).order_by(Role.name)).all()
    return templates.TemplateResponse(
        request=request,
        name="admin/users.html",
        context=template_context(
            request,
            auth=auth,
            page_title="User administration",
            users=users,
            invitations=invitations,
            roles=roles,
        ),
    )


@router.post("/admin/invitations", response_class=HTMLResponse)
async def invite_submit(
    request: Request,
    email: str = Form(),
    role: str = Form(default="user"),
    auth: AuthContext = Depends(require_admin),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> HTMLResponse:
    await require_csrf(request, auth.session.csrf_token)
    error = ""
    try:
        create_invitation(
            db,
            settings,
            email=email,
            role_name=role,
            inviter=auth.user,
            request=request,
        )
        deliver_pending(db, settings)
    except ValueError as exc:
        error = str(exc)
    invitations = db.scalars(
        select(Invitation).order_by(Invitation.created_at.desc()).limit(25)
    ).all()
    return templates.TemplateResponse(
        request=request,
        name="partials/invitation_panel.html",
        status_code=400 if error else 200,
        context=template_context(
            request,
            auth=auth,
            invitations=invitations,
            roles=db.scalars(select(Role).order_by(Role.name)).all(),
            error=error,
            success="Invitation queued for delivery." if not error else "",
        ),
    )


@router.post("/admin/users/{user_id}/toggle", response_class=HTMLResponse)
async def toggle_user(
    user_id: str,
    request: Request,
    auth: AuthContext = Depends(require_admin),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    await require_csrf(request, auth.session.csrf_token)
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if user.id == auth.user.id:
        raise HTTPException(status_code=400, detail="You cannot disable your own account")
    user.status = (
        UserStatus.DISABLED.value
        if user.status == UserStatus.ACTIVE.value
        else UserStatus.ACTIVE.value
    )
    user.security_version += 1
    if not user.is_active:
        revoke_all_sessions(db, user)
    record_event(
        db,
        "admin.user.status_changed",
        request=request,
        actor=auth.user,
        target=user,
        detail={"status": user.status},
    )
    db.commit()
    users = db.scalars(select(User).order_by(User.created_at.desc())).all()
    return templates.TemplateResponse(
        request=request,
        name="partials/user_table.html",
        context=template_context(request, auth=auth, users=users),
    )


@router.get("/admin/audit", response_class=HTMLResponse)
def audit_page(
    request: Request,
    auth: AuthContext = Depends(require_admin),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    events = db.scalars(select(AuditEvent).order_by(AuditEvent.occurred_at.desc()).limit(200)).all()
    users = {user.id: user for user in db.scalars(select(User)).all()}
    return templates.TemplateResponse(
        request=request,
        name="admin/audit.html",
        context=template_context(
            request, auth=auth, page_title="Audit activity", events=events, users=users
        ),
    )
