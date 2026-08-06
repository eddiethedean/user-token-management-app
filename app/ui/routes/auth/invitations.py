"""Invitation acceptance flow."""

from __future__ import annotations

from fastapi import Request
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
from app.models import Invitation
from app.routing import app_path
from app.security.passwords import PasswordPolicyError
from app.services.auth import TokenFlowError, accept_invitation, get_valid_invitation
from app.ui.http import auth_card, render_page
from app.ui.layout import alert_box, app_shell
from app.ui.params import (
    FlowTokenForm,
    FullNameForm,
    OptionalPasswordConfirmForm,
    OptionalPasswordForm,
)
from app.ui.urls import form_action


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
        return _invite_html(
            request,
            settings,
            token=token,
            invitation=invitation,
            error=error,
            status_code=400 if error else 200,
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
            return RedirectResponse(app_path(request, "/login"), status_code=303)
        except (TokenFlowError, PasswordPolicyError, ValueError) as exc:
            error = str(exc)
        return _invite_html(
            request,
            settings,
            token=token,
            invitation=invitation,
            full_name=full_name,
            error=error,
            status_code=400,
        )

    def _invite_html(
        request: Request,
        settings: Settings,
        *,
        token: str = "",
        invitation: Invitation | None = None,
        full_name: str = "",
        error: str = "",
        status_code: int = 200,
    ) -> Response:
        body = [Heading("Accept invitation", level=1), alert_box(error)]
        if invitation and not error:
            fields = [
                html.input(type="hidden", name="token", value=token),
                html.p(f"Invited as {invitation.email_original} ({invitation.role_name})"),
                FormField(
                    name="full_name",
                    label="Full name",
                    id="full_name",
                    control=TextInput("full_name", id="full_name", value=full_name),
                ),
            ]
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
                html.button(
                    "Accept invitation", class_="button button-primary button-wide", type="submit"
                )
            )
            body.append(
                HedronForm(
                    *fields,
                    action=form_action("invitations/accept"),
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
                page_title="Accept invitation",
            ),
            status_code=status_code,
            request=request,
        )
