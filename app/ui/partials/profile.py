"""Profile page fragments."""

from __future__ import annotations

from typing import Any

from fastapi import Request
from hedron import Badge, Form, FormField, Heading, OobUpdate, Section, TextInput, html
from hedron_core import Component, HtmlAttrValue, NodeLike

from app.dependencies import AuthContext
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
            html.div(
                html.div(
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
                    class_="field-full",
                ),
                html.div(
                    FormField(
                        name="full_name",
                        label="Full name",
                        id="full_name",
                        control=TextInput(
                            "full_name",
                            id="full_name",
                            value=user.full_name or "",
                            autocomplete="name",
                        ),
                    ),
                    class_="field-full",
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
                html.div(
                    FormField(
                        name="phone",
                        label="Work phone",
                        id="phone",
                        control=TextInput(
                            "phone",
                            id="phone",
                            type="tel",
                            value=user.phone or "",
                            autocomplete="tel",
                        ),
                    ),
                    class_="field-full",
                ),
                class_="field-grid",
            ),
            html.div(
                submit_button("Save changes"),
                html.span("Saving…", class_="htmx-indicator"),
                class_="form-actions",
            ),
            class_="stack-form",
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
    """Identity aside keeps html.* for hx-swap-oob; uses Badge for status."""
    user = auth.user
    attrs: dict[str, HtmlAttrValue] = {"id": "profile-identity", "class_": "panel identity-panel"}
    if oob:
        attrs["hx-swap-oob"] = "outerHTML"
    initial = (user.full_name or user.email_original or "?")[:1].upper()
    last_login = (
        user.last_login_at.strftime("%b %d, %Y %H:%M") if user.last_login_at else "First session"
    )
    return html.aside(
        html.div(initial, class_="identity-avatar"),
        Heading(user.full_name or "Account holder", level=2),
        html.p(user.email_original),
        html.dl(
            html.div(
                html.dt("Account status"),
                html.dd(Badge(user.status, tone="success")),
            ),
            html.div(
                html.dt("Access level"),
                html.dd((", ".join(user.role_names) or "user").title()),
            ),
            html.div(
                html.dt("Created"),
                html.dd(user.created_at.strftime("%b %d, %Y")),
            ),
            html.div(
                html.dt("Last sign-in"),
                html.dd(last_login),
            ),
            class_="detail-list",
        ),
        html.a(
            "Manage connections",
            class_="button button-secondary button-wide",
            href=page_href(request, "/security"),
        ),
        **attrs,
    )


def account_profile_panel(request: Request, auth: AuthContext, *, csrf_token: str) -> NodeLike:
    return html.div(
        html.section(
            html.div(
                html.div(
                    html.h2("Profile details"),
                    html.p("Information shown to application administrators."),
                ),
                class_="panel-heading",
            ),
            profile_form(request, auth, csrf_token=csrf_token),
            class_="panel panel-main",
        ),
        profile_identity(request, auth),
        class_="content-grid profile-grid",
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
