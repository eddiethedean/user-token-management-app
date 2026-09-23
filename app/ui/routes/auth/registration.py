"""Self-registration and email verification."""

from __future__ import annotations

import logging

from fastapi import BackgroundTasks, Request, status
from hedron import Hedron
from starlette.responses import Response

from app.application.feedback import account_failure
from app.dependencies import DbSession, SettingsDep
from app.logging_config import log_event
from app.security.csrf import require_preauth_csrf
from app.security.email import EmailPolicyError
from app.security.passwords import PasswordPolicyError
from app.services.auth import (
    TokenFlowError,
    complete_self_registration,
    get_valid_registration_verification,
    request_self_registration,
)
from app.services.directory import (
    DirectoryEligibilityError,
    DirectoryUnavailableError,
    validate_directory_email,
)
from app.services.mailer import schedule_email_delivery
from app.services.rate_limit import check_rate_limit
from app.ui.params import (
    EmailForm,
    FlowTokenForm,
    FlowTokenQuery,
    FullNameForm,
    OptionalPasswordConfirmForm,
    OptionalPasswordForm,
    PreauthCsrfForm,
)
from app.ui.partials.auth import render_register_page, render_verify_page

log = logging.getLogger(__name__)


def register_registration_routes(app: Hedron) -> None:
    @app.page("/register", include_in_schema=False)
    def registration_page(request: Request, settings: SettingsDep):
        return render_register_page(request, settings)

    @app.action("/register", include_in_schema=False)
    async def registration_submit(
        request: Request,
        db: DbSession,
        settings: SettingsDep,
        background_tasks: BackgroundTasks,
        email: EmailForm,
        full_name: FullNameForm = "",
        preauth_csrf_token: PreauthCsrfForm = "",
    ) -> Response:
        require_preauth_csrf(request, preauth_csrf_token, settings)
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
            directory_record = await validate_directory_email(email, settings)
            request_self_registration(
                db,
                settings,
                email=email,
                full_name=full_name or (directory_record.display_name if directory_record else ""),
                request=request,
            )
            schedule_email_delivery(background_tasks, settings)
        except (ValueError, DirectoryUnavailableError) as exc:
            reference_id = getattr(request.state, "support_reference", "")
            outcome = account_failure(
                reason="service_unavailable"
                if isinstance(exc, DirectoryUnavailableError)
                else "domain_ineligible"
                if isinstance(exc, DirectoryEligibilityError)
                or (isinstance(exc, EmailPolicyError) and "domain" in str(exc).casefold())
                else "invalid",
                reference_id=reference_id,
            )
            log_event(
                log,
                "auth.registration.rejected",
                outcome="failed",
                error_code=(
                    "auth_account_unavailable"
                    if isinstance(exc, DirectoryUnavailableError)
                    else "auth_invalid"
                ),
                reference_id=reference_id,
                operation="registration_request",
                exception_type=type(exc).__name__,
            )
            return render_register_page(
                request,
                settings,
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE
                if isinstance(exc, DirectoryUnavailableError)
                else status.HTTP_400_BAD_REQUEST,
                error=outcome.message,
                error_reference=reference_id,
                email=email,
                full_name=full_name,
            )
        return render_register_page(
            request,
            settings,
            status_code=status.HTTP_202_ACCEPTED,
            success=(
                "Request received. If the address is eligible, check your government email for "
                "a verification link. After verification, an administrator must approve the "
                "request before you can sign in."
            ),
        )

    @app.page("/registration/verify", include_in_schema=False)
    def registration_verification_page(
        request: Request,
        token: FlowTokenQuery,
        db: DbSession,
        settings: SettingsDep,
    ) -> Response:
        error = ""
        reference_id = ""
        verification = None
        try:
            verification = get_valid_registration_verification(db, settings, token)
        except TokenFlowError as exc:
            reference_id = getattr(request.state, "support_reference", "")
            outcome = account_failure(reason="link_invalid", reference_id=reference_id)
            error = outcome.message
            log_event(
                log,
                "auth.registration_verification.rejected",
                outcome="rejected",
                error_code="auth_link_invalid",
                reference_id=reference_id,
                operation="registration_verification",
                exception_type=type(exc).__name__,
            )
        return render_verify_page(
            request,
            settings,
            token=token,
            verification=verification,
            error=error,
            error_reference=reference_id if error else "",
            status_code=status.HTTP_400_BAD_REQUEST if error else status.HTTP_200_OK,
        )

    @app.action("/registration/verify", include_in_schema=False)
    def registration_verification_submit(
        request: Request,
        db: DbSession,
        settings: SettingsDep,
        background_tasks: BackgroundTasks,
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
                raise PasswordPolicyError("Passwords do not match.", reason="mismatch")
            complete_self_registration(
                db, settings, raw_token=token, password=password, request=request
            )
            schedule_email_delivery(background_tasks, settings)
            return render_verify_page(
                request,
                settings,
                success=(
                    "Your government email is verified. Your request is now awaiting administrator "
                    "approval, and you cannot sign in until it is approved."
                ),
            )
        except (TokenFlowError, PasswordPolicyError) as exc:
            reference_id = getattr(request.state, "support_reference", "")
            outcome = account_failure(
                reason=(
                    "link_invalid"
                    if isinstance(exc, TokenFlowError)
                    else "password_mismatch"
                    if getattr(exc, "reason", "policy") == "mismatch"
                    else "password_policy"
                    if isinstance(exc, PasswordPolicyError)
                    else "invalid"
                ),
                reference_id=reference_id,
            )
            error = outcome.message
            log_event(
                log,
                "auth.registration_verification.rejected",
                outcome="rejected",
                error_code="auth_link_invalid"
                if isinstance(exc, TokenFlowError)
                else "auth_invalid",
                reference_id=reference_id,
                operation="registration_verification",
                exception_type=type(exc).__name__,
            )
        return render_verify_page(
            request,
            settings,
            token=token,
            verification=verification,
            error=error,
            error_reference=reference_id if error else "",
            status_code=status.HTTP_400_BAD_REQUEST,
        )
