"""Hedron web routes — full Access Registry UI parity."""

from __future__ import annotations

from urllib.parse import urlencode

from fastapi import Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from hedron import Hedron, Page, html
from hedron.responses import render_component_response
from hedron_core import RenderMode
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
    utcnow,
)
from app.routing import app_path, is_htmx_request
from app.security.csrf import (
    clear_preauth_csrf_cookie,
    issue_preauth_csrf,
    require_csrf,
    require_preauth_csrf,
    set_preauth_csrf_cookie,
)
from app.security.passwords import PasswordPolicyError
from app.services.accounts import (
    CurrentPasswordError,
    ProfileValues,
    change_password as change_account_password,
    update_profile,
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
    lock_administrator_action,
    request_password_reset,
    request_self_registration,
    revoke_all_sessions,
    revoke_invitation,
    revoke_session,
)
from app.services.directory import DirectoryUnavailableError, validate_directory_email
from app.services.rate_limit import check_rate_limit
from app.services.secrets import (
    SecretStorageError,
    delete_user_secret,
    list_user_secrets,
    require_secret_provider,
    store_user_secret,
)
from app.ui.layout import alert_box, app_shell, main_panel, page_heading, side_nav_oob
from app.ui import partials as ui
from app.ui.interactions import interaction_response, ok_fragment
from app.ui.urls import form_action, page_href

ADMIN_PAGE_SIZE = 50
AUDIT_PAGE_SIZE = 50


def _hx_target(request: Request) -> str:
    raw = (request.headers.get("HX-Target") or "").strip()
    if raw and not raw.startswith(("#", ".", "[")) and " " not in raw:
        return f"#{raw}"
    return raw


def _is_main_panel_nav(request: Request) -> bool:
    return is_htmx_request(request) and _hx_target(request) == "#main-panel"


def _safe_next(value: str) -> str:
    is_local_path = (
        value.startswith("/")
        and not value.startswith("//")
        and "\\" not in value
        and not any(ord(character) < 32 for character in value)
    )
    return value if is_local_path else "/profile"


def _render_page(
    page: Page,
    *,
    request: Request | None = None,
    status_code: int = 200,
    headers: dict | None = None,
    authenticated: bool = False,
):
    response = render_component_response(
        page,
        request=request,
        mode=RenderMode.PAGE,
        status_code=status_code,
        extra_headers=headers,
        authenticated=authenticated,
    )
    # Hedron forbids <script> nodes in the tree; inject AR progressive-enhancement JS here.
    html_text = response.body.decode(response.charset or "utf-8")
    app_script = '<script src="/assets/app.js" defer></script>'
    if "app.js" not in html_text:
        if "</body>" in html_text:
            html_text = html_text.replace("</body>", f"{app_script}</body>", 1)
        else:
            html_text += app_script
        response.body = html_text.encode(response.charset or "utf-8")
        response.headers["content-length"] = str(len(response.body))
    return response


def _render_fragment(*nodes: object, request: Request | None = None, status_code: int = 200):
    content = html.div(*nodes) if len(nodes) != 1 else nodes[0]
    return render_component_response(
        content,
        request=request,
        mode=RenderMode.FRAGMENT,
        status_code=status_code,
    )


def _user_page(db: Session, *, query: str = "", status_filter: str = "", page: int = 1):
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


def _security_values(db: Session, auth: AuthContext, settings: Settings, **values):
    now = utcnow()
    sessions = db.scalars(
        select(RefreshSession)
        .where(
            RefreshSession.user_id == auth.user.id,
            RefreshSession.revoked_at.is_(None),
            RefreshSession.idle_expires_at > now,
            RefreshSession.absolute_expires_at > now,
        )
        .order_by(RefreshSession.last_seen_at.desc())
    ).all()
    events = db.scalars(
        select(AuditEvent)
        .where(AuditEvent.target_user_id == auth.user.id)
        .order_by(AuditEvent.occurred_at.desc())
        .limit(12)
    ).all()
    return {
        "sessions": list(sessions),
        "secret_slots": list_user_secrets(db, auth.user),
        "events": list(events),
        "local_password": settings.authentication_mode == "local_password",
        **values,
    }


def _user_listing_values(db: Session, *, query: str = "", status_filter: str = "", page: int = 1, **values):
    cleaned_query = query.strip()[:160]
    cleaned_status = status_filter if status_filter in {item.value for item in UserStatus} else ""
    users, total_users, current_page = _user_page(
        db, query=cleaned_query, status_filter=cleaned_status, page=page
    )
    return {
        "users": users,
        "total_users": total_users,
        "current_page": current_page,
        "page_count": max(1, (total_users + ADMIN_PAGE_SIZE - 1) // ADMIN_PAGE_SIZE),
        "user_query": cleaned_query,
        "status_filter": cleaned_status,
        **values,
    }


def _user_listing_path(request: Request, *, query: str = "", status_filter: str = "", page: int = 1, notice: str = "") -> str:
    parameters = {"q": query, "status": status_filter, "page": max(1, page)}
    if notice:
        parameters["notice"] = notice
    return app_path(request, f"/admin/users?{urlencode(parameters)}")


def _auth_card(*children: object) -> object:
    return html.div(*children, class_="panel auth-card")


def register_routes(app: Hedron) -> None:
    @app.get("/", include_in_schema=False)
    def home(request: Request, auth: AuthContext | None = Depends(get_optional_auth)):
        return RedirectResponse(app_path(request, "/profile" if auth else "/login"), status_code=303)

    @app.get("/login", include_in_schema=False)
    def login_page(
        request: Request,
        next: str = "/profile",
        password: str = "",
        auth: AuthContext | None = Depends(get_optional_auth),
        settings: Settings = Depends(get_settings),
    ):
        if auth:
            return RedirectResponse(app_path(request, _safe_next(next)), status_code=303)
        return _login_html(request, settings, next=_safe_next(next), success="Password changed. Sign in with your new password." if password == "changed" else "")

    @app.post("/login", include_in_schema=False)
    def login_submit(
        request: Request,
        email: str = Form(),
        password: str = Form(max_length=128),
        preauth_csrf_token: str = Form(default="", max_length=256),
        next: str = Form(default="/profile", max_length=2048),
        db: Session = Depends(get_db),
        settings: Settings = Depends(get_settings),
    ):
        require_preauth_csrf(request, preauth_csrf_token, settings)
        if settings.authentication_mode != "local_password":
            raise HTTPException(status_code=403, detail="Password sign-in is disabled")
        check_rate_limit(
            db, settings, request, scope="login",
            source_limit=settings.rate_limit_login_per_source,
            account_limit=settings.rate_limit_login_per_account,
            account_key=email,
        )
        try:
            user = authenticate_user(db, settings, email, password, request)
        except (AuthenticationError, ValueError) as exc:
            return _login_html(request, settings, status_code=400, error=str(exc), email=email, next=_safe_next(next))
        tokens = create_session(db, settings, user, request)
        response = RedirectResponse(app_path(request, _safe_next(next)), status_code=303)
        set_auth_cookies(response, tokens, settings, request)
        clear_preauth_csrf_cookie(response, request, settings)
        return response

    @app.post("/login/federated", include_in_schema=False)
    def federated_login_submit(
        request: Request,
        next: str = Form(default="/profile", max_length=2048),
        preauth_csrf_token: str = Form(default="", max_length=256),
        db: Session = Depends(get_db),
        settings: Settings = Depends(get_settings),
    ):
        require_preauth_csrf(request, preauth_csrf_token, settings)
        check_rate_limit(db, settings, request, scope="federated_login", source_limit=settings.rate_limit_login_per_source)
        try:
            user = authenticate_trusted_identity(db, settings, request)
        except AuthenticationError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc
        tokens = create_session(db, settings, user, request)
        response = RedirectResponse(app_path(request, _safe_next(next)), status_code=303)
        set_auth_cookies(response, tokens, settings, request)
        clear_preauth_csrf_cookie(response, request, settings)
        return response

    def _login_html(request, settings, *, status_code=200, error="", email="", next="/profile", success=""):
        preauth = issue_preauth_csrf(settings)
        federated = settings.authentication_mode == "trusted_header"

        def trust_item(title: str, detail: str) -> object:
            return html.div(
                html.span("✓", aria={"hidden": "true"}),
                html.p(html.strong(title), html.small(detail)),
            )

        intro = html.div(
            html.p("Controlled access", class_="eyebrow"),
            html.h1("Your identity, managed with clarity."),
            html.p(
                "Access your profile, review active sessions, and manage the security "
                "of your government application account."
            ),
            html.div(
                trust_item(
                    "Administrator-approved access",
                    "Invited and self-registered accounts require authorization.",
                ),
                trust_item("Short-lived credentials", "Access tokens expire automatically."),
                trust_item("Audited activity", "Security-sensitive actions are recorded."),
                class_="trust-list",
                aria={"label": "Security features"},
            ),
            class_="auth-intro",
        )

        card_children: list[object] = [
            html.p("Account access", class_="eyebrow"),
            html.h2("Sign in"),
            html.p(
                "Continue through the approved identity-aware proxy using your CAC or "
                "federated credential."
                if federated
                else "Use the government email associated with your approved account.",
                class_="muted",
            ),
        ]
        if success:
            card_children.append(alert_box(success, kind="success"))
        if error:
            card_children.append(alert_box(error))
        if federated:
            card_children.append(
                html.form(
                    html.input(type="hidden", name="next", value=next),
                    html.input(type="hidden", name="preauth_csrf_token", value=preauth),
                    html.button(
                        "Continue with federated sign-in",
                        class_="button button-primary button-wide",
                        type="submit",
                    ),
                    action=form_action("login/federated"),
                    method="post",
                    class_="stack-form",
                )
            )
            card_children.append(
                html.p(
                    "Your identity must already be provisioned and active in this application.",
                    class_="card-footnote",
                )
            )
        else:
            card_children.append(
                html.form(
                    html.input(type="hidden", name="next", value=next),
                    html.input(type="hidden", name="preauth_csrf_token", value=preauth),
                    html.label("Government email", for_="email"),
                    html.input(
                        id="email",
                        name="email",
                        type="email",
                        value=email,
                        required=True,
                        autocomplete="username",
                        maxlength="320",
                        autofocus=True,
                    ),
                    html.div(
                        html.label("Password", for_="password"),
                        html.a("Forgot password?", href=page_href("password/forgot")),
                        class_="label-row",
                    ),
                    html.input(
                        id="password",
                        name="password",
                        type="password",
                        required=True,
                        autocomplete="current-password",
                        maxlength="128",
                    ),
                    html.button(
                        "Show password",
                        class_="password-toggle",
                        type="button",
                        data={"password-toggle": "password"},
                        aria={"pressed": "false"},
                    ),
                    html.button(
                        "Sign in securely",
                        class_="button button-primary button-wide",
                        type="submit",
                    ),
                    action=form_action("login"),
                    method="post",
                    class_="stack-form",
                )
            )
            card_children.append(
                html.p(
                    "Need an account? ",
                    html.a("Request access", href=page_href("register")),
                    ". You must verify your government email and receive administrator "
                    "approval before signing in.",
                    class_="card-footnote",
                )
            )

        layout = html.section(
            intro,
            html.div(*card_children, class_="auth-card"),
            class_="auth-layout",
        )
        page = app_shell(layout, request=request, settings=settings, auth=None, page_title="Sign in")
        response = _render_page(page, request=request, status_code=status_code)
        set_preauth_csrf_cookie(response, request, preauth, settings)
        return response

    @app.get("/register", include_in_schema=False)
    def registration_page(request: Request, settings: Settings = Depends(get_settings)):
        return _register_html(request, settings)

    @app.post("/register", include_in_schema=False)
    async def registration_submit(
        request: Request,
        email: str = Form(max_length=320),
        full_name: str = Form(default="", max_length=160),
        db: Session = Depends(get_db),
        settings: Settings = Depends(get_settings),
    ):
        check_rate_limit(
            db, settings, request, scope="registration",
            source_limit=settings.rate_limit_registration_per_source,
            account_limit=settings.rate_limit_registration_per_account,
            account_key=email,
        )
        try:
            await validate_directory_email(email, settings)
            request_self_registration(db, settings, email=email, full_name=full_name, request=request)
        except (ValueError, DirectoryUnavailableError) as exc:
            return _register_html(
                request, settings,
                status_code=503 if isinstance(exc, DirectoryUnavailableError) else 400,
                error=str(exc), email=email, full_name=full_name,
            )
        return _register_html(
            request, settings, status_code=202,
            success=(
                "Request received. If the address is eligible, check your government email for "
                "a verification link. After verification, an administrator must approve the "
                "request before you can sign in."
            ),
        )

    def _register_html(request, settings, *, status_code=200, error="", success="", email="", full_name=""):
        body = [html.h1("Request access"), alert_box(error), alert_box(success, kind="success")]
        if not success:
            body.append(
                html.form(
                    html.label("Government email", for_="email"),
                    html.input(id="email", name="email", type="email", value=email, required=True),
                    html.label("Full name", for_="full_name"),
                    html.input(id="full_name", name="full_name", value=full_name),
                    html.button("Submit request", class_="button button-primary button-wide", type="submit"),
                    action=form_action("register"), method="post", class_="stack-form",
                )
            )
        body.append(html.p(html.a("Back to sign in", href=page_href("login"))))
        return _render_page(
            app_shell(_auth_card(*body), request=request, settings=settings, auth=None, page_title="Request access"),
            status_code=status_code, request=request)

    @app.get("/registration/verify", include_in_schema=False)
    def registration_verification_page(
        request: Request, token: str, db: Session = Depends(get_db), settings: Settings = Depends(get_settings),
    ):
        error = ""
        verification = None
        try:
            verification = get_valid_registration_verification(db, settings, token)
        except TokenFlowError as exc:
            error = str(exc)
        return _verify_html(request, settings, token=token, verification=verification, error=error, status_code=400 if error else 200)

    @app.post("/registration/verify", include_in_schema=False)
    def registration_verification_submit(
        request: Request,
        token: str = Form(max_length=512),
        password: str = Form(default="", max_length=128),
        password_confirm: str = Form(default="", max_length=128),
        db: Session = Depends(get_db),
        settings: Settings = Depends(get_settings),
    ):
        check_rate_limit(db, settings, request, scope="registration_verify", source_limit=settings.rate_limit_registration_per_source)
        verification = None
        error = ""
        try:
            verification = get_valid_registration_verification(db, settings, token)
            if settings.authentication_mode == "local_password" and password != password_confirm:
                raise PasswordPolicyError("Passwords do not match.")
            complete_self_registration(db, settings, raw_token=token, password=password, request=request)
            return _verify_html(
                request, settings, success=(
                    "Your government email is verified. Your request is now awaiting administrator "
                    "approval, and you cannot sign in until it is approved."
                ),
            )
        except (TokenFlowError, PasswordPolicyError) as exc:
            error = str(exc)
        return _verify_html(request, settings, token=token, verification=verification, error=error, status_code=400)

    def _verify_html(request, settings, *, token="", verification=None, error="", success="", status_code=200):
        body = [html.h1("Verify registration"), alert_box(error), alert_box(success, kind="success")]
        if not success and not error:
            fields = [html.input(type="hidden", name="token", value=token)]
            if settings.authentication_mode == "local_password":
                fields.extend([
                    html.label("Password", for_="password"),
                    html.input(id="password", name="password", type="password", required=True, minlength="15"),
                    html.label("Confirm password", for_="password_confirm"),
                    html.input(id="password_confirm", name="password_confirm", type="password", required=True, minlength="15"),
                ])
            fields.append(html.button("Verify", class_="button button-primary button-wide", type="submit"))
            body.append(html.form(*fields, action=form_action("registration/verify"), method="post", class_="stack-form"))
        elif error:
            body.append(html.p(html.a("Request access again", href=page_href("register"))))
        return _render_page(
            app_shell(_auth_card(*body), request=request, settings=settings, auth=None, page_title="Verify registration"),
            status_code=status_code, request=request)

    @app.post("/logout", include_in_schema=False)
    async def logout_submit(
        request: Request,
        auth: AuthContext = Depends(require_auth),
        db: Session = Depends(get_db),
        settings: Settings = Depends(get_settings),
    ):
        await require_csrf(request, auth.session.csrf_token)
        revoke_session(db, auth.session, actor=auth.user, request=request)
        response = RedirectResponse(app_path(request, "/login"), status_code=303)
        clear_auth_cookies(response, settings, request)
        return response

    @app.get("/password/forgot", include_in_schema=False)
    def forgot_page(request: Request, settings: Settings = Depends(get_settings)):
        if settings.authentication_mode != "local_password":
            raise HTTPException(status_code=404, detail="Not found")
        return _forgot_html(request, settings)

    @app.post("/password/forgot", include_in_schema=False)
    def forgot_submit(
        request: Request,
        email: str = Form(max_length=320),
        db: Session = Depends(get_db),
        settings: Settings = Depends(get_settings),
    ):
        if settings.authentication_mode != "local_password":
            raise HTTPException(status_code=404, detail="Not found")
        check_rate_limit(
            db, settings, request, scope="reset",
            source_limit=settings.rate_limit_reset_per_source,
            account_limit=settings.rate_limit_reset_per_account,
            account_key=email,
        )
        request_password_reset(db, settings, email=email, request=request)
        return _forgot_html(
            request, settings,
            success="If the account exists and can sign in with a password, a reset link was sent.",
        )

    def _forgot_html(request, settings, *, success=""):
        body = [html.h1("Forgot password"), alert_box(success, kind="success")]
        if not success:
            body.append(
                html.form(
                    html.label("Government email", for_="email"),
                    html.input(id="email", name="email", type="email", required=True),
                    html.button("Send reset link", class_="button button-primary button-wide", type="submit"),
                    action=form_action("password/forgot"), method="post", class_="stack-form",
                )
            )
        body.append(html.p(html.a("Back to sign in", href=page_href("login"))))
        return _render_page(app_shell(_auth_card(*body), request=request, settings=settings, auth=None, page_title="Forgot password"), request=request)

    @app.get("/password/reset", include_in_schema=False)
    def reset_page(request: Request, token: str, db: Session = Depends(get_db), settings: Settings = Depends(get_settings)):
        if settings.authentication_mode != "local_password":
            raise HTTPException(status_code=404, detail="Not found")
        error = ""
        try:
            get_valid_password_reset(db, settings, token)
        except TokenFlowError as exc:
            error = str(exc)
        return _reset_html(request, settings, token=token, error=error, status_code=400 if error else 200)

    @app.post("/password/reset", include_in_schema=False)
    def reset_submit(
        request: Request,
        token: str = Form(max_length=512),
        password: str = Form(max_length=128),
        password_confirm: str = Form(max_length=128),
        db: Session = Depends(get_db),
        settings: Settings = Depends(get_settings),
    ):
        if settings.authentication_mode != "local_password":
            raise HTTPException(status_code=404, detail="Not found")
        try:
            if password != password_confirm:
                raise PasswordPolicyError("Passwords do not match.")
            complete_password_reset(db, settings, raw_token=token, password=password, request=request)
            return RedirectResponse(app_path(request, "/login?password=changed"), status_code=303)
        except (TokenFlowError, PasswordPolicyError) as exc:
            return _reset_html(request, settings, token=token, error=str(exc), status_code=400)

    def _reset_html(request, settings, *, token="", error="", status_code=200):
        body = [html.h1("Reset password"), alert_box(error)]
        if not error or token:
            body.append(
                html.form(
                    html.input(type="hidden", name="token", value=token),
                    html.label("New password", for_="password"),
                    html.input(id="password", name="password", type="password", required=True, minlength="15"),
                    html.label("Confirm password", for_="password_confirm"),
                    html.input(id="password_confirm", name="password_confirm", type="password", required=True, minlength="15"),
                    html.button("Update password", class_="button button-primary button-wide", type="submit"),
                    action=form_action("password/reset"), method="post", class_="stack-form",
                )
            )
        return _render_page(
            app_shell(_auth_card(*body), request=request, settings=settings, auth=None, page_title="Reset password"),
            status_code=status_code, request=request)

    @app.get("/invitations/accept", include_in_schema=False)
    def invitation_page(request: Request, token: str, db: Session = Depends(get_db), settings: Settings = Depends(get_settings)):
        error = ""
        invitation = None
        try:
            invitation = get_valid_invitation(db, settings, token)
        except TokenFlowError as exc:
            error = str(exc)
        return _invite_html(request, settings, token=token, invitation=invitation, error=error, status_code=400 if error else 200)

    @app.post("/invitations/accept", include_in_schema=False)
    def invitation_submit(
        request: Request,
        token: str = Form(max_length=512),
        full_name: str = Form(default="", max_length=160),
        password: str = Form(default="", max_length=128),
        password_confirm: str = Form(default="", max_length=128),
        db: Session = Depends(get_db),
        settings: Settings = Depends(get_settings),
    ):
        invitation = None
        error = ""
        try:
            invitation = get_valid_invitation(db, settings, token)
            if settings.authentication_mode == "local_password" and password != password_confirm:
                raise PasswordPolicyError("Passwords do not match.")
            accept_invitation(db, settings, raw_token=token, full_name=full_name, password=password, request=request)
            return RedirectResponse(app_path(request, "/login"), status_code=303)
        except (TokenFlowError, PasswordPolicyError, ValueError) as exc:
            error = str(exc)
        return _invite_html(request, settings, token=token, invitation=invitation, full_name=full_name, error=error, status_code=400)

    def _invite_html(request, settings, *, token="", invitation=None, full_name="", error="", status_code=200):
        body = [html.h1("Accept invitation"), alert_box(error)]
        if invitation and not error:
            fields = [
                html.input(type="hidden", name="token", value=token),
                html.p(f"Invited as {invitation.email_original} ({invitation.role_name})"),
                html.label("Full name", for_="full_name"),
                html.input(id="full_name", name="full_name", value=full_name),
            ]
            if settings.authentication_mode == "local_password":
                fields.extend([
                    html.label("Password", for_="password"),
                    html.input(id="password", name="password", type="password", required=True, minlength="15"),
                    html.label("Confirm password", for_="password_confirm"),
                    html.input(id="password_confirm", name="password_confirm", type="password", required=True, minlength="15"),
                ])
            fields.append(html.button("Accept invitation", class_="button button-primary button-wide", type="submit"))
            body.append(html.form(*fields, action=form_action("invitations/accept"), method="post", class_="stack-form"))
        return _render_page(
            app_shell(_auth_card(*body), request=request, settings=settings, auth=None, page_title="Accept invitation"),
            status_code=status_code, request=request)

    # ---- Authenticated pages ----

    @app.get("/profile", include_in_schema=False)
    async def profile_page(
        request: Request, updated: bool = False, auth: AuthContext = Depends(require_auth), settings: Settings = Depends(get_settings),
    ):
        request.state.hedron_authenticated = True
        csrf = auth.session.csrf_token
        verified_badge = html.span(
            html.span("✓", aria={"hidden": "true"}),
            " Verified email",
            class_="verification-badge",
        )
        body = [
            page_heading(
                "Account profile",
                "Your information",
                "Keep your contact and organizational details current.",
                verified_badge,
            ),
            alert_box("Your profile has been updated." if updated else "", kind="success"),
            html.div(
                html.section(
                    html.div(
                        html.div(
                            html.h2("Profile details"),
                            html.p("Information shown to application administrators."),
                        ),
                        class_="panel-heading",
                    ),
                    ui.profile_form(auth, csrf_token=csrf),
                    class_="panel panel-main",
                ),
                ui.profile_identity(auth),
                class_="content-grid profile-grid",
            ),
        ]
        if _is_main_panel_nav(request):
            return await interaction_response(
                request,
                ok_fragment(
                    main_panel(*body),
                    oob=(side_nav_oob(request, auth),),
                    push_url=app_path(request, "/profile"),
                ),
            )
        return _render_page(
            app_shell(
                *body,
                request=request, settings=settings, auth=auth, page_title="Your profile", csrf_token=csrf,
            ),
            request=request, authenticated=True,
        )

    @app.post("/profile", include_in_schema=False)
    async def profile_submit(
        request: Request,
        full_name: str = Form(default=""),
        organization: str = Form(default=""),
        job_title: str = Form(default=""),
        phone: str = Form(default=""),
        auth: AuthContext = Depends(require_auth),
        db: Session = Depends(get_db),
    ):
        await require_csrf(request, auth.session.csrf_token)
        update_profile(
            db, user=auth.user,
            values=ProfileValues(full_name=full_name, organization=organization, job_title=job_title, phone=phone),
            request=request,
        )
        if not is_htmx_request(request):
            return RedirectResponse(app_path(request, "/profile?updated=true"), status_code=303)
        return await interaction_response(
            request,
            ok_fragment(
                html.div(*ui.profile_response(auth, csrf_token=auth.session.csrf_token, success="")),
                toast="Your profile has been updated.",
            ),
        )

    @app.get("/security", include_in_schema=False)
    async def security_page(
        request: Request, notice: str = "",
        auth: AuthContext = Depends(require_auth), db: Session = Depends(get_db), settings: Settings = Depends(get_settings),
    ):
        request.state.hedron_authenticated = True
        notices = {
            "session-revoked": "The browser session was revoked.",
            "secret-saved": "The API token was saved.",
            "secret-deleted": "The API token was deleted.",
        }
        values = _security_values(db, auth, settings, security_success=notices.get(notice, ""))
        csrf = auth.session.csrf_token
        body = [
            page_heading(
                "Account protection",
                "Security",
                "Manage your password, API tokens, sessions, and recent account activity.",
            ),
            alert_box(values.get("security_success", ""), kind="success"),
            html.div(
                ui.security_tabs(
                    csrf_token=csrf,
                    local_password=values["local_password"],
                    secret_slots=values["secret_slots"],
                    sessions=values["sessions"],
                    auth=auth,
                ),
                class_="security-stack",
            ),
        ]
        if _is_main_panel_nav(request):
            return await interaction_response(
                request,
                ok_fragment(
                    main_panel(*body),
                    oob=(side_nav_oob(request, auth),),
                    push_url=app_path(request, "/security"),
                ),
            )
        page = app_shell(
            *body,
            request=request, settings=settings, auth=auth, page_title="Security", csrf_token=csrf,
        )
        return _render_page(page, request=request, headers={"Cache-Control": "no-store"}, authenticated=True)

    @app.get("/security/activity", include_in_schema=False)
    async def security_activity_fragment(
        request: Request,
        auth: AuthContext = Depends(require_auth),
        db: Session = Depends(get_db),
        settings: Settings = Depends(get_settings),
    ):
        request.state.hedron_authenticated = True
        values = _security_values(db, auth, settings)
        if is_htmx_request(request):
            return await interaction_response(
                request,
                ok_fragment(ui.security_activity(values["events"])),
            )
        return RedirectResponse(app_path(request, "/security"), status_code=303)

    @app.post("/security/password", include_in_schema=False)
    async def password_change_submit(
        request: Request,
        current_password: str = Form(max_length=128),
        new_password: str = Form(max_length=128),
        new_password_confirm: str = Form(max_length=128),
        auth: AuthContext = Depends(require_auth),
        db: Session = Depends(get_db),
        settings: Settings = Depends(get_settings),
    ):
        await require_csrf(request, auth.session.csrf_token)
        if settings.authentication_mode != "local_password":
            raise HTTPException(status_code=403, detail="Password changes are disabled")
        error = ""
        if new_password != new_password_confirm:
            error = "New passwords do not match."
        else:
            try:
                change_account_password(
                    db, settings, user=auth.user, current_password=current_password,
                    new_password=new_password, request=request,
                )
            except (CurrentPasswordError, PasswordPolicyError) as exc:
                error = str(exc)
        if not error:
            if is_htmx_request(request):
                response = await interaction_response(
                    request,
                    ok_fragment(
                        html.div(),
                        redirect=app_path(request, "/login?password=changed"),
                    ),
                )
                clear_auth_cookies(response, settings, request)
                return response
            response = RedirectResponse(app_path(request, "/login?password=changed"), status_code=303)
            clear_auth_cookies(response, settings, request)
            return response
        if is_htmx_request(request):
            return await interaction_response(
                request,
                ok_fragment(
                    ui.password_form(csrf_token=auth.session.csrf_token, error=error),
                    status_code=400,
                ),
            )
        csrf = auth.session.csrf_token
        return _render_page(
            app_shell(
                page_heading(
                    "Account protection",
                    "Security",
                    "Manage your password, API tokens, sessions, and recent account activity.",
                ),
                html.section(ui.password_form(csrf_token=csrf, error=error), class_="panel"),
                request=request,
                settings=settings,
                auth=auth,
                page_title="Security",
                csrf_token=csrf,
            ),
            status_code=400,
            headers={"Cache-Control": "no-store"}, request=request, authenticated=True)

    @app.post("/security/sessions/{session_id}/revoke", include_in_schema=False)
    async def revoke_session_submit(
        session_id: str, request: Request,
        auth: AuthContext = Depends(require_auth), db: Session = Depends(get_db), settings: Settings = Depends(get_settings),
    ):
        await require_csrf(request, auth.session.csrf_token)
        session = db.get(RefreshSession, session_id)
        if not session or session.user_id != auth.user.id:
            raise HTTPException(status_code=404, detail="Session not found")
        revoke_session(db, session, actor=auth.user, request=request)
        if not is_htmx_request(request):
            return RedirectResponse(app_path(request, "/security?notice=session-revoked"), status_code=303)
        values = _security_values(db, auth, settings)
        return await interaction_response(
            request,
            ok_fragment(
                html.div(
                    ui.session_list(values["sessions"], auth=auth, csrf_token=auth.session.csrf_token),
                    ui.session_count(values["sessions"], oob=True),
                    ui.security_activity(values["events"], oob=True),
                ),
                toast="The browser session was revoked.",
            ),
        )

    @app.post("/security/secrets/{provider}", include_in_schema=False)
    async def secret_submit(
        provider: str, request: Request, token: str = Form(max_length=8192),
        auth: AuthContext = Depends(require_auth), db: Session = Depends(get_db), settings: Settings = Depends(get_settings),
    ):
        await require_csrf(request, auth.session.csrf_token)
        try:
            specification = require_secret_provider(provider)
            stored = store_user_secret(db, settings, user=auth.user, provider=provider, token=token, request=request)
            error = ""
            response_status = 200
        except (ValueError, SecretStorageError) as exc:
            try:
                specification = require_secret_provider(provider)
            except ValueError as provider_exc:
                raise HTTPException(status_code=404, detail="API token provider not found") from provider_exc
            stored = db.scalar(select(UserSecret).where(UserSecret.user_id == auth.user.id, UserSecret.provider == specification.name))
            error = str(exc)
            response_status = 503 if isinstance(exc, SecretStorageError) else 400
        if not is_htmx_request(request):
            if not error:
                return RedirectResponse(app_path(request, "/security?notice=secret-saved"), status_code=303)
            raise HTTPException(status_code=response_status, detail=error)
        events = _security_values(db, auth, settings)["events"]
        slot = ui.secret_slot(
            specification, stored, csrf_token=auth.session.csrf_token, error=error,
            success=f"{specification.label} API token saved." if not error else "",
        )
        if error:
            return await interaction_response(
                request,
                ok_fragment(
                    html.div(slot, ui.security_activity(events, oob=True)),
                    status_code=response_status,
                    toast=error,
                    toast_tone="danger",
                ),
            )
        return await interaction_response(
            request,
            ok_fragment(
                html.div(slot, ui.security_activity(events, oob=True)),
                toast=f"{specification.label} API token saved.",
            ),
        )

    @app.post("/security/secrets/{provider}/delete", include_in_schema=False)
    async def secret_delete_submit(
        provider: str, request: Request,
        auth: AuthContext = Depends(require_auth), db: Session = Depends(get_db), settings: Settings = Depends(get_settings),
    ):
        await require_csrf(request, auth.session.csrf_token)
        try:
            specification = require_secret_provider(provider)
            deleted = delete_user_secret(db, user=auth.user, provider=provider, request=request)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail="API token provider not found") from exc
        if not deleted:
            raise HTTPException(status_code=404, detail="API token is not configured.")
        if not is_htmx_request(request):
            return RedirectResponse(app_path(request, "/security?notice=secret-deleted"), status_code=303)
        events = _security_values(db, auth, settings)["events"]
        return await interaction_response(
            request,
            ok_fragment(
                html.div(
                    ui.secret_slot(
                        specification,
                        None,
                        csrf_token=auth.session.csrf_token,
                        success=f"{specification.label} API token deleted.",
                    ),
                    ui.security_activity(events, oob=True),
                ),
                toast=f"{specification.label} API token deleted.",
            ),
        )

    # ---- Admin ----

    @app.get("/admin/users", include_in_schema=False)
    async def users_page(
        request: Request, q: str = "", status: str = "", page: int = 1, notice: str = "",
        auth: AuthContext = Depends(require_admin), db: Session = Depends(get_db), settings: Settings = Depends(get_settings),
    ):
        request.state.hedron_authenticated = True
        user_notices = {
            "status-updated": "The account status was updated.",
            "registration-approved": "The registration was approved.",
            "registration-denied": "The registration was denied.",
        }
        invitation_notices = {
            "invitation-queued": "The invitation was queued for delivery.",
            "invitation-revoked": "The invitation was revoked.",
        }
        listing = _user_listing_values(db, query=q, status_filter=status, page=page, user_success=user_notices.get(notice, ""))
        csrf = auth.session.csrf_token
        directory = ui.user_directory(
            listing["users"], csrf_token=csrf, query=listing["user_query"],
            status_filter=listing["status_filter"], page=listing["current_page"],
            page_count=listing["page_count"], total_users=listing["total_users"],
            page_size=ADMIN_PAGE_SIZE, success=listing.get("user_success", ""),
        )
        if is_htmx_request(request) and not _is_main_panel_nav(request):
            return await interaction_response(
                request,
                ok_fragment(
                    html.div(directory, ui.user_match_count(listing["total_users"], oob=True)),
                ),
            )
        invitations = list(db.scalars(select(Invitation).order_by(Invitation.created_at.desc()).limit(25)).all())
        roles = list(db.scalars(select(Role).order_by(Role.name)).all())
        body = [
            page_heading(
                "Administration",
                "Users and invitations",
                "Provision accounts, review status, and control application access.",
                ui.user_match_count(listing["total_users"]),
            ),
            html.div(
                html.section(
                    html.div(html.h2("Directory"), html.p("All application-managed identities."), class_="panel-heading"),
                    directory,
                    class_="panel panel-main",
                ),
                html.aside(
                    ui.invitation_panel(
                        invitations, roles, csrf_token=csrf,
                        success=invitation_notices.get(notice, ""),
                    )
                ),
                class_="admin-layout",
            ),
        ]
        if _is_main_panel_nav(request):
            return await interaction_response(
                request,
                ok_fragment(
                    main_panel(*body),
                    oob=(side_nav_oob(request, auth),),
                    push_url=app_path(request, "/admin/users"),
                ),
            )
        return _render_page(
            app_shell(
                *body,
                request=request, settings=settings, auth=auth, page_title="User administration", csrf_token=csrf,
            ),
            request=request, authenticated=True,
        )

    @app.post("/admin/invitations", include_in_schema=False)
    async def invite_submit(
        request: Request, email: str = Form(max_length=320), role: str = Form(default="user", max_length=64),
        auth: AuthContext = Depends(require_admin), db: Session = Depends(get_db), settings: Settings = Depends(get_settings),
    ):
        await require_csrf(request, auth.session.csrf_token)
        error = ""
        response_status = 200
        try:
            await validate_directory_email(email, settings)
            create_invitation(db, settings, email=email, role_name=role, inviter=auth.user, request=request)
        except (ValueError, DirectoryUnavailableError) as exc:
            error = str(exc)
            response_status = 503 if isinstance(exc, DirectoryUnavailableError) else 400
        invitations = list(db.scalars(select(Invitation).order_by(Invitation.created_at.desc()).limit(25)).all())
        roles = list(db.scalars(select(Role).order_by(Role.name)).all())
        if not is_htmx_request(request) and not error:
            return RedirectResponse(app_path(request, "/admin/users?notice=invitation-queued"), status_code=303)
        if not is_htmx_request(request):
            raise HTTPException(status_code=response_status, detail=error or "Invitation error")
        panel = ui.invitation_panel(
            invitations, roles, csrf_token=auth.session.csrf_token, error=error,
            success="Invitation queued for delivery." if not error else "",
        )
        if error:
            return await interaction_response(
                request,
                ok_fragment(panel, status_code=response_status, toast=error, toast_tone="danger"),
            )
        return await interaction_response(
            request,
            ok_fragment(panel, toast="Invitation queued for delivery."),
        )

    async def _admin_user_mutation(request, user_id, auth, db, settings, *, action: str, q="", status="", page=1):
        await require_csrf(request, auth.session.csrf_token)
        if not lock_administrator_action(db, auth.user):
            db.rollback()
            raise HTTPException(status_code=403, detail="Administrator required")
        user = db.get(User, user_id)
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        notice = "status-updated"
        toast = "The account status was updated."
        if action == "toggle":
            if user.id == auth.user.id:
                raise HTTPException(status_code=400, detail="You cannot disable your own account")
            if user.status == UserStatus.PENDING.value:
                raise HTTPException(status_code=400, detail="Use the registration approval action")
            if user.status == UserStatus.DISABLED.value and (
                not user.email_verified_at
                or (settings.authentication_mode == "local_password" and not user.password_hash)
            ):
                raise HTTPException(status_code=400, detail="This account cannot be enabled until its government email is verified")
            user.status = UserStatus.DISABLED.value if user.status == UserStatus.ACTIVE.value else UserStatus.ACTIVE.value
            user.security_version += 1
            if not user.is_active:
                revoke_all_sessions(db, user)
            record_event(db, "admin.user.status_changed", request=request, actor=auth.user, target=user, detail={"status": user.status})
            db.commit()
        elif action == "approve":
            try:
                approve_self_registration(
                    db, settings, user=user, administrator=auth.user, request=request
                )
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            notice = "registration-approved"
            toast = "The registration was approved."
        elif action == "deny":
            try:
                deny_self_registration(
                    db, settings, user=user, administrator=auth.user, request=request
                )
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            notice = "registration-denied"
            toast = "The registration was denied."
        if not is_htmx_request(request):
            return RedirectResponse(_user_listing_path(request, query=q, status_filter=status, page=page, notice=notice), status_code=303)
        listing = _user_listing_values(db, query=q, status_filter=status, page=page)
        return await interaction_response(
            request,
            ok_fragment(
                html.div(
                    ui.user_table(
                        listing["users"], csrf_token=auth.session.csrf_token,
                        query=listing["user_query"], status_filter=listing["status_filter"],
                        page=listing["current_page"], page_count=listing["page_count"],
                        total_users=listing["total_users"], page_size=ADMIN_PAGE_SIZE,
                    ),
                    ui.user_match_count(listing["total_users"], oob=True),
                ),
                toast=toast,
            ),
        )

    @app.post("/admin/users/{user_id}/toggle", include_in_schema=False)
    async def toggle_user(
        user_id: str, request: Request, q: str = Form(default=""), status: str = Form(default=""), page: int = Form(default=1),
        auth: AuthContext = Depends(require_admin), db: Session = Depends(get_db), settings: Settings = Depends(get_settings),
    ):
        return await _admin_user_mutation(request, user_id, auth, db, settings, action="toggle", q=q, status=status, page=page)

    @app.post("/admin/users/{user_id}/approve", include_in_schema=False)
    async def approve_user(
        user_id: str, request: Request, q: str = Form(default=""), status: str = Form(default=""), page: int = Form(default=1),
        auth: AuthContext = Depends(require_admin), db: Session = Depends(get_db), settings: Settings = Depends(get_settings),
    ):
        return await _admin_user_mutation(request, user_id, auth, db, settings, action="approve", q=q, status=status, page=page)

    @app.post("/admin/users/{user_id}/deny", include_in_schema=False)
    async def deny_user(
        user_id: str, request: Request, q: str = Form(default=""), status: str = Form(default=""), page: int = Form(default=1),
        auth: AuthContext = Depends(require_admin), db: Session = Depends(get_db), settings: Settings = Depends(get_settings),
    ):
        return await _admin_user_mutation(request, user_id, auth, db, settings, action="deny", q=q, status=status, page=page)

    @app.post("/admin/invitations/{invitation_id}/revoke", include_in_schema=False)
    async def revoke_invitation_submit(
        invitation_id: str, request: Request,
        auth: AuthContext = Depends(require_admin), db: Session = Depends(get_db), settings: Settings = Depends(get_settings),
    ):
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
        if not is_htmx_request(request):
            return RedirectResponse(app_path(request, "/admin/users?notice=invitation-revoked"), status_code=303)
        invitations = list(db.scalars(select(Invitation).order_by(Invitation.created_at.desc()).limit(25)).all())
        roles = list(db.scalars(select(Role).order_by(Role.name)).all())
        return await interaction_response(
            request,
            ok_fragment(
                ui.invitation_panel(
                    invitations, roles, csrf_token=auth.session.csrf_token, success="The invitation was revoked."
                ),
                toast="The invitation was revoked.",
            ),
        )

    @app.get("/admin/audit", include_in_schema=False)
    async def audit_page(
        request: Request, event_type: str = "", outcome: str = "", page: int = 1,
        auth: AuthContext = Depends(require_admin), db: Session = Depends(get_db), settings: Settings = Depends(get_settings),
    ):
        request.state.hedron_authenticated = True
        page = max(1, page)
        statement = select(AuditEvent)
        count_statement = select(func.count()).select_from(AuditEvent)
        conditions = []
        et = event_type.strip()[:100]
        oc = outcome.strip()[:40]
        if et:
            conditions.append(AuditEvent.event_type.ilike(f"%{et}%"))
        if oc:
            conditions.append(AuditEvent.outcome.ilike(f"%{oc}%"))
        if conditions:
            statement = statement.where(*conditions)
            count_statement = count_statement.where(*conditions)
        total = int(db.scalar(count_statement) or 0)
        page_count = max(1, (total + AUDIT_PAGE_SIZE - 1) // AUDIT_PAGE_SIZE)
        page = min(page, page_count)
        events = list(
            db.scalars(
                statement.order_by(AuditEvent.occurred_at.desc())
                .offset((page - 1) * AUDIT_PAGE_SIZE)
                .limit(AUDIT_PAGE_SIZE)
            ).all()
        )
        results = ui.audit_results(
            events, event_type_filter=et, outcome_filter=oc,
            current_page=page, page_count=page_count, total_events=total,
            page_size=AUDIT_PAGE_SIZE,
        )
        if is_htmx_request(request) and not _is_main_panel_nav(request):
            return await interaction_response(
                request,
                ok_fragment(html.div(results, ui.audit_match_count(total, oob=True))),
            )
        csrf = auth.session.csrf_token
        body = [
            page_heading(
                "Administration",
                "Audit activity",
                "Security-relevant actions recorded by the application.",
                ui.audit_match_count(total),
            ),
            html.section(
                ui.audit_results_lazy(event_type=et, outcome=oc, page=page)
                if not (et or oc or page > 1)
                else results,
                class_="panel",
            ),
        ]
        if _is_main_panel_nav(request):
            return await interaction_response(
                request,
                ok_fragment(
                    main_panel(*body),
                    oob=(side_nav_oob(request, auth),),
                    push_url=app_path(request, "/admin/audit"),
                ),
            )
        return _render_page(
            app_shell(
                *body,
                request=request, settings=settings, auth=auth, page_title="Audit activity", csrf_token=csrf,
            ),
            request=request, authenticated=True,
        )
