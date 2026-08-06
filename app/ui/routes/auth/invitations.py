"""Invitation acceptance flow."""

from __future__ import annotations

from fastapi import Request, status
from fastapi.responses import RedirectResponse
from hedron import Hedron
from starlette.responses import Response

from app.dependencies import DbSession, SettingsDep
from app.routing import app_path
from app.security.passwords import PasswordPolicyError
from app.services.auth import TokenFlowError, accept_invitation, get_valid_invitation
from app.ui.params import (
    FlowTokenForm,
    FullNameForm,
    OptionalPasswordConfirmForm,
    OptionalPasswordForm,
)
from app.ui.partials.auth import render_invitation_page


def register_invitation_routes(app: Hedron) -> None:
    @app.get("/invitations/accept", include_in_schema=False)
    def invitation_page(
        request: Request,
        token: str,
        db: DbSession,
        settings: SettingsDep,
    ) -> Response:
        error = ""
        invitation = None
        try:
            invitation = get_valid_invitation(db, settings, token)
        except TokenFlowError as exc:
            error = str(exc)
        return render_invitation_page(
            request,
            settings,
            token=token,
            invitation=invitation,
            error=error,
            status_code=status.HTTP_400_BAD_REQUEST if error else status.HTTP_200_OK,
        )

    @app.post("/invitations/accept", include_in_schema=False)
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
                raise PasswordPolicyError("Passwords do not match.")
            accept_invitation(
                db,
                settings,
                raw_token=token,
                full_name=full_name,
                password=password,
                request=request,
            )
            return RedirectResponse(
                app_path(request, "/login"), status_code=status.HTTP_303_SEE_OTHER
            )
        except (TokenFlowError, PasswordPolicyError, ValueError) as exc:
            error = str(exc)
        return render_invitation_page(
            request,
            settings,
            token=token,
            invitation=invitation,
            full_name=full_name,
            error=error,
            status_code=status.HTTP_400_BAD_REQUEST,
        )
