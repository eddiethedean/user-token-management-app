"""Profile page fragments."""

from __future__ import annotations

from typing import Any

from fastapi import Request
from hedron import (
    Avatar,
    Badge,
    DescriptionList,
    Form,
    FormField,
    FormGrid,
    Heading,
    Inline,
    LinkButton,
    OobUpdate,
    Section,
    SplitView,
    Stack,
    Surface,
    Text,
    TextInput,
    html,
)
from hedron_core import Component, HtmlAttrValue, NodeLike

from app.dependencies import AuthContext
from app.ui.design_system import DATA_MOVER_DESIGN, surface_card
from app.ui.design_system import DataMoverPageHeader as PageHeader
from app.ui.forms import csrf_hidden, submit_button
from app.ui.layout import INDICATOR, account_summary, alert_box
from app.ui.urls import form_action, hx_attrs, page_href


def profile_form(
    request: Request, auth: AuthContext, *, csrf_token: str, success: str = ""
) -> Component[Any]:
    user = auth.user
    return Section(
        alert_box(success, kind="success"),
        Form(
            csrf_hidden(csrf_token),
            FormGrid(
                FormField(
                    name="email",
                    label="Government email",
                    id="email",
                    help="Verified addresses can only be changed through an administrator.",
                    control=TextInput(
                        "email",
                        id="email",
                        type="email",
                        value=user.email_original,
                        disabled=True,
                    ),
                ),
                FormField(
                    name="full_name",
                    label="Full name",
                    id="full_name",
                    help="Shown in the workspace and administrator records.",
                    control=TextInput(
                        "full_name",
                        id="full_name",
                        value=user.full_name or "",
                        autocomplete="name",
                    ),
                ),
                FormField(
                    name="organization",
                    label="Organization",
                    id="organization",
                    control=TextInput(
                        "organization",
                        id="organization",
                        value=user.organization or "",
                    ),
                ),
                FormField(
                    name="job_title",
                    label="Job title",
                    id="job_title",
                    control=TextInput(
                        "job_title",
                        id="job_title",
                        value=user.job_title or "",
                    ),
                ),
                columns=2,
                gap="md",
            ),
            FormField(
                name="phone",
                label="Work phone",
                id="phone",
                help="Optional contact number for account coordination.",
                control=TextInput(
                    "phone",
                    id="phone",
                    type="tel",
                    value=user.phone or "",
                    autocomplete="tel",
                ),
            ),
            submit_button("Save changes"),
            action=form_action(request, "profile"),
            method="post",
            **hx_attrs(
                request,
                path="profile",
                target="#profile-form-region",
                sync="this:drop",
                indicator=INDICATOR,
            ),
        ),
        id="profile-form-region",
    )


def profile_identity(request: Request, auth: AuthContext, *, oob: bool = False) -> NodeLike:
    """Render the profile summary in a native Hedron inset surface."""
    user = auth.user
    attrs: dict[str, HtmlAttrValue] = {"id": "profile-identity"}
    if oob:
        attrs["hx-swap-oob"] = "outerHTML"
    initial = (user.full_name or user.email_original or "?")[:1].upper()
    last_login = (
        user.last_login_at.strftime("%b %d, %Y %H:%M") if user.last_login_at else "First session"
    )
    return html.aside(
        DATA_MOVER_DESIGN.apply(
            "data-mover-inset",
            Surface(
                Stack(
                    Inline(
                        Avatar(
                            user.full_name or user.email_original,
                            mark=initial,
                            size="lg",
                            appearance="soft",
                        ),
                        Stack(
                            Heading(user.full_name or "Account holder", level=3),
                            Text(user.email_original),
                            gap="xs",
                        ),
                        gap="sm",
                    ),
                    DescriptionList(
                        ("Account status", Badge(user.status, tone="success")),
                        ("Access level", (", ".join(user.role_names) or "user").title()),
                        ("Created", user.created_at.strftime("%b %d, %Y")),
                        ("Last sign-in", last_login),
                        density="compact",
                    ),
                    LinkButton(
                        "Manage connections",
                        width="full",
                        href=page_href(request, "/security"),
                    ),
                    gap="md",
                )
            ),
        ),
        **attrs,
    )


def account_profile_panel(request: Request, auth: AuthContext, *, csrf_token: str) -> NodeLike:
    return SplitView(
        primary=surface_card(
            PageHeader(
                "Profile details",
                eyebrow="Workspace identity",
                description="Keep the details visible to application administrators current.",
                level=2,
                density="compact",
            ),
            profile_form(request, auth, csrf_token=csrf_token),
        ),
        secondary=profile_identity(request, auth),
        ratio="3:2",
        gap="lg",
        collapse="never",
    )


def profile_response(
    request: Request, auth: AuthContext, *, csrf_token: str, success: str
) -> tuple[Component[Any], tuple[OobUpdate, OobUpdate]]:
    form = profile_form(request, auth, csrf_token=csrf_token, success=success)
    oob = (
        OobUpdate(
            content=account_summary(request, auth, csrf_token=csrf_token, oob=False),
            element_id="account-summary",
            swap="outerHTML",
        ),
        OobUpdate(
            content=profile_identity(request, auth, oob=False),
            element_id="profile-identity",
            swap="outerHTML",
        ),
    )
    return form, oob
