"""Unauthenticated auth page builders (login, register, password, invitations)."""

from __future__ import annotations

from fastapi import Request
from hedron import (
    Card,
    FormField,
    Heading,
    Section,
    Stack,
    Text,
    TextInput,
    html,
)
from hedron import (
    Form as HedronForm,
)
from hedron_core import NodeLike
from starlette.responses import Response

from app.config import Settings
from app.models import Invitation, RegistrationVerification
from app.security.csrf import issue_preauth_csrf, set_preauth_csrf_cookie
from app.ui.forms import hidden_field, submit_button
from app.ui.http import auth_card, render_page
from app.ui.layout import alert_box, app_shell
from app.ui.urls import form_action, page_href


def render_login_page(
    request: Request,
    settings: Settings,
    *,
    status_code: int = 200,
    error: str = "",
    email: str = "",
    next: str = "/profile",
    success: str = "",
    bootstrap_hint: str = "",
) -> Response:
    preauth = issue_preauth_csrf(settings)
    federated = settings.authentication_mode == "trusted_header"

    def trust_item(title: str, detail: str) -> NodeLike:
        return html.div(
            html.span("✓", aria={"hidden": "true"}),
            html.p(html.strong(title), html.small(detail)),
        )

    intro = Stack(
        html.p("Controlled access", class_="eyebrow"),
        Heading("Your identity, managed with clarity.", level=1),
        Text(
            "Access your profile, review active sessions, and manage the security "
            "of your government application account."
        ),
        html.div(
            trust_item(
                "Administrator-approved access",
                "Invited and self-registered accounts require authorization.",
            ),
            trust_item("Short-lived credentials", "Access tokens expire automatically."),
            trust_item("Audited activity", "Security-sensitive actions are recorded."),
            class_="trust-list",
            aria={"label": "Security features"},
        ),
        class_="auth-intro",
        gap="0.75rem",
    )

    card_children: list[NodeLike] = [
        html.p("Account access", class_="eyebrow"),
        Heading("Sign in", level=2),
        html.p(
            "Continue through the approved identity-aware proxy using your CAC or "
            "federated credential."
            if federated
            else "Use the government email associated with your approved account.",
            class_="muted",
        ),
    ]
    if success:
        card_children.append(alert_box(success, kind="success"))
    if bootstrap_hint and not error:
        card_children.append(alert_box(bootstrap_hint, kind="info"))
    if error:
        card_children.append(alert_box(error))
    if federated:
        card_children.append(
            HedronForm(
                hidden_field("next", next),
                hidden_field("preauth_csrf_token", preauth),
                submit_button("Continue with federated sign-in", wide=True),
                action=form_action(request, "login/federated"),
                method="post",
                class_="stack-form",
            )
        )
        card_children.append(
            html.p(
                "Your identity must already be provisioned and active in this application.",
                class_="card-footnote",
            )
        )
    else:
        card_children.append(
            HedronForm(
                hidden_field("next", next),
                hidden_field("preauth_csrf_token", preauth),
                FormField(
                    name="email",
                    label="Government email",
                    id="email",
                    required=True,
                    control=TextInput(
                        "email",
                        id="email",
                        type="email",
                        value=email,
                        required=True,
                        autocomplete="username",
                    ),
                ),
                html.div(
                    html.label("Password", for_="password"),
                    html.a("Forgot password?", href=page_href(request, "password/forgot")),
                    class_="label-row",
                ),
                TextInput(
                    "password",
                    id="password",
                    type="password",
                    required=True,
                    autocomplete="current-password",
                ),
                html.button(
                    "Show password",
                    class_="password-toggle",
                    type="button",
                    data={"password-toggle": "password"},
                    aria={"pressed": "false"},
                ),
                submit_button("Sign in securely", wide=True),
                action=form_action(request, "login"),
                method="post",
                class_="stack-form",
            )
        )
        card_children.append(
            html.p(
                "Need an account? ",
                html.a("Request access", href=page_href(request, "register")),
                ". You must verify your government email and receive administrator "
                "approval before signing in.",
                class_="card-footnote",
            )
        )

    layout = Section(
        intro,
        Card(*card_children, class_="auth-card"),
        class_="auth-layout",
    )
    page = app_shell(layout, request=request, settings=settings, auth=None, page_title="Sign in")
    response = render_page(page, request=request, status_code=status_code)
    set_preauth_csrf_cookie(response, request, preauth, settings)
    return response


def render_register_page(
    request: Request,
    settings: Settings,
    *,
    status_code: int = 200,
    error: str = "",
    success: str = "",
    email: str = "",
    full_name: str = "",
) -> Response:
    preauth = issue_preauth_csrf(settings)
    body: list[NodeLike] = [
        Heading("Request access", level=1),
        alert_box(error),
        alert_box(success, kind="success"),
    ]
    if not success:
        body.append(
            HedronForm(
                hidden_field("preauth_csrf_token", preauth),
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
                submit_button("Submit request", wide=True),
                action=form_action(request, "register"),
                method="post",
                class_="stack-form",
            )
        )
    body.append(html.p(html.a("Back to sign in", href=page_href(request, "login"))))
    response = render_page(
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
    if not success:
        set_preauth_csrf_cookie(response, request, preauth, settings)
    return response


def render_verify_page(
    request: Request,
    settings: Settings,
    *,
    token: str = "",
    verification: RegistrationVerification | None = None,
    error: str = "",
    success: str = "",
    status_code: int = 200,
) -> Response:
    _ = verification
    body: list[NodeLike] = [
        Heading("Verify registration", level=1),
        alert_box(error),
        alert_box(success, kind="success"),
    ]
    if not success and not error:
        fields: list[NodeLike] = [hidden_field("token", token)]
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
        fields.append(submit_button("Verify", wide=True))
        body.append(
            HedronForm(
                *fields,
                action=form_action(request, "registration/verify"),
                method="post",
                class_="stack-form",
            )
        )
    elif error:
        body.append(html.p(html.a("Request access again", href=page_href(request, "register"))))
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


def render_forgot_page(request: Request, settings: Settings, *, success: str = "") -> Response:
    preauth = issue_preauth_csrf(settings)
    body: list[NodeLike] = [
        Heading("Forgot password", level=1),
        alert_box(success, kind="success"),
    ]
    if not success:
        body.append(
            HedronForm(
                hidden_field("preauth_csrf_token", preauth),
                FormField(
                    name="email",
                    label="Government email",
                    id="email",
                    required=True,
                    control=TextInput("email", id="email", type="email", required=True),
                ),
                submit_button("Send reset link", wide=True),
                action=form_action(request, "password/forgot"),
                method="post",
                class_="stack-form",
            )
        )
    body.append(html.p(html.a("Back to sign in", href=page_href(request, "login"))))
    response = render_page(
        app_shell(
            auth_card(*body),
            request=request,
            settings=settings,
            auth=None,
            page_title="Forgot password",
        ),
        request=request,
    )
    if not success:
        set_preauth_csrf_cookie(response, request, preauth, settings)
    return response


def render_reset_page(
    request: Request,
    settings: Settings,
    *,
    token: str = "",
    error: str = "",
    status_code: int = 200,
) -> Response:
    body: list[NodeLike] = [Heading("Reset password", level=1), alert_box(error)]
    if not error or token:
        body.append(
            HedronForm(
                hidden_field("token", token),
                FormField(
                    name="password",
                    label="New password",
                    id="password",
                    required=True,
                    control=TextInput("password", id="password", type="password", required=True),
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
                submit_button("Update password", wide=True),
                action=form_action(request, "password/reset"),
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


def render_invitation_page(
    request: Request,
    settings: Settings,
    *,
    token: str = "",
    invitation: Invitation | None = None,
    full_name: str = "",
    error: str = "",
    status_code: int = 200,
) -> Response:
    body: list[NodeLike] = [Heading("Accept invitation", level=1), alert_box(error)]
    if invitation and not error:
        fields: list[NodeLike] = [
            hidden_field("token", token),
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
        fields.append(submit_button("Accept invitation", wide=True))
        body.append(
            HedronForm(
                *fields,
                action=form_action(request, "invitations/accept"),
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
