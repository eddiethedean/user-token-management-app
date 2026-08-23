"""Layout shell matching Data Mover base template."""

from __future__ import annotations

from typing import Any

from fastapi import Request
from hedron import (
    Alert,
    AppShell,
    Badge,
    Container,
    EnvironmentBanner,
    Fragment,
    HtmxLink,
    Inline,
    Nav,
    NavStatus,
    OobUpdate,
    Page,
    PageHeader,
    Section,
    StyleScope,
    html,
)
from hedron_core import Component, HtmlAttrValue, NodeLike
from hedron_core.builtins import BusyRegion, RequestIndicator, SkipLink, SwapReveal, ToastHost

from app.config import Settings
from app.dependencies import AuthContext
from app.ui.forms import csrf_hidden, submit_button
from app.ui.urls import asset_href, form_action, page_href

INDICATOR = "#global-request-indicator"

HTMX_CONFIG = (
    '{"includeIndicatorStyles":false,"allowEval":false,"allowScriptTags":false,'
    '"historyCacheSize":10,"refreshOnHistoryMiss":true,'
    '"responseHandling":[{"code":"204","swap":false},{"code":"[23]..","swap":true},'
    '{"code":"[45]..","swap":true,"error":true}]}'
)


def document_head(
    *, request: Request, page_title: str, app_name: str, custom_theme_enabled: bool
) -> Fragment:
    title = f"{page_title} · {app_name}" if page_title else app_name
    nodes: list[NodeLike] = [
        html.meta(name="color-scheme", content="dark"),
        html.meta(name="theme-color", content="#080d1a"),
        html.meta(name="htmx-config", content=HTMX_CONFIG),
        html.title(title),
    ]
    if custom_theme_enabled:
        nodes.append(
            html.link(
                rel="stylesheet",
                href=asset_href(request, "/assets/theme.css"),
            )
        )
    return Fragment(*nodes)


def alert_box(message: str, *, kind: str = "error") -> Alert | Fragment:
    if not message:
        return Fragment()
    tone = {"success": "success", "info": "info", "warning": "warning"}.get(kind, "danger")
    return Alert(message, tone=tone)


def account_summary(
    request: Request, auth: AuthContext, *, csrf_token: str, oob: bool = False
) -> NodeLike:
    """Account chrome; stays on html.* so hx-swap-oob and nested form attrs remain valid."""
    user = auth.user
    attrs: dict[str, HtmlAttrValue] = {
        "id": "account-summary",
        "class_": "account-summary",
    }
    if oob:
        attrs["hx-swap-oob"] = "outerHTML"
    return Inline(
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
    from starlette._utils import get_route_path

    path = get_route_path(request.scope)
    normalized = path.rstrip("/") or "/"

    def link(href: str, number: str, label: str) -> NodeLike:
        href_norm = href.rstrip("/") or "/"
        active = (
            "active" if normalized == href_norm or normalized.startswith(f"{href_norm}/") else ""
        )
        return HtmxLink(
            f"{number} · {label}",
            page_href(request, href),
            target="#main-panel",
            swap="outerHTML",
            push_url=True,
            select="#main-panel",
            disabled_elt="#side-nav",
            indicator=INDICATOR,
            preload="mouseover",
            active=bool(active),
            class_="nav-link",
        )

    children: list[NodeLike] = [
        html.p("Workspace", class_="nav-label"),
        link("/pipeline", "01", "Pipeline"),
        link("/security", "02", "Connections"),
        link("/profile", "03", "Account"),
    ]
    if "administrator" in auth.user.role_names:
        children.append(html.p("Administration", class_="nav-label nav-label-spaced"))
        children.append(link("/admin/users", "04", "Team"))
        children.append(link("/admin/audit", "05", "Activity"))
    return children


def side_nav(request: Request, auth: AuthContext) -> Nav:
    """Typed navigation landmark composed into Hedron's AppShell."""
    return Nav(
        *side_nav_children(request, auth),
        id="side-nav",
        class_="side-nav",
        aria={"label": "Account navigation"},
    )


def shell_nav_footer() -> NavStatus:
    """Use Hedron's typed AppShell status slot for workspace health."""
    return NavStatus(
        "Sandbox healthy · Credentials encrypted",
        tone="success",
        mark="●",
    )


def side_nav_oob(request: Request, auth: AuthContext) -> OobUpdate:
    """Replace side-nav contents after in-shell navigation (preserves outer nav element)."""
    return OobUpdate(
        content=Fragment(*side_nav_children(request, auth)),
        element_id="side-nav",
        swap="innerHTML",
    )


def toast_host(*, oob: bool = False) -> NodeLike:
    host = ToastHost()
    if oob:
        return html.div(host, hx_swap_oob="outerHTML")
    return host


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
    brand = html.a(
        html.span("DM", class_="brand-mark", aria={"hidden": "true"}),
        html.span(
            html.strong(settings.app_name),
            html.small("Secure data movement"),
        ),
        class_="brand",
        href=page_href(request, "/"),
        aria={"label": f"{settings.app_name} home"},
    )
    environment_badge = Badge("Sandbox online", tone="success")
    feedback = html.div(id="global-feedback", class_="global-feedback", aria={"live": "assertive"})
    indicator = RequestIndicator(
        "Working…",
        id="global-request-indicator",
        placement="top",
    )
    skip = SkipLink(target="#main-content", label="Skip to main content")
    content: NodeLike
    banner: NodeLike | None = None
    header: NodeLike | None = None
    footer: NodeLike | None = None
    if auth:
        content = AppShell(
            nav=side_nav(request, auth),
            body=main_panel(*body),
            panel_id="main-content",
            banner=EnvironmentBanner(
                "Controlled workspace · Transfers are simulated in demo mode",
                tone="info",
                mark="▦",
            ),
            brand=brand,
            env_badge=environment_badge,
            account=(account_summary(request, auth, csrf_token=csrf_token) if csrf_token else None),
            nav_footer=shell_nav_footer(),
            app_footer=Container(
                html.span(settings.app_name),
                html.span("Demo environment · No remote systems are contacted"),
                class_="footer-inner",
            ),
            content_width="wide",
        )
    else:
        banner = Container(
            EnvironmentBanner(
                "Controlled workspace · Transfers are simulated in demo mode",
                tone="info",
                mark="▦",
            ),
            class_="banner-inner",
        )
        header = html.header(
            Container(brand, environment_badge, class_="header-inner"),
            class_="site-header",
        )
        content = html.main(*body, id="main-content", class_="auth-main", tabindex="-1")
        footer = html.footer(
            Container(
                html.span(settings.app_name),
                html.span("Demo environment · No remote systems are contacted"),
                class_="footer-inner",
            ),
            class_="site-footer",
        )
    page_nodes: list[NodeLike] = [skip, feedback, indicator, toast_host(), dialog_host()]
    if auth:
        page_nodes.append(content)
    else:
        page_nodes.extend(item for item in (banner, header, content, footer) if item is not None)
    return Page(
        *page_nodes,
        title=page_title or settings.app_name,
        head=document_head(
            request=request,
            page_title=page_title,
            app_name=settings.app_name,
            custom_theme_enabled=settings.custom_theme_enabled,
        ),
    )


def page_heading(eyebrow: str, title: str, lead: str, *extra: NodeLike) -> PageHeader:
    """Use Hedron's native page header while preserving optional page actions."""
    return PageHeader(
        title,
        eyebrow=eyebrow,
        description=lead,
        actions=extra[0] if len(extra) == 1 else None,
        class_="page-heading",
    )


def main_panel(*body: NodeLike) -> Component[Any]:
    """Authenticated main panel root used for in-shell HTMX navigation swaps."""
    return BusyRegion(
        SwapReveal(
            StyleScope(
                Section(*body, id="main-panel", class_="main-panel"),
                theme="aurora",
                density="comfortable",
            )
        ),
        scope="document",
        indicator="#global-request-indicator",
    )
