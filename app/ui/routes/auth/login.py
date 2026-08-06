"""Login, federated sign-in, and logout."""

from __future__ import annotations

from fastapi import HTTPException, Request
from fastapi.responses import RedirectResponse
from hedron import (
    Card,
    FormField,
    Heading,
    Hedron,
    Section,
    Stack,
    Text,
    TextInput,
    html,
)
from hedron import (
    Form as HedronForm,
)
from hedron_core import NodeLike
from starlette.responses import Response

from app.config import Settings
from app.dependencies import (
    Auth,
    DbSession,
    OptionalAuth,
    SettingsDep,
    clear_auth_cookies,
    set_auth_cookies,
)
from app.routing import app_path
from app.security.csrf import (
    clear_preauth_csrf_cookie,
    issue_preauth_csrf,
    require_csrf,
    require_preauth_csrf,
    set_preauth_csrf_cookie,
)
from app.services.auth import (
    AuthenticationError,
    authenticate_trusted_identity,
    authenticate_user,
    create_session,
    revoke_session,
)
from app.services.rate_limit import check_rate_limit
from app.ui.http import render_page, safe_next
from app.ui.layout import alert_box, app_shell
from app.ui.params import (
    LoginEmailForm,
    NextForm,
    NextQuery,
    PasswordForm,
    PasswordNoticeQuery,
    PreauthCsrfForm,
)
from app.ui.urls import form_action, page_href


def register_login_routes(app: Hedron) -> None:
    @app.get("/login", include_in_schema=False)
    def login_page(
        request: Request,
        auth: OptionalAuth,
        settings: SettingsDep,
        next: NextQuery = "/profile",
        password: PasswordNoticeQuery = "",
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
        db: DbSession,
        settings: SettingsDep,
        email: LoginEmailForm,
        password: PasswordForm,
        preauth_csrf_token: PreauthCsrfForm = "",
        next: NextForm = "/profile",
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
        db: DbSession,
        settings: SettingsDep,
        next: NextForm = "/profile",
        preauth_csrf_token: PreauthCsrfForm = "",
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

        def trust_item(title: str, detail: str) -> NodeLike:
            return html.div(
                html.span("✓", aria={"hidden": "true"}),
                html.p(html.strong(title), html.small(detail)),
            )

        intro = Stack(
            html.p("Controlled access", class_="eyebrow"),
            Heading("Your identity, managed with clarity.", level=1),
            Text(
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
            gap="0.75rem",
        )

        card_children: list[NodeLike] = [
            html.p("Account access", class_="eyebrow"),
            Heading("Sign in", level=2),
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
                HedronForm(
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
                HedronForm(
                    html.input(type="hidden", name="next", value=next),
                    html.input(type="hidden", name="preauth_csrf_token", value=preauth),
                    FormField(
                        name="email",
                        label="Government email",
                        id="email",
                        required=True,
                        control=TextInput(
                            "email",
                            id="email",
                            type="email",
                            value=email,
                            required=True,
                            autocomplete="username",
                        ),
                    ),
                    html.div(
                        html.label("Password", for_="password"),
                        html.a("Forgot password?", href=page_href("password/forgot")),
                        class_="label-row",
                    ),
                    TextInput(
                        "password",
                        id="password",
                        type="password",
                        required=True,
                        autocomplete="current-password",
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

        layout = Section(
            intro,
            Card(*card_children, class_="auth-card"),
            class_="auth-layout",
        )
        page = app_shell(
            layout, request=request, settings=settings, auth=None, page_title="Sign in"
        )
        response = render_page(page, request=request, status_code=status_code)
        set_preauth_csrf_cookie(response, request, preauth, settings)
        return response

    @app.post("/logout", include_in_schema=False)
    async def logout_submit(
        request: Request,
        auth: Auth,
        db: DbSession,
        settings: SettingsDep,
    ) -> Response:
        await require_csrf(request, auth.session.csrf_token)
        revoke_session(db, auth.session, actor=auth.user, request=request)
        response = RedirectResponse(app_path(request, "/login"), status_code=303)
        clear_auth_cookies(response, settings, request)
        return response
