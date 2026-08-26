"""Layout shell matching Data Mover base template."""

from __future__ import annotations

from typing import Any

from fastapi import Request
from hedron import (
    AccountSummary,
    ActionGroup,
    Alert,
    AmbientCanvas,
    AmbientLayer,
    AppFooter,
    AppShell,
    AppShellChrome,
    Badge,
    Brand,
    Container,
    EnvironmentBanner,
    Fragment,
    Header,
    HtmxLink,
    IconButton,
    Image,
    Inline,
    Nav,
    NavGroup,
    NavStatus,
    OobUpdate,
    Page,
    Section,
    Stack,
    StyleScope,
    Surface,
    Text,
    ToggleSwitch,
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
from starlette._utils import get_route_path
from starlette.responses import Response

from app.config import Settings
from app.dependencies import AuthContext
from app.ui.design_system import APP_SHELL_NAV_STYLE_CLASS
from app.ui.design_system import DataMoverPageHeader as PageHeader
from app.ui.forms import csrf_hidden, submit_button
from app.ui.icons import NAV_ICONS
from app.ui.urls import asset_href, asset_src, form_action, hx_attrs, page_href

INDICATOR = "#global-request-indicator"
THEME_COOKIE = "data_mover_theme"
COLOR_MODE_COOKIE = "data_mover_color_mode"
THEME_CHOICES = ("data-mover", "aurora")
UI_PREFERENCE_MAX_AGE = 31536000

HTMX_CONFIG = (
    '{"includeIndicatorStyles":false,"allowEval":false,"allowScriptTags":false,'
    '"historyCacheSize":10,"refreshOnHistoryMiss":true,'
    '"responseHandling":[{"code":"204","swap":false},{"code":"[23]..","swap":true},'
    '{"code":"[45]..","swap":true,"error":true}]}'
)


def theme_preference_for_request(
    request: Request,
    *,
    default_color_mode: str = "light",
) -> ThemePreference:
    """Resolve the allowlisted Hedron 0.66.1 light/dark preference."""

    color_mode = request.cookies.get(COLOR_MODE_COOKIE)
    if color_mode not in {"light", "dark"}:
        color_mode = default_color_mode if default_color_mode in {"light", "dark"} else "light"

    return resolve_theme_preference(
        request.cookies.get(THEME_COOKIE),
        color_mode,
        allowed_themes=THEME_CHOICES,
    )


def set_color_mode_cookie(
    response: Response,
    *,
    request: Request,
    settings: Settings,
    color_mode: str,
) -> None:
    """Persist a validated account color mode in the current browser."""

    mode = color_mode if color_mode in {"light", "dark"} else "light"
    path = "/" if settings.cookie_path == "auto" else settings.cookie_path
    common = {
        "secure": settings.cookie_secure,
        "httponly": True,
        "samesite": "lax",
        "path": path,
    }
    if path not in {None, "/"}:
        response.delete_cookie(
            COLOR_MODE_COOKIE,
            secure=settings.cookie_secure,
            httponly=True,
            samesite="lax",
            path="/",
        )
    response.set_cookie(
        COLOR_MODE_COOKIE,
        mode,
        max_age=UI_PREFERENCE_MAX_AGE,
        **common,
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
    color_scheme = preference.color_mode
    theme_color = "#080d1a" if preference.color_mode == "dark" else "#f6f6fb"
    nodes: list[NodeLike] = [
        html.meta(name="color-scheme", content=color_scheme),
        html.meta(name="theme-color", content=theme_color),
        html.meta(name="htmx-config", content=HTMX_CONFIG),
        html.title(title),
        html.link(
            rel="icon",
            type="image/png",
            href=asset_href(request, "/assets/brand/data-mover-mark.png"),
        ),
        html.link(
            rel="stylesheet",
            href=asset_href(request, "/app-assets/hedron-desktop.css?v=2"),
        ),
    ]
    if custom_theme_enabled:
        nodes.append(
            html.link(
                rel="stylesheet",
                href=asset_href(request, "/assets/theme.css?v=8"),
            )
        )
        nodes.append(
            html.link(
                rel="stylesheet",
                href=asset_href(request, "/app-assets/data-mover-components.css?v=6"),
            )
        )
    return Fragment(*nodes)


def alert_box(message: str, *, kind: str = "error") -> Alert | Fragment:
    if not message:
        return Fragment()
    tone = {"success": "success", "info": "info", "warning": "warning"}.get(kind, "danger")
    return Alert(message, tone=tone)


def color_mode_toggle(request: Request, *, csrf_token: str) -> NodeLike:
    """Render the native light/dark switch as a standalone shell utility."""

    preference = theme_preference_for_request(request)
    return html.form(
        csrf_hidden(csrf_token),
        html.input(type="hidden", name="theme", value=preference.theme),
        html.input(type="hidden", name="next", value=get_route_path(request.scope)),
        ToggleSwitch(
            "dark_mode",
            "Dark mode",
            checked=preference.color_mode == "dark",
            mark="color-mode-toggle",
        ),
        html.noscript(submit_button("Apply mode", quiet=True, size="sm")),
        action=form_action(request, "/preferences/theme"),
        method="post",
        data={"color-mode-form": "true"},
        **hx_attrs(
            request,
            method="post",
            path="/preferences/theme",
            swap="none",
        ),
    )


def account_summary(
    request: Request, auth: AuthContext, *, csrf_token: str, oob: bool = False
) -> NodeLike:
    """Typed account chrome with a real action slot for HTMX OOB updates."""
    user = auth.user
    attrs: dict[str, HtmlAttrValue] = {}
    if oob:
        attrs["hx-swap-oob"] = "outerHTML"
    return AccountSummary(
        user.full_name or user.email_original,
        detail=user.email_original if user.full_name else None,
        mark_text=(user.full_name or user.email_original or "?")[:1].upper(),
        mark_size="lg",
        mark_shape="circle",
        mark_tone="accent",
        action=ActionGroup(
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
        class_="data-mover-account-summary",
        attrs=attrs,
    )


def side_nav_children(request: Request, auth: AuthContext) -> list[NodeLike]:
    path = get_route_path(request.scope)
    normalized = path.rstrip("/") or "/"

    def link(href: str, label: str, *, icon: str) -> NodeLike:
        href_norm = href.rstrip("/") or "/"
        active = (
            "active" if normalized == href_norm or normalized.startswith(f"{href_norm}/") else ""
        )
        return html.div(
            HtmxLink(
                label,
                page_href(request, href),
                target="#main-panel",
                swap="outerHTML",
                push_url=True,
                select="#main-panel",
                disabled_elt="#side-nav",
                indicator=INDICATOR,
                preload="mouseover",
                active=bool(active),
                class_=APP_SHELL_NAV_STYLE_CLASS,
                leading_icon=NAV_ICONS[icon],
            ),
            class_="data-mover-nav-item",
            title=label,
        )

    workspace = [
        link("/pipeline", "Pipeline", icon="pipeline"),
        link("/security", "Connections", icon="connections"),
        link("/profile", "Account", icon="account"),
    ]
    children: list[NodeLike] = [
        html.div(
            IconButton(
                "Collapse navigation",
                icon="‹",
                size="sm",
                appearance="ghost",
                emphasis="neutral",
                id="side-nav-toggle",
                class_="data-mover-nav-toggle",
            ),
            class_="data-mover-nav-toggle-row",
        ),
        NavGroup("Workspace", *workspace),
    ]
    if "administrator" in auth.user.role_names:
        children.append(
            NavGroup(
                "Administration",
                link("/admin/users", "Team", icon="team"),
                link("/admin/audit", "Activity", icon="activity"),
            )
        )
    return children


def side_nav(request: Request, auth: AuthContext) -> Nav:
    """Typed navigation landmark composed into Hedron's AppShell."""
    return Nav(
        *side_nav_children(request, auth),
        id="side-nav",
        class_="data-mover-side-nav",
        aria={"label": "Account navigation"},
    )


def shell_nav_footer() -> NavStatus:
    """Use Hedron's typed AppShell status slot for workspace health."""
    return NavStatus(
        "Sandbox healthy · Credentials encrypted",
        tone="success",
        mark="●",
        class_="data-mover-nav-footer",
    )


def side_nav_oob(request: Request, auth: AuthContext) -> OobUpdate:
    """Replace side-nav contents after in-shell navigation (preserves outer nav element)."""
    return OobUpdate(
        content=Fragment(*side_nav_children(request, auth), shell_nav_footer()),
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


def data_mover_mark(request: Request, preference: ThemePreference) -> NodeLike:
    """Render the theme-matched product mark, including system-mode selection."""

    light_src = asset_src(request, "/assets/brand/data-mover-mark-light.png")
    dark_src = asset_src(request, "/assets/brand/data-mover-mark-dark.png")
    if preference.color_mode == "dark":
        return Image(dark_src, alt="", width=48)
    if preference.color_mode == "light":
        return Image(light_src, alt="", width=48)
    return html.picture(
        html.source(
            srcset=dark_src,
            media="(prefers-color-scheme: dark)",
            type="image/png",
        ),
        Image(light_src, alt="", width=48),
    )


def app_shell(
    *body: NodeLike,
    request: Request,
    settings: Settings,
    auth: AuthContext | None,
    page_title: str,
    csrf_token: str = "",
    default_color_mode: str = "light",
) -> Page:
    preference = theme_preference_for_request(
        request,
        default_color_mode=default_color_mode,
    )
    markers = theme_markers(preference)
    brand = Brand(
        settings.app_name,
        mark_content=data_mover_mark(request, preference),
        mark_size="lg",
        mark_shape="rounded",
        mark_tone="neutral",
        subtitle="Secure transfer operations",
        subtitle_overflow="truncate",
        href=page_href(request, "/"),
        aria={"label": f"{settings.app_name} home"},
        class_="data-mover-brand",
    )
    cdao_identity = Inline(
        html.span(
            Image(
                asset_src(request, "/assets/brand/cdao-mark.png"),
                alt="Chief Digital and Artificial Intelligence Office",
                width=18,
                height=26,
            ),
            class_="data-mover-cdao-mark",
        ),
        Text("CDAO", as_="strong", role="caption", effect="subtle"),
        gap="xs",
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
    if auth:
        content = Container(
            AmbientCanvas(
                AppShell(
                    nav=side_nav(request, auth),
                    body=main_panel(
                        *body,
                        theme=preference.theme,
                        color_mode=(
                            preference.color_mode if preference.color_mode != "system" else None
                        ),
                    ),
                    panel_id="main-content",
                    banner=banner,
                    brand=brand,
                    env_badge=cdao_identity,
                    account=(
                        Inline(
                            environment_badge,
                            color_mode_toggle(request, csrf_token=csrf_token),
                            account_summary(request, auth, csrf_token=csrf_token),
                            gap="sm",
                        )
                        if csrf_token
                        else None
                    ),
                    nav_footer=shell_nav_footer(),
                    chrome=AppShellChrome(
                        preset="editorial",
                        header_behavior="sticky",
                        nav_behavior="sticky",
                        nav_offset="header",
                        shell_gap="standard",
                        content_inset="wide",
                        banner_spacing="standard",
                        header_density="standard",
                        footer_density="compact",
                    ),
                    app_footer=AppFooter(
                        settings.app_name,
                        html.span("Demo environment · No remote systems are contacted"),
                    ),
                    content_width="wide",
                    mobile_collapse=False,
                ),
                layers=(
                    AmbientLayer(
                        pattern="mesh",
                        tone="accent",
                        intensity="soft",
                        scale="lg",
                        order=0,
                    ),
                    AmbientLayer(
                        pattern="grid",
                        tone="muted",
                        intensity="subtle",
                        placement="fixed-canvas",
                        scale="lg",
                        order=1,
                    ),
                ),
            ),
            max_width="xl",
        )
    else:
        header = Header(
            Container(
                Surface(
                    ActionGroup(
                        brand,
                        Inline(
                            cdao_identity,
                            Badge("Demo workspace", tone="warning"),
                            environment_badge,
                            gap="sm",
                        ),
                        align="between",
                        gap="sm",
                        collapse="never",
                    ),
                    appearance="raised",
                    density="comfortable",
                    padding="sm",
                    elevation="sm",
                    class_="hedron-surface--glass",
                ),
                max_width="xl",
            ),
        )
        content = Container(
            StyleScope(
                AmbientCanvas(
                    Stack(
                        banner,
                        header,
                        html.main(
                            Container(
                                *body,
                                query="inline-size",
                                name="auth",
                                max_width="xl",
                                padding="lg",
                            ),
                            id="main-content",
                            tabindex="-1",
                        ),
                        AppFooter(
                            settings.app_name,
                            html.span("Demo environment · No remote systems are contacted"),
                        ),
                        gap="md",
                    ),
                    layers=(
                        AmbientLayer(pattern="radial", tone="accent", intensity="soft", order=0),
                        AmbientLayer(
                            pattern="grid",
                            tone="muted",
                            intensity="subtle",
                            placement="fixed-canvas",
                            scale="lg",
                            order=1,
                        ),
                    ),
                ),
                theme=preference.theme,
                color_mode=preference.color_mode if preference.color_mode != "system" else None,
                variant="auth",
                design="data-mover",
                recipe_defaults={
                    "surface": "data-mover-auth-panel",
                    "content": "data-mover-supporting-copy",
                },
                presentation={
                    "PageHeader.title": "data-mover-auth-title",
                    "PageHeader.description": "data-mover-auth-copy",
                    "Text": "data-mover-auth-copy",
                },
            ),
            max_width="xl",
        )
    page_nodes: list[NodeLike] = [skip, indicator, toast_host(), dialog_host()]
    page_nodes.append(content)
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
        density="spacious",
        title_measure="narrow",
        description_measure="default",
        title_effect="display",
        description_effect="subtle",
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
                    max_width="full",
                    padding="lg",
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
                presentation={
                    "PageHeader.title": "data-mover-page-title",
                    "PageHeader.description": "data-mover-page-copy",
                    "Text": "data-mover-page-copy",
                },
            )
        ),
        scope="document",
        indicator="#global-request-indicator",
    )
