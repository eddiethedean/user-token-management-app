"""Unauthenticated auth page builders (login, register, password, invitations)."""

from __future__ import annotations

from fastapi import Request
from hedron import (
    ActionGroup,
    Card,
    FormField,
    Heading,
    LinkButton,
    SplitView,
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
    next: str = "/pipeline",
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
        html.p("Secure data operations", class_="eyebrow"),
        Heading("Move data. Keep control.", level=1),
        Text(
            "Build dependable routes between your remote systems while your credentials "
            "stay encrypted and under your control."
        ),
        html.div(
            trust_item(
                "Bring your own connections",
                "Use the service credentials already approved for your account.",
            ),
            trust_item(
                "Secrets stay sealed", "Saved credentials are encrypted and never displayed."
            ),
            trust_item(
                "Observable by default", "Every transfer has progress, metrics, and a run log."
            ),
            class_="trust-list hedron-stack",
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
            else "Use the email associated with your approved Data Mover workspace.",
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
                submit_button("Sign in securely", wide=True),
                action=form_action(request, "login"),
                method="post",
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

    layout = SplitView(
        primary=intro,
        secondary=Card(*card_children, class_="auth-card"),
        ratio="3:2",
        gap="5.625rem",
        collapse="md",
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
        html.p("Identity request", class_="eyebrow"),
        Heading("Request access", level=1),
        html.p(
            "Use your government email. After you verify the address, an administrator "
            "will review your request.",
            class_="muted",
        ),
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
            )
        )
    body.append(
        html.p(
            html.a("Back to sign in", href=page_href(request, "login")),
            class_="card-footnote",
        )
    )
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
        html.p("Email verification", class_="eyebrow"),
        Heading("Verify registration", level=1),
        html.p(
            (
                "Confirm your address and choose the credentials for your application account."
                if verification
                else "This link cannot be used to verify a registration."
            ),
            class_="muted",
        ),
        alert_box(error),
        alert_box(success, kind="success"),
    ]
    if not success and verification:
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
            )
        )
    elif error:
        body.append(
            ActionGroup(
                LinkButton("Request access again", href=page_href(request, "register")),
                LinkButton("Back to sign in", href=page_href(request, "login")),
                class_="auth-actions",
            )
        )
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
        html.p("Account recovery", class_="eyebrow"),
        Heading("Forgot password", level=1),
        html.p(
            "Enter your government email. If an eligible account exists, we will send "
            "a time-limited reset link.",
            class_="muted",
        ),
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
            )
        )
    body.append(
        html.p(
            html.a("Back to sign in", href=page_href(request, "login")),
            class_="card-footnote",
        )
    )
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
    can_retry: bool = False,
    status_code: int = 200,
) -> Response:
    body: list[NodeLike] = [
        html.p("Account recovery", class_="eyebrow"),
        Heading("Reset password", level=1),
        html.p(
            (
                "Choose a new password for your account."
                if not error or can_retry
                else "This reset link can no longer be used. Request a new one to continue."
            ),
            class_="muted",
        ),
        alert_box(error),
    ]
    if not error or can_retry:
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
            )
        )
    else:
        body.append(
            ActionGroup(
                LinkButton("Request a new reset link", href=page_href(request, "password/forgot")),
                LinkButton("Back to sign in", href=page_href(request, "login")),
                class_="auth-actions",
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
    body: list[NodeLike] = [
        html.p("Account invitation", class_="eyebrow"),
        Heading("Accept invitation", level=1),
        html.p(
            (
                "Complete your profile to activate this approved invitation."
                if invitation
                else "This invitation link is no longer available. Ask an administrator to "
                "send a new invitation."
            ),
            class_="muted",
        ),
        alert_box(error),
    ]
    if invitation:
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
            )
        )
        body.append(
            html.p(
                "Already have access? ",
                html.a("Sign in", href=page_href(request, "login")),
                class_="card-footnote",
            )
        )
    elif error:
        body.append(
            ActionGroup(
                LinkButton("Back to sign in", href=page_href(request, "login")),
                class_="auth-actions",
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
