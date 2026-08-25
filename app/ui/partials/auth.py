"""Unauthenticated auth page builders (login, register, password, invitations)."""

from __future__ import annotations

from fastapi import Request
from hedron import (
    ActionGroup,
    Alert,
    Divider,
    FlowStep,
    FormField,
    Link,
    LinkButton,
    PageHeader,
    ProcessFlow,
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
from app.ui.design_system import surface_card
from app.ui.forms import compact_password_input, hidden_field, submit_button
from app.ui.http import auth_card, render_page
from app.ui.layout import alert_box, app_shell
from app.ui.urls import form_action, page_href


def _auth_heading(eyebrow: str, title: str, description: str) -> PageHeader:
    """Give every account-access screen the same compact visual hierarchy."""

    return PageHeader(
        title,
        eyebrow=eyebrow,
        description=description,
        level=1,
        density="compact",
    )


def _auth_password_field(
    name: str,
    label: str,
    *,
    autocomplete: str,
    help_text: str | None = None,
) -> FormField:
    """Password field with the same accessible reveal affordance used at sign-in."""

    return FormField(
        name=name,
        label=label,
        id=name,
        required=True,
        help=help_text,
        control=compact_password_input(
            name,
            id=name,
            autocomplete=autocomplete,
            required=True,
        ),
    )


def _auth_footer_link(request: Request, label: str, href: str) -> Stack:
    return Stack(
        Divider(),
        ActionGroup(
            LinkButton(label, href=page_href(request, href), appearance="ghost", size="sm"),
            align="center",
        ),
        gap="sm",
    )


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

    password_control = compact_password_input(
        "password",
        id="password",
        autocomplete="current-password",
        required=True,
    )

    intro = Stack(
        PageHeader(
            "Move data without moving secrets.",
            eyebrow="Secure transfer workspace",
            description=(
                "Build dependable routes between approved systems while credentials remain "
                "encrypted, access-controlled, and out of every run log."
            ),
            level=2,
            density="compact",
        ),
        ProcessFlow(
            FlowStep(
                "Connect",
                status="complete",
                status_text="Protected",
                description="Use approved sources and destinations.",
            ),
            FlowStep(
                "Shape",
                status="current",
                status_text="Preview",
                description="Inspect schemas and map every field.",
            ),
            FlowStep(
                "Move",
                status="pending",
                status_text="Observable",
                description="Run with progress, metrics, and audit history.",
            ),
            label="Data movement workflow",
            direction="horizontal",
            collapse="never",
            density="compact",
        ),
        Alert(
            "Explore the complete workflow without contacting external systems.",
            title="Safe demo environment",
            tone="warning",
            appearance="soft",
        ),
        gap="md",
    )

    card_children: list[NodeLike] = [
        PageHeader(
            "Welcome back",
            eyebrow="Account access",
            description=(
                "Continue through the approved identity-aware proxy using your CAC or "
                "federated credential."
                if federated
                else "Sign in with the email associated with your approved workspace."
            ),
            level=1,
            density="compact",
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
                submit_button("Continue with federated sign-in", width="full"),
                action=form_action(request, "login/federated"),
                method="post",
            )
        )
        card_children.append(
            Text(
                "Your identity must already be provisioned and active in this application.",
                role="caption",
                overflow="wrap",
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
                Stack(
                    html.label("Password", for_="password"),
                    password_control,
                    gap="xs",
                ),
                ActionGroup(
                    Link("Forgot password?", href=page_href(request, "password/forgot")),
                    align="end",
                    collapse="never",
                ),
                submit_button("Sign in securely", width="full"),
                action=form_action(request, "login"),
                method="post",
            )
        )
        card_children.append(
            Stack(
                Divider(),
                html.p(
                    "Need an account? ",
                    Link("Request access", href=page_href(request, "register")),
                ),
                Text("Access requires a verified address and administrator approval."),
                gap="xs",
            )
        )

    layout = surface_card(
        SplitView(
            primary=Stack(*card_children, gap="md"),
            secondary=intro,
            ratio="1:1",
            gap="xl",
            collapse="never",
        ),
        recipe="data-mover-auth-panel",
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
        _auth_heading(
            "Identity request",
            "Request access",
            "Use your government email. After you verify the address, an administrator "
            "will review your request.",
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
                submit_button("Submit request", width="full"),
                action=form_action(request, "register"),
                method="post",
            )
        )
    body.append(_auth_footer_link(request, "Back to sign in", "login"))
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
        _auth_heading(
            "Email verification",
            "Verify registration",
            (
                "Confirm your address and choose the credentials for your application account."
                if verification
                else "This link cannot be used to verify a registration."
            ),
        ),
        alert_box(error),
        alert_box(success, kind="success"),
    ]
    if not success and verification:
        fields: list[NodeLike] = [hidden_field("token", token)]
        if settings.authentication_mode == "local_password":
            fields.extend(
                [
                    _auth_password_field(
                        "password",
                        "Password",
                        autocomplete="new-password",
                        help_text="Use 15–128 characters and avoid common passwords.",
                    ),
                    _auth_password_field(
                        "password_confirm",
                        "Confirm password",
                        autocomplete="new-password",
                    ),
                ]
            )
        fields.append(submit_button("Verify", width="full"))
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
                align="center",
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
        _auth_heading(
            "Account recovery",
            "Recover your account",
            "Enter your government email. If an eligible account exists, we will send "
            "a time-limited reset link.",
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
                submit_button("Send reset link", width="full"),
                action=form_action(request, "password/forgot"),
                method="post",
            )
        )
    body.append(_auth_footer_link(request, "Back to sign in", "login"))
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
        _auth_heading(
            "Account recovery",
            "Create a new password",
            (
                "Choose a new password for your account."
                if not error or can_retry
                else "This reset link can no longer be used. Request a new one to continue."
            ),
        ),
        alert_box(error),
    ]
    if not error or can_retry:
        body.append(
            HedronForm(
                hidden_field("token", token),
                _auth_password_field(
                    "password",
                    "New password",
                    autocomplete="new-password",
                    help_text="Use 15–128 characters and avoid common passwords.",
                ),
                _auth_password_field(
                    "password_confirm",
                    "Confirm password",
                    autocomplete="new-password",
                ),
                submit_button("Update password", width="full"),
                action=form_action(request, "password/reset"),
                method="post",
            )
        )
    else:
        body.append(
            ActionGroup(
                LinkButton("Request a new reset link", href=page_href(request, "password/forgot")),
                LinkButton("Back to sign in", href=page_href(request, "login")),
                align="center",
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
        _auth_heading(
            "Account invitation",
            "Join the workspace",
            (
                "Complete your profile to activate this approved invitation."
                if invitation
                else "This invitation link is no longer available. Ask an administrator to "
                "send a new invitation."
            ),
        ),
        alert_box(error),
    ]
    if invitation:
        fields: list[NodeLike] = [
            hidden_field("token", token),
            Alert(
                f"{invitation.email_original} · {invitation.role_name.title()} access",
                title="Approved invitation",
                tone="success",
                appearance="soft",
            ),
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
                    _auth_password_field(
                        "password",
                        "Password",
                        autocomplete="new-password",
                        help_text="Use 15–128 characters and avoid common passwords.",
                    ),
                    _auth_password_field(
                        "password_confirm",
                        "Confirm password",
                        autocomplete="new-password",
                    ),
                ]
            )
        fields.append(submit_button("Accept invitation", width="full"))
        body.append(
            HedronForm(
                *fields,
                action=form_action(request, "invitations/accept"),
                method="post",
            )
        )
        body.append(
            ActionGroup(
                Text("Already have access?", as_="span", role="caption"),
                Link("Sign in", href=page_href(request, "login")),
                align="center",
                gap="xs",
                collapse="never",
            )
        )
    elif error:
        body.append(
            ActionGroup(
                LinkButton("Back to sign in", href=page_href(request, "login")),
                align="center",
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
