"""Unauthenticated auth flows: login, registration, password reset, invitations."""

from __future__ import annotations

from fastapi import Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from hedron import Hedron, html
from sqlalchemy.orm import Session
from starlette.responses import Response

from app.config import Settings, get_settings
from app.database import get_db
from app.dependencies import (
    AuthContext,
    clear_auth_cookies,
    get_optional_auth,
    require_auth,
    set_auth_cookies,
)
from app.models import Invitation, RegistrationVerification
from app.routing import app_path
from app.security.csrf import (
    clear_preauth_csrf_cookie,
    issue_preauth_csrf,
    require_csrf,
    require_preauth_csrf,
    set_preauth_csrf_cookie,
)
from app.security.passwords import PasswordPolicyError
from app.services.auth import (
    AuthenticationError,
    TokenFlowError,
    accept_invitation,
    authenticate_trusted_identity,
    authenticate_user,
    complete_password_reset,
    complete_self_registration,
    create_session,
    get_valid_invitation,
    get_valid_password_reset,
    get_valid_registration_verification,
    request_password_reset,
    request_self_registration,
    revoke_session,
)
from app.services.directory import DirectoryUnavailableError, validate_directory_email
from app.services.rate_limit import check_rate_limit
from app.ui.http import auth_card, render_page, safe_next
from app.ui.layout import alert_box, app_shell
from app.ui.urls import form_action, page_href


def register_auth_routes(app: Hedron) -> None:
    @app.get("/login", include_in_schema=False)
    def login_page(
        request: Request,
        next: str = "/profile",
        password: str = "",
        auth: AuthContext | None = Depends(get_optional_auth),
        settings: Settings = Depends(get_settings),
    ) -> Response:
        if auth:
            return RedirectResponse(app_path(request, safe_next(next)), status_code=303)
        return _login_html(
            request,
            settings,
            next=safe_next(next),
            success="Password changed. Sign in with your new password."
            if password == "changed"
            else "",
        )

    @app.post("/login", include_in_schema=False)
    def login_submit(
        request: Request,
        email: str = Form(),
        password: str = Form(max_length=128),
        preauth_csrf_token: str = Form(default="", max_length=256),
        next: str = Form(default="/profile", max_length=2048),
        db: Session = Depends(get_db),
        settings: Settings = Depends(get_settings),
    ) -> Response:
        require_preauth_csrf(request, preauth_csrf_token, settings)
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
            return _login_html(
                request,
                settings,
                status_code=400,
                error=str(exc),
                email=email,
                next=safe_next(next),
            )
        tokens = create_session(db, settings, user, request)
        response = RedirectResponse(app_path(request, safe_next(next)), status_code=303)
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
    ) -> Response:
        require_preauth_csrf(request, preauth_csrf_token, settings)
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
        response = RedirectResponse(app_path(request, safe_next(next)), status_code=303)
        set_auth_cookies(response, tokens, settings, request)
        clear_preauth_csrf_cookie(response, request, settings)
        return response

    def _login_html(
        request: Request,
        settings: Settings,
        *,
        status_code: int = 200,
        error: str = "",
        email: str = "",
        next: str = "/profile",
        success: str = "",
    ) -> Response:
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
        page = app_shell(
            layout, request=request, settings=settings, auth=None, page_title="Sign in"
        )
        response = render_page(page, request=request, status_code=status_code)
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
    ) -> Response:
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
                db, settings, email=email, full_name=full_name, request=request
            )
        except (ValueError, DirectoryUnavailableError) as exc:
            return _register_html(
                request,
                settings,
                status_code=503 if isinstance(exc, DirectoryUnavailableError) else 400,
                error=str(exc),
                email=email,
                full_name=full_name,
            )
        return _register_html(
            request,
            settings,
            status_code=202,
            success=(
                "Request received. If the address is eligible, check your government email for "
                "a verification link. After verification, an administrator must approve the "
                "request before you can sign in."
            ),
        )

    def _register_html(
        request: Request,
        settings: Settings,
        *,
        status_code: int = 200,
        error: str = "",
        success: str = "",
        email: str = "",
        full_name: str = "",
    ) -> Response:
        body = [html.h1("Request access"), alert_box(error), alert_box(success, kind="success")]
        if not success:
            body.append(
                html.form(
                    html.label("Government email", for_="email"),
                    html.input(id="email", name="email", type="email", value=email, required=True),
                    html.label("Full name", for_="full_name"),
                    html.input(id="full_name", name="full_name", value=full_name),
                    html.button(
                        "Submit request", class_="button button-primary button-wide", type="submit"
                    ),
                    action=form_action("register"),
                    method="post",
                    class_="stack-form",
                )
            )
        body.append(html.p(html.a("Back to sign in", href=page_href("login"))))
        return render_page(
            app_shell(
                auth_card(*body),
                request=request,
                settings=settings,
                auth=None,
                page_title="Request access",
            ),
            status_code=status_code,
            request=request,
        )

    @app.get("/registration/verify", include_in_schema=False)
    def registration_verification_page(
        request: Request,
        token: str,
        db: Session = Depends(get_db),
        settings: Settings = Depends(get_settings),
    ) -> Response:
        error = ""
        verification = None
        try:
            verification = get_valid_registration_verification(db, settings, token)
        except TokenFlowError as exc:
            error = str(exc)
        return _verify_html(
            request,
            settings,
            token=token,
            verification=verification,
            error=error,
            status_code=400 if error else 200,
        )

    @app.post("/registration/verify", include_in_schema=False)
    def registration_verification_submit(
        request: Request,
        token: str = Form(max_length=512),
        password: str = Form(default="", max_length=128),
        password_confirm: str = Form(default="", max_length=128),
        db: Session = Depends(get_db),
        settings: Settings = Depends(get_settings),
    ) -> Response:
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
            if settings.authentication_mode == "local_password" and password != password_confirm:
                raise PasswordPolicyError("Passwords do not match.")
            complete_self_registration(
                db, settings, raw_token=token, password=password, request=request
            )
            return _verify_html(
                request,
                settings,
                success=(
                    "Your government email is verified. Your request is now awaiting administrator "
                    "approval, and you cannot sign in until it is approved."
                ),
            )
        except (TokenFlowError, PasswordPolicyError) as exc:
            error = str(exc)
        return _verify_html(
            request, settings, token=token, verification=verification, error=error, status_code=400
        )

    def _verify_html(
        request: Request,
        settings: Settings,
        *,
        token: str = "",
        verification: RegistrationVerification | None = None,
        error: str = "",
        success: str = "",
        status_code: int = 200,
    ) -> Response:
        body = [
            html.h1("Verify registration"),
            alert_box(error),
            alert_box(success, kind="success"),
        ]
        if not success and not error:
            fields = [html.input(type="hidden", name="token", value=token)]
            if settings.authentication_mode == "local_password":
                fields.extend(
                    [
                        html.label("Password", for_="password"),
                        html.input(
                            id="password",
                            name="password",
                            type="password",
                            required=True,
                            minlength="15",
                        ),
                        html.label("Confirm password", for_="password_confirm"),
                        html.input(
                            id="password_confirm",
                            name="password_confirm",
                            type="password",
                            required=True,
                            minlength="15",
                        ),
                    ]
                )
            fields.append(
                html.button("Verify", class_="button button-primary button-wide", type="submit")
            )
            body.append(
                html.form(
                    *fields,
                    action=form_action("registration/verify"),
                    method="post",
                    class_="stack-form",
                )
            )
        elif error:
            body.append(html.p(html.a("Request access again", href=page_href("register"))))
        return render_page(
            app_shell(
                auth_card(*body),
                request=request,
                settings=settings,
                auth=None,
                page_title="Verify registration",
            ),
            status_code=status_code,
            request=request,
        )

    @app.post("/logout", include_in_schema=False)
    async def logout_submit(
        request: Request,
        auth: AuthContext = Depends(require_auth),
        db: Session = Depends(get_db),
        settings: Settings = Depends(get_settings),
    ) -> Response:
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
    ) -> Response:
        if settings.authentication_mode != "local_password":
            raise HTTPException(status_code=404, detail="Not found")
        check_rate_limit(
            db,
            settings,
            request,
            scope="reset",
            source_limit=settings.rate_limit_reset_per_source,
            account_limit=settings.rate_limit_reset_per_account,
            account_key=email,
        )
        request_password_reset(db, settings, email=email, request=request)
        return _forgot_html(
            request,
            settings,
            success="If the account exists and can sign in with a password, a reset link was sent.",
        )

    def _forgot_html(request: Request, settings: Settings, *, success: str = "") -> Response:
        body = [html.h1("Forgot password"), alert_box(success, kind="success")]
        if not success:
            body.append(
                html.form(
                    html.label("Government email", for_="email"),
                    html.input(id="email", name="email", type="email", required=True),
                    html.button(
                        "Send reset link", class_="button button-primary button-wide", type="submit"
                    ),
                    action=form_action("password/forgot"),
                    method="post",
                    class_="stack-form",
                )
            )
        body.append(html.p(html.a("Back to sign in", href=page_href("login"))))
        return render_page(
            app_shell(
                auth_card(*body),
                request=request,
                settings=settings,
                auth=None,
                page_title="Forgot password",
            ),
            request=request,
        )

    @app.get("/password/reset", include_in_schema=False)
    def reset_page(
        request: Request,
        token: str,
        db: Session = Depends(get_db),
        settings: Settings = Depends(get_settings),
    ) -> Response:
        if settings.authentication_mode != "local_password":
            raise HTTPException(status_code=404, detail="Not found")
        error = ""
        try:
            get_valid_password_reset(db, settings, token)
        except TokenFlowError as exc:
            error = str(exc)
        return _reset_html(
            request, settings, token=token, error=error, status_code=400 if error else 200
        )

    @app.post("/password/reset", include_in_schema=False)
    def reset_submit(
        request: Request,
        token: str = Form(max_length=512),
        password: str = Form(max_length=128),
        password_confirm: str = Form(max_length=128),
        db: Session = Depends(get_db),
        settings: Settings = Depends(get_settings),
    ) -> Response:
        if settings.authentication_mode != "local_password":
            raise HTTPException(status_code=404, detail="Not found")
        try:
            if password != password_confirm:
                raise PasswordPolicyError("Passwords do not match.")
            complete_password_reset(
                db, settings, raw_token=token, password=password, request=request
            )
            return RedirectResponse(app_path(request, "/login?password=changed"), status_code=303)
        except (TokenFlowError, PasswordPolicyError) as exc:
            return _reset_html(request, settings, token=token, error=str(exc), status_code=400)

    def _reset_html(
        request: Request,
        settings: Settings,
        *,
        token: str = "",
        error: str = "",
        status_code: int = 200,
    ) -> Response:
        body = [html.h1("Reset password"), alert_box(error)]
        if not error or token:
            body.append(
                html.form(
                    html.input(type="hidden", name="token", value=token),
                    html.label("New password", for_="password"),
                    html.input(
                        id="password",
                        name="password",
                        type="password",
                        required=True,
                        minlength="15",
                    ),
                    html.label("Confirm password", for_="password_confirm"),
                    html.input(
                        id="password_confirm",
                        name="password_confirm",
                        type="password",
                        required=True,
                        minlength="15",
                    ),
                    html.button(
                        "Update password", class_="button button-primary button-wide", type="submit"
                    ),
                    action=form_action("password/reset"),
                    method="post",
                    class_="stack-form",
                )
            )
        return render_page(
            app_shell(
                auth_card(*body),
                request=request,
                settings=settings,
                auth=None,
                page_title="Reset password",
            ),
            status_code=status_code,
            request=request,
        )

    @app.get("/invitations/accept", include_in_schema=False)
    def invitation_page(
        request: Request,
        token: str,
        db: Session = Depends(get_db),
        settings: Settings = Depends(get_settings),
    ) -> Response:
        error = ""
        invitation = None
        try:
            invitation = get_valid_invitation(db, settings, token)
        except TokenFlowError as exc:
            error = str(exc)
        return _invite_html(
            request,
            settings,
            token=token,
            invitation=invitation,
            error=error,
            status_code=400 if error else 200,
        )

    @app.post("/invitations/accept", include_in_schema=False)
    def invitation_submit(
        request: Request,
        token: str = Form(max_length=512),
        full_name: str = Form(default="", max_length=160),
        password: str = Form(default="", max_length=128),
        password_confirm: str = Form(default="", max_length=128),
        db: Session = Depends(get_db),
        settings: Settings = Depends(get_settings),
    ) -> Response:
        invitation = None
        error = ""
        try:
            invitation = get_valid_invitation(db, settings, token)
            if settings.authentication_mode == "local_password" and password != password_confirm:
                raise PasswordPolicyError("Passwords do not match.")
            accept_invitation(
                db,
                settings,
                raw_token=token,
                full_name=full_name,
                password=password,
                request=request,
            )
            return RedirectResponse(app_path(request, "/login"), status_code=303)
        except (TokenFlowError, PasswordPolicyError, ValueError) as exc:
            error = str(exc)
        return _invite_html(
            request,
            settings,
            token=token,
            invitation=invitation,
            full_name=full_name,
            error=error,
            status_code=400,
        )

    def _invite_html(
        request: Request,
        settings: Settings,
        *,
        token: str = "",
        invitation: Invitation | None = None,
        full_name: str = "",
        error: str = "",
        status_code: int = 200,
    ) -> Response:
        body = [html.h1("Accept invitation"), alert_box(error)]
        if invitation and not error:
            fields = [
                html.input(type="hidden", name="token", value=token),
                html.p(f"Invited as {invitation.email_original} ({invitation.role_name})"),
                html.label("Full name", for_="full_name"),
                html.input(id="full_name", name="full_name", value=full_name),
            ]
            if settings.authentication_mode == "local_password":
                fields.extend(
                    [
                        html.label("Password", for_="password"),
                        html.input(
                            id="password",
                            name="password",
                            type="password",
                            required=True,
                            minlength="15",
                        ),
                        html.label("Confirm password", for_="password_confirm"),
                        html.input(
                            id="password_confirm",
                            name="password_confirm",
                            type="password",
                            required=True,
                            minlength="15",
                        ),
                    ]
                )
            fields.append(
                html.button(
                    "Accept invitation", class_="button button-primary button-wide", type="submit"
                )
            )
            body.append(
                html.form(
                    *fields,
                    action=form_action("invitations/accept"),
                    method="post",
                    class_="stack-form",
                )
            )
        return render_page(
            app_shell(
                auth_card(*body),
                request=request,
                settings=settings,
                auth=None,
                page_title="Accept invitation",
            ),
            status_code=status_code,
            request=request,
        )
