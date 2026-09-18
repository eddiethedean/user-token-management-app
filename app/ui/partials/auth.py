"""Unauthenticated auth page builders (login, register, password, invitations)."""

from __future__ import annotations

from fastapi import Request
from hedron import (
    ActionGroup,
    Alert,
    Badge,
    Container,
    Divider,
    FormField,
    Grid,
    Icon,
    Inline,
    Link,
    LinkButton,
    SplitView,
    Stack,
    Surface,
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
from app.ui.design_system import (
    DataMoverPageHeader as PageHeader,
)
from app.ui.design_system import surface_card
from app.ui.forms import compact_password_input, hidden_field, submit_button
from app.ui.http import auth_card, render_page
from app.ui.icons import NAV_ICONS
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
        title_measure="narrow",
        description_measure="default",
        title_effect="display",
        description_effect="subtle",
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
        Badge("SECURE TRANSFER WORKSPACE", tone="info", size="sm", appearance="soft"),
        html.h2(
            "Move data.",
            html.br(),
            html.span("Keep control.", class_="data-mover-login-accent"),
            class_="data-mover-login-headline",
        ),
        Text(
            "Connect your systems. Build your route. Move forward with a clear view of every transfer.",
            role="body",
            effect="subtle",
            measure="default",
        ),
        html.figure(
            Grid(
                *[
                    Stack(
                        Surface(
                            Icon(NAV_ICONS[icon], size="lg", decorative=True),
                            appearance="raised",
                            density="comfortable",
                            padding="md",
                            elevation="none",
                            class_="data-mover-login-node-icon",
                        ),
                        Text(label, role="label", effect="none"),
                        Text(detail, role="caption", effect="none"),
                        gap="xs",
                        class_=f"data-mover-login-node data-mover-login-node-{kind}",
                    )
                    for icon, label, detail, kind in (
                        ("connections", "Connect", "Approved systems", "source"),
                        ("pipeline", "Transfer", "Your configured route", "transfer"),
                        ("activity", "Verify", "A clear audit trail", "destination"),
                    )
                ],
                columns=3,
                gap="sm",
                class_="data-mover-login-flow",
            ),
            html.figcaption("From source to destination. One controlled workflow."),
            class_="data-mover-login-illustration",
        ),
        Stack(
            Divider(),
            Inline(
                Icon(NAV_ICONS["account"], size="sm", decorative=True),
                Text(
                    "Encrypted credentials. Approved access. Traceable transfers.",
                    role="caption",
                    effect="subtle",
                ),
                gap="sm",
            ),
            gap="md",
        ),
        gap="lg",
        class_="data-mover-login-intro",
    )

    card_children: list[NodeLike] = [
        Surface(
            Icon(NAV_ICONS["account"], size="lg", decorative=True),
            appearance="raised",
            density="compact",
            padding="sm",
            elevation="none",
            class_="data-mover-login-access-icon",
        ),
        PageHeader(
            "Welcome back",
            description=(
                "Continue through the approved identity-aware proxy using your CAC or "
                "federated credential."
                if federated
                else "Sign in to your approved workspace."
            ),
            level=1,
            density="compact",
            title_measure="wide",
            description_measure="default",
            title_effect="none",
            description_effect="none",
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
                    Link(
                        "Forgot password?",
                        href=page_href(request, "password/forgot"),
                        class_="data-mover-login-forgot-link",
                    ),
                    gap="xs",
                    class_="data-mover-login-password-field",
                ),
                submit_button("Continue to workspace", width="full", size="lg"),
                action=form_action(request, "login"),
                method="post",
            )
        )
        card_children.append(
            Stack(
                Divider(),
                ActionGroup(
                    Text("Need an account?", as_="span", role="caption"),
                    LinkButton(
                        "Request access",
                        href=page_href(request, "register"),
                        appearance="ghost",
                        size="sm",
                    ),
                    align="center",
                    gap="xs",
                ),
                Text(
                    "A verified email and administrator approval are required.",
                    role="caption",
                    overflow="wrap",
                ),
                gap="xs",
            )
        )

    login_card = surface_card(
        Stack(*card_children, gap="md"),
        recipe="data-mover-auth-panel",
        class_="data-mover-login-card",
    )
    layout = Container(
        SplitView(
            primary=intro,
            secondary=Stack(
                login_card,
                ActionGroup(
                    Text(
                        "Demo workspace · No external systems are contacted."
                        if settings.is_demo_mode
                        else "Live workspace · Transfers may change remote systems.",
                        role="caption",
                        effect="subtle",
                    ),
                    align="center",
                ),
                gap="md",
            ),
            ratio="1:1",
            gap="xl",
            collapse="never",
            class_="data-mover-login-split",
        ),
        max_width="lg",
        padding="lg",
    )
    page = app_shell(
        layout,
        request=request,
        settings=settings,
        auth=None,
        page_title="Sign in",
        default_color_mode="dark",
        auth_presentation="login",
    )
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
