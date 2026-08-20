"""Login, federated sign-in, and logout."""

from __future__ import annotations

from typing import cast

from fastapi import HTTPException, Request, status
from fastapi.responses import RedirectResponse
from hedron import Hedron
from hedron_posit import HedronPosit
from sqlalchemy import func, select
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
from app.dev_trace import dev_trace
from app.models import User
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

_BOOTSTRAP_HINT = (
    "No accounts exist yet. An operator must create the first administrator, for example: "
    "python -m app create-admin --email you@socom.mil"
)


def _bootstrap_hint(db: DbSession, settings: SettingsDep) -> str:
    if settings.authentication_mode != "local_password":
        return ""
    count = db.scalar(select(func.count()).select_from(User)) or 0
    return _BOOTSTRAP_HINT if count == 0 else ""


def register_login_routes(app: Hedron) -> None:
    @app.page("/login", include_in_schema=False)
    def login_page(
        request: Request,
        auth: OptionalAuth,
        db: DbSession,
        settings: SettingsDep,
        next: NextQuery = "/pipeline",
        password: PasswordNoticeQuery = "",
    ) -> Response:
        if auth:
            return RedirectResponse(
                cast(HedronPosit, request.app).href(safe_next(next), request=request),
                status_code=status.HTTP_303_SEE_OTHER,
            )
        return render_login_page(
            request,
            settings,
            next=safe_next(next),
            success="Password changed. Sign in with your new password."
            if password == "changed"
            else "",
            bootstrap_hint=_bootstrap_hint(db, settings),
        )

    @app.action("/login", include_in_schema=False)
    def login_submit(
        request: Request,
        db: DbSession,
        settings: SettingsDep,
        email: LoginEmailForm,
        password: PasswordForm,
        preauth_csrf_token: PreauthCsrfForm = "",
        next: NextForm = "/pipeline",
    ) -> Response:
        require_preauth_csrf(request, preauth_csrf_token, settings)
        if settings.authentication_mode != "local_password":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail="Password sign-in is disabled"
            )
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
            dev_trace("auth.password.rejected", reason="credentials_or_account")
            return render_login_page(
                request,
                settings,
                status_code=status.HTTP_400_BAD_REQUEST,
                error=str(exc),
                email=email,
                next=safe_next(next),
                bootstrap_hint=_bootstrap_hint(db, settings),
            )
        dev_trace("auth.password.accepted")
        tokens = create_session(db, settings, user, request)
        response = RedirectResponse(
            cast(HedronPosit, request.app).href(safe_next(next), request=request),
            status_code=status.HTTP_303_SEE_OTHER,
        )
        set_auth_cookies(response, tokens, settings, request)
        clear_preauth_csrf_cookie(response, request, settings)
        return response

    @app.action("/login/federated", include_in_schema=False)
    def federated_login_submit(
        request: Request,
        db: DbSession,
        settings: SettingsDep,
        next: NextForm = "/pipeline",
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
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc
        tokens = create_session(db, settings, user, request)
        response = RedirectResponse(
            cast(HedronPosit, request.app).href(safe_next(next), request=request),
            status_code=status.HTTP_303_SEE_OTHER,
        )
        set_auth_cookies(response, tokens, settings, request)
        clear_preauth_csrf_cookie(response, request, settings)
        return response

    @app.action("/logout", include_in_schema=False)
    async def logout_submit(
        request: Request,
        auth: Auth,
        db: DbSession,
        settings: SettingsDep,
        _csrf: RequireCsrf,
    ) -> Response:
        revoke_session(db, auth.session, actor=auth.user, request=request)
        response = RedirectResponse(
            cast(HedronPosit, request.app).href("/login", request=request),
            status_code=status.HTTP_303_SEE_OTHER,
        )
        clear_auth_cookies(response, settings, request)
        return response
