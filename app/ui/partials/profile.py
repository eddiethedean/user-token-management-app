"""Profile page fragments."""

from __future__ import annotations

from hedron import html
from hedron_core import HtmlAttrValue, NodeLike

from app.dependencies import AuthContext
from app.ui.layout import INDICATOR, account_summary, alert_box
from app.ui.urls import form_action, hx_attrs, page_href


def profile_form(auth: AuthContext, *, csrf_token: str, success: str = "") -> NodeLike:
    user = auth.user
    return html.div(
        alert_box(success, kind="success"),
        html.form(
            html.input(type="hidden", name="csrf_token", value=csrf_token),
            html.div(
                html.div(
                    html.label("Government email", for_="email"),
                    html.input(id="email", value=user.email_original, disabled=True),
                    html.p(
                        "Verified addresses can only be changed through an administrator.",
                        class_="field-help",
                    ),
                    class_="field-full",
                ),
                html.div(
                    html.label("Full name", for_="full_name"),
                    html.input(
                        id="full_name",
                        name="full_name",
                        value=user.full_name or "",
                        autocomplete="name",
                        maxlength="160",
                    ),
                    class_="field-full",
                ),
                html.div(
                    html.label("Organization", for_="organization"),
                    html.input(
                        id="organization",
                        name="organization",
                        value=user.organization or "",
                        maxlength="160",
                    ),
                ),
                html.div(
                    html.label("Job title", for_="job_title"),
                    html.input(
                        id="job_title",
                        name="job_title",
                        value=user.job_title or "",
                        maxlength="160",
                    ),
                ),
                html.div(
                    html.label("Work phone", for_="phone"),
                    html.input(
                        id="phone",
                        name="phone",
                        value=user.phone or "",
                        autocomplete="tel",
                        maxlength="40",
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
        html.h2(user.full_name or "Account holder"),
        html.p(user.email_original),
        html.dl(
            html.div(
                html.dt("Account status"),
                html.dd(html.span(user.status, class_="pill pill-active")),
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
