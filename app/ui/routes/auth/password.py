"""Password forgot and reset flows."""

from __future__ import annotations

from fastapi import HTTPException, Request
from fastapi.responses import RedirectResponse
from hedron import (
    Form as HedronForm,
)
from hedron import (
    FormField,
    Heading,
    Hedron,
    TextInput,
    html,
)
from starlette.responses import Response

from app.config import Settings
from app.dependencies import DbSession, SettingsDep
from app.routing import app_path
from app.security.passwords import PasswordPolicyError
from app.services.auth import (
    TokenFlowError,
    complete_password_reset,
    get_valid_password_reset,
    request_password_reset,
)
from app.services.rate_limit import check_rate_limit
from app.ui.http import auth_card, render_page
from app.ui.layout import alert_box, app_shell
from app.ui.params import EmailForm, FlowTokenForm, PasswordConfirmForm, PasswordForm
from app.ui.urls import form_action, page_href


def register_password_routes(app: Hedron) -> None:
    @app.get("/password/forgot", include_in_schema=False)
    def forgot_page(request: Request, settings: SettingsDep):
        if settings.authentication_mode != "local_password":
            raise HTTPException(status_code=404, detail="Not found")
        return _forgot_html(request, settings)

    @app.post("/password/forgot", include_in_schema=False)
    def forgot_submit(
        request: Request,
        db: DbSession,
        settings: SettingsDep,
        email: EmailForm,
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
        body = [Heading("Forgot password", level=1), alert_box(success, kind="success")]
        if not success:
            body.append(
                HedronForm(
                    FormField(
                        name="email",
                        label="Government email",
                        id="email",
                        required=True,
                        control=TextInput("email", id="email", type="email", required=True),
                    ),
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
        db: DbSession,
        settings: SettingsDep,
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
        db: DbSession,
        settings: SettingsDep,
        token: FlowTokenForm,
        password: PasswordForm,
        password_confirm: PasswordConfirmForm,
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
        body = [Heading("Reset password", level=1), alert_box(error)]
        if not error or token:
            body.append(
                HedronForm(
                    html.input(type="hidden", name="token", value=token),
                    FormField(
                        name="password",
                        label="New password",
                        id="password",
                        required=True,
                        control=TextInput(
                            "password", id="password", type="password", required=True
                        ),
                    ),
                    FormField(
                        name="password_confirm",
                        label="Confirm password",
                        id="password_confirm",
                        required=True,
                        control=TextInput(
                            "password_confirm",
                            id="password_confirm",
                            type="password",
                            required=True,
                        ),
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
