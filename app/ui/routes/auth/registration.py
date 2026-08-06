"""Self-registration and email verification."""

from __future__ import annotations

from fastapi import Request
from hedron import Hedron
from starlette.responses import Response

from app.dependencies import DbSession, SettingsDep
from app.security.passwords import PasswordPolicyError
from app.services.auth import (
    TokenFlowError,
    complete_self_registration,
    get_valid_registration_verification,
    request_self_registration,
)
from app.services.directory import DirectoryUnavailableError, validate_directory_email
from app.services.rate_limit import check_rate_limit
from app.ui.params import (
    EmailForm,
    FlowTokenForm,
    FullNameForm,
    OptionalPasswordConfirmForm,
    OptionalPasswordForm,
)
from app.ui.partials.auth import render_register_page, render_verify_page


def register_registration_routes(app: Hedron) -> None:
    @app.get("/register", include_in_schema=False)
    def registration_page(request: Request, settings: SettingsDep):
        return render_register_page(request, settings)

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
            return render_register_page(
                request,
                settings,
                status_code=503 if isinstance(exc, DirectoryUnavailableError) else 400,
                error=str(exc),
                email=email,
                full_name=full_name,
            )
        return render_register_page(
            request,
            settings,
            status_code=202,
            success=(
                "Request received. If the address is eligible, check your government email for "
                "a verification link. After verification, an administrator must approve the "
                "request before you can sign in."
            ),
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
        return render_verify_page(
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
            return render_verify_page(
                request,
                settings,
                success=(
                    "Your government email is verified. Your request is now awaiting administrator "
                    "approval, and you cannot sign in until it is approved."
                ),
            )
        except (TokenFlowError, PasswordPolicyError) as exc:
            error = str(exc)
        return render_verify_page(
            request, settings, token=token, verification=verification, error=error, status_code=400
        )
