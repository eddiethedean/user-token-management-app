"""Self-registration and email verification."""

from __future__ import annotations

from fastapi import Request
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
from app.models import RegistrationVerification
from app.security.passwords import PasswordPolicyError
from app.services.auth import (
    TokenFlowError,
    complete_self_registration,
    get_valid_registration_verification,
    request_self_registration,
)
from app.services.directory import DirectoryUnavailableError, validate_directory_email
from app.services.rate_limit import check_rate_limit
from app.ui.http import auth_card, render_page
from app.ui.layout import alert_box, app_shell
from app.ui.params import (
    EmailForm,
    FlowTokenForm,
    FullNameForm,
    OptionalPasswordConfirmForm,
    OptionalPasswordForm,
)
from app.ui.urls import form_action, page_href


def register_registration_routes(app: Hedron) -> None:
    @app.get("/register", include_in_schema=False)
    def registration_page(request: Request, settings: SettingsDep):
        return _register_html(request, settings)

    @app.post("/register", include_in_schema=False)
    async def registration_submit(
        request: Request,
        db: DbSession,
        settings: SettingsDep,
        email: EmailForm,
        full_name: FullNameForm = "",
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
        body = [
            Heading("Request access", level=1),
            alert_box(error),
            alert_box(success, kind="success"),
        ]
        if not success:
            body.append(
                HedronForm(
                    FormField(
                        name="email",
                        label="Government email",
                        id="email",
                        required=True,
                        control=TextInput(
                            "email", id="email", type="email", value=email, required=True
                        ),
                    ),
                    FormField(
                        name="full_name",
                        label="Full name",
                        id="full_name",
                        control=TextInput("full_name", id="full_name", value=full_name),
                    ),
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
        db: DbSession,
        settings: SettingsDep,
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
        db: DbSession,
        settings: SettingsDep,
        token: FlowTokenForm,
        password: OptionalPasswordForm = "",
        password_confirm: OptionalPasswordConfirmForm = "",
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
            Heading("Verify registration", level=1),
            alert_box(error),
            alert_box(success, kind="success"),
        ]
        if not success and not error:
            fields: list = [html.input(type="hidden", name="token", value=token)]
            if settings.authentication_mode == "local_password":
                fields.extend(
                    [
                        FormField(
                            name="password",
                            label="Password",
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
                    ]
                )
            fields.append(
                html.button("Verify", class_="button button-primary button-wide", type="submit")
            )
            body.append(
                HedronForm(
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
