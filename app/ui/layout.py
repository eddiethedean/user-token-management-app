"""Layout shell matching Access Registry base template."""

from __future__ import annotations

from typing import Any

from fastapi import Request
from hedron import (
    Alert,
    Footer,
    Fragment,
    Header,
    Heading,
    OobUpdate,
    Page,
    Section,
    Stack,
    Text,
    html,
)
from hedron_core import Component, HtmlAttrValue, NodeLike

from app.config import Settings
from app.dependencies import AuthContext
from app.ui.forms import csrf_hidden, submit_button
from app.ui.urls import asset_href, form_action, hx_attrs, page_href

INDICATOR = "#global-request-indicator"

HTMX_CONFIG = (
    '{"includeIndicatorStyles":false,"allowEval":false,"allowScriptTags":false,'
    '"historyCacheSize":10,"refreshOnHistoryMiss":true,'
    '"responseHandling":[{"code":"204","swap":false},{"code":"[23]..","swap":true},'
    '{"code":"[45]..","swap":true,"error":true}]}'
)


def document_head(*, request: Request, page_title: str, app_name: str) -> Fragment:
    title = f"{page_title} · {app_name}" if page_title else app_name
    return Fragment(
        html.meta(name="color-scheme", content="dark"),
        html.meta(name="theme-color", content="#080d1a"),
        html.meta(name="htmx-config", content=HTMX_CONFIG),
        html.title(title),
        html.link(
            rel="stylesheet",
            href=asset_href(request, "/assets/theme.css"),
        ),
    )


def alert_box(message: str, *, kind: str = "error") -> Alert | Fragment:
    if not message:
        return Fragment()
    tone = "success" if kind == "success" else "danger"
    return Alert(message, tone=tone)


def account_summary(
    request: Request, auth: AuthContext, *, csrf_token: str, oob: bool = False
) -> NodeLike:
    """Account chrome; stays on html.* so hx-swap-oob and nested form attrs remain valid."""
    user = auth.user
    attrs: dict[str, HtmlAttrValue] = {"id": "account-summary", "class_": "account-summary"}
    if oob:
        attrs["hx-swap-oob"] = "outerHTML"
    return html.div(
        html.span(
            (user.full_name or user.email_original or "?")[:1].upper(),
            class_="account-avatar",
            aria={"hidden": "true"},
        ),
        html.span(
            html.strong(user.full_name or user.email_original),
            html.small(user.email_original),
            class_="account-copy",
        ),
        html.form(
            csrf_hidden(csrf_token),
            submit_button("Sign out", quiet=True, small=True),
            action=form_action(request, "logout"),
            method="post",
        ),
        **attrs,
    )


def side_nav_children(request: Request, auth: AuthContext) -> list[NodeLike]:
    path = str(request.scope.get("path") or "/")
    normalized = path.rstrip("/") or "/"

    def link(href: str, number: str, label: str) -> NodeLike:
        href_norm = href.rstrip("/") or "/"
        active = "active" if normalized == href_norm else ""
        return html.a(
            html.span(number, aria={"hidden": "true"}),
            f" {label}",
            class_=f"nav-link {active}".strip(),
            href=page_href(request, href),
            **hx_attrs(
                request,
                method="get",
                path=href.lstrip("/"),
                target="#main-panel",
                swap="outerHTML",
                push_url=True,
                select="#main-panel",
                disabled_elt="#side-nav a.nav-link",
                indicator=INDICATOR,
            ),
        )

    children: list[NodeLike] = [
        html.p("Workspace", class_="nav-label"),
        link("/profile", "01", "Profile"),
        link("/security", "02", "Security"),
    ]
    if "administrator" in auth.user.role_names:
        children.append(html.p("Administration", class_="nav-label nav-label-spaced"))
        children.append(link("/admin/users", "03", "Users"))
        children.append(link("/admin/audit", "04", "Audit log"))
    children.append(
        html.div(
            html.span(class_="status-dot", aria={"hidden": "true"}),
            html.div(html.strong("Protected session"), html.small("Encrypted connection")),
            class_="nav-status",
        )
    )
    return children


def side_nav(request: Request, auth: AuthContext, *, oob: bool = False) -> NodeLike:
    """Side nav keeps html.nav for aria-label and optional hx-swap-oob."""
    attrs: dict[str, HtmlAttrValue] = {
        "id": "side-nav",
        "class_": "side-nav",
        "aria": {"label": "Account navigation"},
    }
    if oob:
        attrs["hx-swap-oob"] = "outerHTML"
    return html.nav(*side_nav_children(request, auth), **attrs)


def side_nav_oob(request: Request, auth: AuthContext) -> OobUpdate:
    """Replace side-nav contents after in-shell navigation (preserves outer nav element)."""
    return OobUpdate(
        content=Fragment(*side_nav_children(request, auth)),
        element_id="side-nav",
        swap="innerHTML",
    )


def toast_host(*, oob: bool = False) -> NodeLike:
    attrs: dict[str, HtmlAttrValue] = {
        "id": "toast-host",
        "class_": "toast-host",
        "aria": {"live": "polite"},
    }
    if oob:
        attrs["hx-swap-oob"] = "outerHTML"
    return html.div(**attrs)


def dialog_host() -> NodeLike:
    return html.div(id="dialog-host", class_="dialog-host")


def app_shell(
    *body: NodeLike,
    request: Request,
    settings: Settings,
    auth: AuthContext | None,
    page_title: str,
    csrf_token: str = "",
) -> Page:
    banner = html.div(
        html.div(
            html.span("▦", class_="flag-mark", aria={"hidden": "true"}),
            html.span("Official use system · Activity may be monitored"),
            class_="page-width banner-inner",
        ),
        class_="official-banner",
    )
    header_children: list[NodeLike] = [
        html.a(
            html.span("AR", class_="brand-mark", aria={"hidden": "true"}),
            html.span(
                html.strong(settings.app_name),
                html.small("Identity and access services"),
            ),
            html.span("Protected workspace", class_="brand-chip"),
            class_="brand",
            href=page_href(request, "/"),
            aria={"label": f"{settings.app_name} home"},
        )
    ]
    if auth and csrf_token:
        header_children.append(account_summary(request, auth, csrf_token=csrf_token))
    header = Header(
        html.div(*header_children, class_="page-width header-inner"),
        class_="site-header",
    )
    feedback = html.div(id="global-feedback", class_="global-feedback", aria={"live": "assertive"})
    indicator = html.div(
        "Working…",
        id="global-request-indicator",
        class_="global-request-indicator htmx-indicator",
        role="status",
        aria={"live": "polite"},
    )
    skip = html.a(
        "Skip to main content", class_="skip-link", href=page_href(request, "/#main-content")
    )
    footer = Footer(
        html.div(
            html.span(settings.app_name),
            html.span(
                "Authorized use only · Never include classified information in support requests"
            ),
            class_="page-width footer-inner",
        ),
        class_="site-footer",
    )
    if auth:
        content = html.div(
            side_nav(request, auth),
            # html.main keeps tabindex for skip-link focus management.
            html.main(
                main_panel(*body),
                id="main-content",
                class_="main-content",
                tabindex="-1",
            ),
            class_="page-width app-shell",
        )
    else:
        content = html.main(*body, id="main-content", class_="auth-main", tabindex="-1")
    return Page(
        skip,
        feedback,
        indicator,
        toast_host(),
        dialog_host(),
        banner,
        header,
        content,
        footer,
        title=page_title or settings.app_name,
        head=document_head(request=request, page_title=page_title, app_name=settings.app_name),
    )


def page_heading(eyebrow: str, title: str, lead: str, *extra: NodeLike) -> Stack:
    return Stack(
        html.div(
            html.p(eyebrow, class_="eyebrow"),
            Heading(title, level=1),
            Text(lead),
        ),
        *extra,
        class_="page-heading",
        gap="0px",
    )


def main_panel(*body: NodeLike) -> Component[Any]:
    """Authenticated main panel root used for in-shell HTMX navigation swaps."""
    return Section(*body, id="main-panel", class_="main-panel")
