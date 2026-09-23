"""Invitation acceptance flow."""

from __future__ import annotations

import logging

from fastapi import Request, status
from fastapi.responses import RedirectResponse
from hedron import Hedron
from starlette.responses import Response

from app.application.feedback import account_failure
from app.dependencies import DbSession, SettingsDep
from app.logging_config import log_event
from app.security.passwords import PasswordPolicyError
from app.services.auth import TokenFlowError, accept_invitation, get_valid_invitation
from app.ui.params import (
    FlowTokenForm,
    FlowTokenQuery,
    FullNameForm,
    OptionalPasswordConfirmForm,
    OptionalPasswordForm,
)
from app.ui.partials.auth import render_invitation_page
from app.ui.urls import redirect_path

log = logging.getLogger(__name__)


def register_invitation_routes(app: Hedron) -> None:
    @app.page("/invitations/accept", include_in_schema=False)
    def invitation_page(
        request: Request,
        token: FlowTokenQuery,
        db: DbSession,
        settings: SettingsDep,
    ) -> Response:
        error = ""
        reference_id = ""
        invitation = None
        try:
            invitation = get_valid_invitation(db, settings, token)
        except TokenFlowError as exc:
            reference_id = getattr(request.state, "support_reference", "")
            outcome = account_failure(reason="link_invalid", reference_id=reference_id)
            error = outcome.message
            log_event(
                log,
                "auth.invitation.rejected",
                outcome="rejected",
                error_code="auth_link_invalid",
                reference_id=reference_id,
                operation="invitation_open",
                exception_type=type(exc).__name__,
            )
        return render_invitation_page(
            request,
            settings,
            token=token,
            invitation=invitation,
            error=error,
            error_reference=reference_id if error else "",
            status_code=status.HTTP_400_BAD_REQUEST if error else status.HTTP_200_OK,
        )

    @app.action("/invitations/accept", include_in_schema=False)
    def invitation_submit(
        request: Request,
        db: DbSession,
        settings: SettingsDep,
        token: FlowTokenForm,
        full_name: FullNameForm = "",
        password: OptionalPasswordForm = "",
        password_confirm: OptionalPasswordConfirmForm = "",
    ) -> Response:
        invitation = None
        error = ""
        try:
            invitation = get_valid_invitation(db, settings, token)
            if settings.authentication_mode == "local_password" and password != password_confirm:
                raise PasswordPolicyError("Passwords do not match.", reason="mismatch")
            accept_invitation(
                db,
                settings,
                raw_token=token,
                full_name=full_name,
                password=password,
                request=request,
            )
            return RedirectResponse(
                redirect_path(request, "/login"),
                status_code=status.HTTP_303_SEE_OTHER,
            )
        except (TokenFlowError, PasswordPolicyError, ValueError) as exc:
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
                "auth.invitation.rejected",
                outcome="rejected",
                error_code="auth_link_invalid"
                if isinstance(exc, TokenFlowError)
                else "auth_invalid",
                reference_id=reference_id,
                operation="invitation_accept",
                exception_type=type(exc).__name__,
            )
        return render_invitation_page(
            request,
            settings,
            token=token,
            invitation=invitation,
            full_name=full_name,
            error=error,
            error_reference=reference_id if error else "",
            status_code=status.HTTP_400_BAD_REQUEST,
        )
