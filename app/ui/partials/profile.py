"""Profile page fragments."""

from __future__ import annotations

from typing import Any

from hedron import Badge, Form, FormField, Heading, Section, TextInput, html
from hedron_core import Component, HtmlAttrValue, NodeLike

from app.dependencies import AuthContext
from app.ui.layout import INDICATOR, account_summary, alert_box
from app.ui.urls import form_action, hx_attrs, page_href


def profile_form(auth: AuthContext, *, csrf_token: str, success: str = "") -> Component[Any]:
    user = auth.user
    return Section(
        alert_box(success, kind="success"),
        Form(
            html.input(type="hidden", name="csrf_token", value=csrf_token),
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
                html.button("Save changes", class_="button button-primary", type="submit"),
                html.span("Saving…", class_="htmx-indicator"),
                class_="form-actions",
            ),
            class_="stack-form",
            action=form_action("profile"),
            method="post",
            **hx_attrs(
                path="profile",
                target="#profile-form-region",
                sync="this:drop",
                disabled_elt="find button",
                indicator=INDICATOR,
            ),
        ),
        id="profile-form-region",
    )


def profile_identity(auth: AuthContext, *, oob: bool = False) -> NodeLike:
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
            "Review account security",
            class_="button button-secondary button-wide",
            href=page_href("/security"),
        ),
        **attrs,
    )


def profile_response(auth: AuthContext, *, csrf_token: str, success: str) -> list[NodeLike]:
    return [
        profile_form(auth, csrf_token=csrf_token, success=success),
        account_summary(auth, csrf_token=csrf_token, oob=True),
        profile_identity(auth, oob=True),
    ]
