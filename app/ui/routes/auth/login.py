"""Login, federated sign-in, and logout."""

from __future__ import annotations

from fastapi import HTTPException, Request
from fastapi.responses import RedirectResponse
from hedron import Hedron
from starlette.responses import Response

from app.dependencies import (
    Auth,
    DbSession,
    OptionalAuth,
    RequireCsrf,
    SettingsDep,
    clear_auth_cookies,
    set_auth_cookies,
)
from app.routing import app_path
from app.security.csrf import (
    clear_preauth_csrf_cookie,
    require_preauth_csrf,
)
from app.services.auth import (
    AuthenticationError,
    authenticate_trusted_identity,
    authenticate_user,
    create_session,
    revoke_session,
)
from app.services.rate_limit import check_rate_limit
from app.ui.http import safe_next
from app.ui.params import (
    LoginEmailForm,
    NextForm,
    NextQuery,
    PasswordForm,
    PasswordNoticeQuery,
    PreauthCsrfForm,
)
from app.ui.partials.auth import render_login_page


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
        return render_login_page(
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
            return render_login_page(
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

    @app.post("/logout", include_in_schema=False)
    async def logout_submit(
        request: Request,
        auth: Auth,
        db: DbSession,
        settings: SettingsDep,
        _csrf: RequireCsrf,
    ) -> Response:
        revoke_session(db, auth.session, actor=auth.user, request=request)
        response = RedirectResponse(app_path(request, "/login"), status_code=303)
        clear_auth_cookies(response, settings, request)
        return response
