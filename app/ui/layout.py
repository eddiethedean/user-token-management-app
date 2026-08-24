"""Layout shell matching Data Mover base template."""

from __future__ import annotations

from typing import Any

from fastapi import Request
from hedron import (
    AccountSummary,
    ActionGroup,
    Alert,
    AppFooter,
    AppShell,
    Badge,
    Brand,
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
    Popover,
    Section,
    Stack,
    StyleScope,
    Text,
    ThemePicker,
    html,
)
from hedron_core import Component, HtmlAttrValue, NodeLike
from hedron_core.builtins import (
    BusyRegion,
    RequestIndicator,
    SkipLink,
    SwapReveal,
    ThemePreference,
    ToastHost,
    resolve_theme_preference,
    theme_markers,
)

from app.config import Settings
from app.dependencies import AuthContext
from app.ui.forms import csrf_hidden, submit_button
from app.ui.urls import asset_href, form_action, page_href

INDICATOR = "#global-request-indicator"
THEME_COOKIE = "data_mover_theme"
COLOR_MODE_COOKIE = "data_mover_color_mode"
THEME_CHOICES = ("data-mover", "aurora")
COLOR_MODE_CHOICES = ("system", "light", "dark")

HTMX_CONFIG = (
    '{"includeIndicatorStyles":false,"allowEval":false,"allowScriptTags":false,'
    '"historyCacheSize":10,"refreshOnHistoryMiss":true,'
    '"responseHandling":[{"code":"204","swap":false},{"code":"[23]..","swap":true},'
    '{"code":"[45]..","swap":true,"error":true}]}'
)


def theme_preference_for_request(request: Request) -> ThemePreference:
    """Resolve the allowlisted Hedron 0.60 preference from host-owned cookies."""

    return resolve_theme_preference(
        request.cookies.get(THEME_COOKIE),
        request.cookies.get(COLOR_MODE_COOKIE),
        allowed_themes=THEME_CHOICES,
    )


def document_head(
    *,
    request: Request,
    page_title: str,
    app_name: str,
    custom_theme_enabled: bool,
    preference: ThemePreference | None = None,
) -> Fragment:
    preference = preference or ThemePreference()
    title = f"{page_title} · {app_name}" if page_title else app_name
    color_scheme = "light dark" if preference.color_mode == "system" else preference.color_mode
    nodes: list[NodeLike] = [
        html.meta(name="color-scheme", content=color_scheme),
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
    """Typed account chrome with a real action slot for HTMX OOB updates."""
    user = auth.user
    preference = theme_preference_for_request(request)
    attrs: dict[str, HtmlAttrValue] = {}
    if oob:
        attrs["hx-swap-oob"] = "outerHTML"
    return AccountSummary(
        user.full_name or user.email_original,
        detail=user.email_original if user.full_name else None,
        mark_text=(user.full_name or user.email_original or "?")[:1].upper(),
        action=ActionGroup(
            Popover(
                Stack(
                    Text("Personalize this workspace for your environment."),
                    ThemePicker(
                        themes=THEME_CHOICES,
                        color_modes=COLOR_MODE_CHOICES,
                        selected=preference,
                        action=form_action(request, "/preferences/theme"),
                        csrf_token=csrf_token,
                        compact=True,
                    ),
                    gap="sm",
                ),
                label="Appearance",
                placement="block-end",
                collision="shift",
            ),
            html.form(
                csrf_hidden(csrf_token),
                submit_button("Sign out", quiet=True, size="sm"),
                action=form_action(request, "logout"),
                method="post",
            ),
            gap="xs",
            collapse="never",
        ),
        id="account-summary",
        attrs=attrs,
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
        )

    workspace = [
        link("/pipeline", "01", "Pipeline"),
        link("/security", "02", "Connections"),
        link("/profile", "03", "Account"),
    ]
    children: list[NodeLike] = [
        html.div(
            html.p("Workspace", class_="hedron-nav-group-label"),
            html.div(*workspace, class_="hedron-nav-group-items"),
            class_="hedron-nav-group",
            role="group",
            aria={"label": "Workspace"},
            data={"hedron-nav-group": "true"},
        )
    ]
    if "administrator" in auth.user.role_names:
        children.append(
            html.div(
                html.p("Administration", class_="hedron-nav-group-label"),
                html.div(
                    link("/admin/users", "04", "Team"),
                    link("/admin/audit", "05", "Activity"),
                    class_="hedron-nav-group-items",
                ),
                class_="hedron-nav-group",
                role="group",
                aria={"label": "Administration"},
                data={"hedron-nav-group": "true"},
            )
        )
    return children


def side_nav(request: Request, auth: AuthContext) -> Nav:
    """Typed navigation landmark composed into Hedron's AppShell."""
    return Nav(
        *side_nav_children(request, auth),
        id="side-nav",
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
    host = ToastHost(
        placement="top-end",
        position="fixed",
        width="content",
        max_width="md",
        gap="sm",
    )
    if oob:
        return html.div(host, hx_swap_oob="outerHTML")
    return host


def dialog_host() -> NodeLike:
    return html.div(id="dialog-host")


def app_shell(
    *body: NodeLike,
    request: Request,
    settings: Settings,
    auth: AuthContext | None,
    page_title: str,
    csrf_token: str = "",
) -> Page:
    preference = theme_preference_for_request(request)
    markers = theme_markers(preference)
    brand = Brand(
        settings.app_name,
        mark_text="DM",
        subtitle="Secure transfer operations",
        subtitle_overflow="truncate",
        href=page_href(request, "/"),
        aria={"label": f"{settings.app_name} home"},
    )
    environment_badge = Badge("Sandbox online", tone="success")
    indicator = RequestIndicator(
        "Working…",
        id="global-request-indicator",
        placement="top",
    )
    skip = SkipLink(target="#main-content", label="Skip to main content")
    content: NodeLike
    banner: NodeLike | None = EnvironmentBanner(
        "Controlled demo workspace · Transfers are simulated and remote endpoints stay untouched",
        tone="warning",
    )
    header: NodeLike | None = None
    footer: NodeLike | None = None
    if auth:
        content = AppShell(
            nav=side_nav(request, auth),
            body=main_panel(
                *body,
                theme=preference.theme,
                color_mode=(preference.color_mode if preference.color_mode != "system" else None),
            ),
            panel_id="main-content",
            banner=banner,
            brand=brand,
            env_badge=environment_badge,
            account=(account_summary(request, auth, csrf_token=csrf_token) if csrf_token else None),
            nav_footer=shell_nav_footer(),
            app_footer=AppFooter(
                settings.app_name,
                html.span("Demo environment · No remote systems are contacted"),
            ),
            content_width="wide",
        )
    else:
        header = html.header(
            Container(
                ActionGroup(
                    brand,
                    Inline(
                        Badge("Demo workspace", tone="warning"),
                        environment_badge,
                        gap="sm",
                    ),
                    align="between",
                    gap="sm",
                    collapse="sm",
                )
            ),
        )
        content = StyleScope(
            html.main(
                Container(
                    *body,
                    query="inline-size",
                    name="auth",
                ),
                id="main-content",
                tabindex="-1",
            ),
            theme=preference.theme,
            color_mode=preference.color_mode if preference.color_mode != "system" else None,
            variant="auth",
            design="data-mover",
            recipe_defaults={
                "surface": "data-mover-auth-panel",
                "content": "data-mover-supporting-copy",
            },
        )
        footer = AppFooter(
            settings.app_name,
            html.span("Demo environment · No remote systems are contacted"),
        )
    page_nodes: list[NodeLike] = [skip, indicator, toast_host(), dialog_host()]
    if auth:
        page_nodes.append(content)
    else:
        page_nodes.extend(item for item in (banner, header, content, footer) if item is not None)
    return Page(
        *page_nodes,
        title=page_title or settings.app_name,
        data_theme=markers["data-theme"],
        data_hedron_theme=markers["data-hedron-theme"],
        head=document_head(
            request=request,
            page_title=page_title,
            app_name=settings.app_name,
            custom_theme_enabled=settings.custom_theme_enabled,
            preference=preference,
        ),
    )


def page_heading(eyebrow: str, title: str, lead: str, *extra: NodeLike) -> PageHeader:
    """Use Hedron's native page header while preserving optional page actions."""
    return PageHeader(
        title,
        eyebrow=eyebrow,
        description=lead,
        meta=extra[0] if len(extra) == 1 else None,
        density="comfortable",
    )


def main_panel(
    *body: NodeLike,
    theme: str = "data-mover",
    color_mode: str | None = None,
) -> Component[Any]:
    """Authenticated main panel root used for in-shell HTMX navigation swaps."""
    return BusyRegion(
        SwapReveal(
            StyleScope(
                Container(
                    Section(*body, id="main-panel"),
                    query="inline-size",
                    name="workspace",
                ),
                theme=theme,
                color_mode=color_mode,
                density="comfortable",
                variant="workspace",
                design="data-mover",
                recipe_defaults={
                    "control": "data-mover-primary-action",
                    "surface": "data-mover-panel",
                    "data": "data-mover-compact-data",
                    "flow": "data-mover-flow",
                },
            )
        ),
        scope="document",
        indicator="#global-request-indicator",
    )
