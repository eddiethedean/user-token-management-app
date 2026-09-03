"""Password forgot and reset flows."""

from __future__ import annotations

from fastapi import BackgroundTasks, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from hedron import Hedron
from starlette.responses import Response

from app.dependencies import DbSession, SettingsDep
from app.security.csrf import require_preauth_csrf
from app.security.passwords import PasswordPolicyError
from app.services.auth import (
    TokenFlowError,
    complete_password_reset,
    get_valid_password_reset,
    request_password_reset,
)
from app.services.mailer import schedule_email_delivery
from app.services.rate_limit import check_rate_limit
from app.ui.params import (
    EmailForm,
    FlowTokenForm,
    FlowTokenQuery,
    PasswordConfirmForm,
    PasswordForm,
    PreauthCsrfForm,
)
from app.ui.partials.auth import render_forgot_page, render_reset_page
from app.ui.urls import redirect_path


def register_password_routes(app: Hedron) -> None:
    @app.page("/password/forgot", include_in_schema=False)
    def forgot_page(request: Request, settings: SettingsDep):
        if settings.authentication_mode != "local_password":
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
        return render_forgot_page(request, settings)

    @app.action("/password/forgot", include_in_schema=False)
    def forgot_submit(
        request: Request,
        db: DbSession,
        settings: SettingsDep,
        background_tasks: BackgroundTasks,
        email: EmailForm,
        preauth_csrf_token: PreauthCsrfForm = "",
    ) -> Response:
        if settings.authentication_mode != "local_password":
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
        require_preauth_csrf(request, preauth_csrf_token, settings)
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
        schedule_email_delivery(background_tasks, settings)
        return render_forgot_page(
            request,
            settings,
            success="If the account exists and can sign in with a password, a reset link was sent.",
        )

    @app.page("/password/reset", include_in_schema=False)
    def reset_page(
        request: Request,
        token: FlowTokenQuery,
        db: DbSession,
        settings: SettingsDep,
    ) -> Response:
        if settings.authentication_mode != "local_password":
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
        error = ""
        try:
            get_valid_password_reset(db, settings, token)
        except TokenFlowError as exc:
            error = str(exc)
        return render_reset_page(
            request,
            settings,
            token=token,
            error=error,
            status_code=status.HTTP_400_BAD_REQUEST if error else status.HTTP_200_OK,
        )

    @app.action("/password/reset", include_in_schema=False)
    def reset_submit(
        request: Request,
        db: DbSession,
        settings: SettingsDep,
        background_tasks: BackgroundTasks,
        token: FlowTokenForm,
        password: PasswordForm,
        password_confirm: PasswordConfirmForm,
    ) -> Response:
        if settings.authentication_mode != "local_password":
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
        try:
            if password != password_confirm:
                raise PasswordPolicyError("Passwords do not match.")
            complete_password_reset(
                db, settings, raw_token=token, password=password, request=request
            )
            schedule_email_delivery(background_tasks, settings)
            return RedirectResponse(
                redirect_path(request, "/login?password=changed"),
                status_code=status.HTTP_303_SEE_OTHER,
            )
        except PasswordPolicyError as exc:
            return render_reset_page(
                request,
                settings,
                token=token,
                error=str(exc),
                can_retry=True,
                status_code=status.HTTP_400_BAD_REQUEST,
            )
        except TokenFlowError as exc:
            return render_reset_page(
                request,
                settings,
                token=token,
                error=str(exc),
                status_code=status.HTTP_400_BAD_REQUEST,
            )
