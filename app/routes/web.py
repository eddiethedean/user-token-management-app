from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func, or_, select
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
from app.models import (
    AuditEvent,
    Invitation,
    RefreshSession,
    Role,
    User,
    UserSecret,
    UserStatus,
)
from app.routing import app_path
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
    accept_invitation,
    approve_self_registration,
    authenticate_trusted_identity,
    authenticate_user,
    complete_password_reset,
    complete_self_registration,
    create_invitation,
    create_session,
    deny_self_registration,
    get_valid_invitation,
    get_valid_password_reset,
    get_valid_registration_verification,
    request_password_reset,
    request_self_registration,
    revoke_all_sessions,
    revoke_invitation,
    revoke_session,
)
from app.services.directory import DirectoryUnavailableError, validate_directory_email
from app.services.rate_limit import check_rate_limit
from app.services.secrets import (
    delete_user_secret,
    list_user_secrets,
    require_secret_provider,
    store_user_secret,
)
from app.templating import template_context, templates

router = APIRouter(include_in_schema=False)
ADMIN_PAGE_SIZE = 50


def _safe_next(value: str) -> str:
    is_local_path = (
        value.startswith("/")
        and not value.startswith("//")
        and "\\" not in value
        and not any(ord(character) < 32 for character in value)
    )
    return value if is_local_path else "/profile"


def _user_page(
    db: Session, *, query: str = "", status_filter: str = "", page: int = 1
) -> tuple[list[User], int, int]:
    page = max(1, page)
    statement = select(User)
    count_statement = select(func.count()).select_from(User)
    conditions = []
    cleaned_query = query.strip()[:160]
    if cleaned_query:
        pattern = f"%{cleaned_query}%"
        conditions.append(
            or_(
                User.email.ilike(pattern),
                User.email_original.ilike(pattern),
                User.full_name.ilike(pattern),
                User.organization.ilike(pattern),
            )
        )
    if status_filter in {item.value for item in UserStatus}:
        conditions.append(User.status == status_filter)
    if conditions:
        statement = statement.where(*conditions)
        count_statement = count_statement.where(*conditions)
    total = int(db.scalar(count_statement) or 0)
    page_count = max(1, (total + ADMIN_PAGE_SIZE - 1) // ADMIN_PAGE_SIZE)
    page = min(page, page_count)
    users = db.scalars(
        statement.order_by(User.created_at.desc())
        .offset((page - 1) * ADMIN_PAGE_SIZE)
        .limit(ADMIN_PAGE_SIZE)
    ).all()
    return list(users), total, page


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
    settings: Settings = Depends(get_settings),
) -> HTMLResponse | RedirectResponse:
    if auth:
        return RedirectResponse(app_path(request, _safe_next(next)), status_code=303)
    return templates.TemplateResponse(
        request=request,
        name="auth/login.html",
        context=template_context(
            request,
            page_title="Sign in",
            next=_safe_next(next),
            federated_sign_in=settings.authentication_mode == "trusted_header",
        ),
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
    if settings.authentication_mode != "local_password":
        raise HTTPException(status_code=403, detail="Password sign-in is disabled")
    check_rate_limit(
        db,
        settings,
        request,
        scope="login",
        source_limit=settings.rate_limit_login_per_source,
        account_limit=settings.rate_limit_login_per_account,
        account_key=email,
    )
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


@router.post("/login/federated", response_model=None)
def federated_login_submit(
    request: Request,
    next: str = Form(default="/profile"),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> RedirectResponse:
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
    response = RedirectResponse(app_path(request, _safe_next(next)), status_code=303)
    set_auth_cookies(response, tokens, settings, request)
    return response


@router.get("/register", response_class=HTMLResponse)
def registration_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request=request,
        name="auth/register.html",
        context=template_context(request, page_title="Request access"),
    )


@router.post("/register", response_class=HTMLResponse)
async def registration_submit(
    request: Request,
    email: str = Form(),
    full_name: str = Form(default=""),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> HTMLResponse:
    check_rate_limit(
        db,
        settings,
        request,
        scope="registration",
        source_limit=settings.rate_limit_registration_per_source,
        account_limit=settings.rate_limit_registration_per_account,
        account_key=email,
    )
    try:
        await validate_directory_email(email, settings)
        request_self_registration(
            db,
            settings,
            email=email,
            full_name=full_name,
            request=request,
        )
    except (ValueError, DirectoryUnavailableError) as exc:
        return templates.TemplateResponse(
            request=request,
            name="auth/register.html",
            status_code=503 if isinstance(exc, DirectoryUnavailableError) else 400,
            context=template_context(
                request,
                page_title="Request access",
                error=str(exc),
                email=email,
                full_name=full_name,
            ),
        )
    return templates.TemplateResponse(
        request=request,
        name="auth/register.html",
        status_code=202,
        context=template_context(
            request,
            page_title="Request access",
            success=(
                "Request received. If the address is eligible, check your government email for "
                "a verification link. After verification, an administrator must approve the "
                "request before you can sign in."
            ),
        ),
    )


@router.get("/registration/verify", response_class=HTMLResponse)
def registration_verification_page(
    request: Request,
    token: str,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> HTMLResponse:
    verification = None
    error = ""
    try:
        verification = get_valid_registration_verification(db, settings, token)
    except TokenFlowError as exc:
        error = str(exc)
    return templates.TemplateResponse(
        request=request,
        name="auth/verify_registration.html",
        status_code=400 if error else 200,
        context=template_context(
            request,
            page_title="Verify registration",
            token=token,
            verification=verification,
            error=error,
        ),
    )


@router.post("/registration/verify", response_class=HTMLResponse)
def registration_verification_submit(
    request: Request,
    token: str = Form(),
    password: str = Form(),
    password_confirm: str = Form(),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> HTMLResponse:
    check_rate_limit(
        db,
        settings,
        request,
        scope="registration_verify",
        source_limit=settings.rate_limit_registration_per_source,
    )
    verification = None
    error = ""
    try:
        verification = get_valid_registration_verification(db, settings, token)
        if password != password_confirm:
            raise PasswordPolicyError("Passwords do not match.")
        complete_self_registration(
            db,
            settings,
            raw_token=token,
            password=password,
            request=request,
        )
        return templates.TemplateResponse(
            request=request,
            name="auth/verify_registration.html",
            context=template_context(
                request,
                page_title="Registration awaiting approval",
                success=(
                    "Your government email is verified. Your request is now awaiting administrator "
                    "approval, and you cannot sign in until it is approved. We will email you when "
                    "access is granted."
                ),
            ),
        )
    except (TokenFlowError, PasswordPolicyError) as exc:
        error = str(exc)
    return templates.TemplateResponse(
        request=request,
        name="auth/verify_registration.html",
        status_code=400,
        context=template_context(
            request,
            page_title="Verify registration",
            token=token,
            verification=verification,
            error=error,
        ),
    )


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
    check_rate_limit(
        db,
        settings,
        request,
        scope="password_reset_request",
        source_limit=settings.rate_limit_reset_per_source,
        account_limit=settings.rate_limit_reset_per_account,
        account_key=email,
    )
    request_password_reset(db, settings, email, request)
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
    check_rate_limit(
        db,
        settings,
        request,
        scope="password_reset_complete",
        source_limit=settings.rate_limit_reset_per_source,
    )
    error = ""
    if password != password_confirm:
        error = "Passwords do not match."
    else:
        try:
            complete_password_reset(
                db, settings, raw_token=token, password=password, request=request
            )
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
    check_rate_limit(
        db,
        settings,
        request,
        scope="invitation_accept",
        source_limit=settings.rate_limit_registration_per_source,
    )
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
    update_profile(
        db,
        user=auth.user,
        values=ProfileValues(
            full_name=full_name,
            organization=organization,
            job_title=job_title,
            phone=phone,
        ),
        request=request,
    )
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
    response = templates.TemplateResponse(
        request=request,
        name="profile/security.html",
        context=template_context(
            request,
            auth=auth,
            page_title="Security",
            sessions=sessions,
            secret_slots=list_user_secrets(db, auth.user),
            events=events,
        ),
    )
    response.headers["Cache-Control"] = "no-store"
    return response


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
    if new_password != new_password_confirm:
        error = "New passwords do not match."
    else:
        try:
            change_account_password(
                db,
                settings,
                user=auth.user,
                current_password=current_password,
                new_password=new_password,
                request=request,
            )
        except (CurrentPasswordError, PasswordPolicyError) as exc:
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


@router.post("/security/secrets/{provider}", response_class=HTMLResponse)
async def secret_submit(
    provider: str,
    request: Request,
    token: str = Form(),
    auth: AuthContext = Depends(require_auth),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> HTMLResponse:
    await require_csrf(request, auth.session.csrf_token)
    try:
        specification = require_secret_provider(provider)
        stored = store_user_secret(
            db,
            settings,
            user=auth.user,
            provider=provider,
            token=token,
            request=request,
        )
        error = ""
    except ValueError as exc:
        try:
            specification = require_secret_provider(provider)
        except ValueError as provider_exc:
            raise HTTPException(
                status_code=404, detail="API token provider not found"
            ) from provider_exc
        stored = db.scalar(
            select(UserSecret).where(
                UserSecret.user_id == auth.user.id,
                UserSecret.provider == specification.name,
            )
        )
        error = str(exc)
    response = templates.TemplateResponse(
        request=request,
        name="partials/secret_token_slot.html",
        status_code=400 if error else 200,
        context=template_context(
            request,
            auth=auth,
            provider=specification,
            secret=stored,
            error=error,
            success=f"{specification.label} API token saved." if not error else "",
        ),
    )
    response.headers["Cache-Control"] = "no-store"
    return response


@router.post("/security/secrets/{provider}/delete", response_class=HTMLResponse)
async def secret_delete_submit(
    provider: str,
    request: Request,
    auth: AuthContext = Depends(require_auth),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    await require_csrf(request, auth.session.csrf_token)
    try:
        specification = require_secret_provider(provider)
        deleted = delete_user_secret(db, user=auth.user, provider=provider, request=request)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="API token provider not found") from exc
    if not deleted:
        raise HTTPException(status_code=404, detail="API token is not configured.")
    response = templates.TemplateResponse(
        request=request,
        name="partials/secret_token_slot.html",
        context=template_context(
            request,
            auth=auth,
            provider=specification,
            secret=None,
            success=f"{specification.label} API token deleted.",
        ),
    )
    response.headers["Cache-Control"] = "no-store"
    return response


@router.get("/admin/users", response_class=HTMLResponse)
def users_page(
    request: Request,
    q: str = "",
    status: str = "",
    page: int = 1,
    auth: AuthContext = Depends(require_admin),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    users, total_users, current_page = _user_page(db, query=q, status_filter=status, page=page)
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
            total_users=total_users,
            current_page=current_page,
            page_count=max(1, (total_users + ADMIN_PAGE_SIZE - 1) // ADMIN_PAGE_SIZE),
            user_query=q.strip()[:160],
            status_filter=status if status in {item.value for item in UserStatus} else "",
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
    response_status = 200
    try:
        await validate_directory_email(email, settings)
        create_invitation(
            db,
            settings,
            email=email,
            role_name=role,
            inviter=auth.user,
            request=request,
        )
    except (ValueError, DirectoryUnavailableError) as exc:
        error = str(exc)
        response_status = 503 if isinstance(exc, DirectoryUnavailableError) else 400
    invitations = db.scalars(
        select(Invitation).order_by(Invitation.created_at.desc()).limit(25)
    ).all()
    return templates.TemplateResponse(
        request=request,
        name="partials/invitation_panel.html",
        status_code=response_status,
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
    if user.status == UserStatus.PENDING.value:
        raise HTTPException(status_code=400, detail="Use the registration approval action")
    if user.status == UserStatus.DISABLED.value and (
        not user.email_verified_at or not user.password_hash
    ):
        raise HTTPException(
            status_code=400,
            detail="This account cannot be enabled until its government email is verified",
        )
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
    users, total_users, current_page = _user_page(db)
    return templates.TemplateResponse(
        request=request,
        name="partials/user_table.html",
        context=template_context(
            request,
            auth=auth,
            users=users,
            total_users=total_users,
            current_page=current_page,
            page_count=max(1, (total_users + ADMIN_PAGE_SIZE - 1) // ADMIN_PAGE_SIZE),
        ),
    )


@router.post("/admin/invitations/{invitation_id}/revoke", response_class=HTMLResponse)
async def revoke_invitation_submit(
    invitation_id: str,
    request: Request,
    auth: AuthContext = Depends(require_admin),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    await require_csrf(request, auth.session.csrf_token)
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
    invitations = db.scalars(
        select(Invitation).order_by(Invitation.created_at.desc()).limit(25)
    ).all()
    return templates.TemplateResponse(
        request=request,
        name="partials/invitation_panel.html",
        context=template_context(
            request,
            auth=auth,
            invitations=invitations,
            roles=db.scalars(select(Role).order_by(Role.name)).all(),
            success=f"Invitation revoked for {invitation.email_original}.",
        ),
    )


@router.post("/admin/users/{user_id}/approve", response_class=HTMLResponse)
async def approve_registration_submit(
    user_id: str,
    request: Request,
    auth: AuthContext = Depends(require_admin),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> HTMLResponse:
    await require_csrf(request, auth.session.csrf_token)
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
    users, total_users, current_page = _user_page(db)
    return templates.TemplateResponse(
        request=request,
        name="partials/user_table.html",
        context=template_context(
            request,
            auth=auth,
            users=users,
            total_users=total_users,
            current_page=current_page,
            page_count=max(1, (total_users + ADMIN_PAGE_SIZE - 1) // ADMIN_PAGE_SIZE),
            success=f"Access approved for {user.email_original}.",
        ),
    )


@router.post("/admin/users/{user_id}/deny", response_class=HTMLResponse)
async def deny_registration_submit(
    user_id: str,
    request: Request,
    auth: AuthContext = Depends(require_admin),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> HTMLResponse:
    await require_csrf(request, auth.session.csrf_token)
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
    users, total_users, current_page = _user_page(db)
    return templates.TemplateResponse(
        request=request,
        name="partials/user_table.html",
        context=template_context(
            request,
            auth=auth,
            users=users,
            total_users=total_users,
            current_page=current_page,
            page_count=max(1, (total_users + ADMIN_PAGE_SIZE - 1) // ADMIN_PAGE_SIZE),
            success=f"Registration denied for {user.email_original}.",
        ),
    )


@router.get("/admin/audit", response_class=HTMLResponse)
def audit_page(
    request: Request,
    event_type: str = "",
    outcome: str = "",
    page: int = 1,
    auth: AuthContext = Depends(require_admin),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    page = max(1, page)
    statement = select(AuditEvent)
    count_statement = select(func.count()).select_from(AuditEvent)
    conditions = []
    cleaned_event_type = event_type.strip()[:100]
    cleaned_outcome = outcome.strip()[:20]
    if cleaned_event_type:
        conditions.append(AuditEvent.event_type == cleaned_event_type)
    if cleaned_outcome:
        conditions.append(AuditEvent.outcome == cleaned_outcome)
    if conditions:
        statement = statement.where(*conditions)
        count_statement = count_statement.where(*conditions)
    total_events = int(db.scalar(count_statement) or 0)
    page_count = max(1, (total_events + ADMIN_PAGE_SIZE - 1) // ADMIN_PAGE_SIZE)
    page = min(page, page_count)
    events = db.scalars(
        statement.order_by(AuditEvent.occurred_at.desc())
        .offset((page - 1) * ADMIN_PAGE_SIZE)
        .limit(ADMIN_PAGE_SIZE)
    ).all()
    referenced_user_ids = {
        user_id
        for event in events
        for user_id in (event.actor_user_id, event.target_user_id)
        if user_id
    }
    users = {
        user.id: user
        for user in db.scalars(select(User).where(User.id.in_(referenced_user_ids))).all()
    }
    return templates.TemplateResponse(
        request=request,
        name="admin/audit.html",
        context=template_context(
            request,
            auth=auth,
            page_title="Audit activity",
            events=events,
            users=users,
            total_events=total_events,
            current_page=page,
            page_count=page_count,
            event_type_filter=cleaned_event_type,
            outcome_filter=cleaned_outcome,
        ),
    )
