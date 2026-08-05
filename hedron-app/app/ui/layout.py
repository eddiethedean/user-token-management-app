"""Layout shell matching Access Registry base template."""

from __future__ import annotations

from hedron import Fragment, OobUpdate, Page, Text, html
from hedron_core.security import SafeUrl, UrlPurpose

from app.config import Settings
from app.dependencies import AuthContext
from app.routing import app_path
from app.ui.urls import form_action, hx_attrs, page_href

INDICATOR = "#global-request-indicator"

HTMX_CONFIG = (
    '{"includeIndicatorStyles":false,"allowEval":false,"allowScriptTags":false,'
    '"historyCacheSize":10,"refreshOnHistoryMiss":true,'
    '"responseHandling":[{"code":"204","swap":false},{"code":"[23]..","swap":true},'
    '{"code":"[45]..","swap":true,"error":true}]}'
)


def document_head(*, page_title: str, app_name: str) -> object:
    title = f"{page_title} · {app_name}" if page_title else app_name
    return html.div(
        html.meta(name="color-scheme", content="dark"),
        html.meta(name="theme-color", content="#080d1a"),
        html.meta(name="htmx-config", content=HTMX_CONFIG),
        html.title(title),
        html.link(
            rel="stylesheet",
            href=SafeUrl.parse("/assets/theme.css", purpose=UrlPurpose.ASSET),
        ),
    )


def alert_box(message: str, *, kind: str = "error") -> object:
    if not message:
        return html.div()
    role = "status" if kind == "success" else "alert"
    css = f"alert alert-{'success' if kind == 'success' else 'error'}"
    return html.div(Text(message), class_=css, role=role)


def account_summary(auth: AuthContext, *, csrf_token: str, oob: bool = False) -> object:
    user = auth.user
    attrs: dict = {"id": "account-summary", "class_": "account-summary"}
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
            html.input(type="hidden", name="csrf_token", value=csrf_token),
            html.button("Sign out", class_="button button-quiet button-small", type="submit"),
            action=form_action("logout"),
            method="post",
        ),
        **attrs,
    )


def side_nav_children(request, auth: AuthContext) -> list[object]:
    path = str(request.scope.get("path") or "/")

    def link(href: str, number: str, label: str) -> object:
        normalized = path.rstrip("/") or "/"
        active = "active" if normalized == href or normalized.endswith(href) else ""
        return html.a(
            html.span(number, aria={"hidden": "true"}),
            f" {label}",
            class_=f"nav-link {active}".strip(),
            href=page_href(href),
            **hx_attrs(
                method="get",
                path=href.lstrip("/"),
                target="#main-panel",
                swap="outerHTML",
                push_url=True,
                indicator=INDICATOR,
            ),
        )

    children: list[object] = [
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


def side_nav(request, auth: AuthContext, *, oob: bool = False) -> object:
    attrs: dict = {
        "id": "side-nav",
        "class_": "side-nav",
        "aria": {"label": "Account navigation"},
    }
    if oob:
        attrs["hx-swap-oob"] = "outerHTML"
    return html.nav(*side_nav_children(request, auth), **attrs)


def side_nav_oob(request, auth: AuthContext) -> OobUpdate:
    """Replace side-nav contents after in-shell navigation (preserves outer nav element)."""
    return OobUpdate(
        content=Fragment(*side_nav_children(request, auth)),
        element_id="side-nav",
        swap="innerHTML",
    )


def toast_host(*, oob: bool = False) -> object:
    attrs: dict = {"id": "toast-host", "class_": "toast-host", "aria": {"live": "polite"}}
    if oob:
        attrs["hx-swap-oob"] = "outerHTML"
    return html.div(**attrs)


def dialog_host() -> object:
    return html.div(id="dialog-host", class_="dialog-host")


def app_shell(
    *body: object,
    request,
    settings: Settings,
    auth: AuthContext | None,
    page_title: str,
    csrf_token: str = "",
) -> Page:
    _ = app_path
    banner = html.div(
        html.div(
            html.span("▦", class_="flag-mark", aria={"hidden": "true"}),
            html.span("Official use system · Activity may be monitored"),
            class_="page-width banner-inner",
        ),
        class_="official-banner",
    )
    header_children: list[object] = [
        html.a(
            html.span("AR", class_="brand-mark", aria={"hidden": "true"}),
            html.span(
                html.strong(settings.app_name),
                html.small("Identity and access services"),
            ),
            html.span("Protected workspace", class_="brand-chip"),
            class_="brand",
            href=page_href("/"),
            aria={"label": f"{settings.app_name} home"},
        )
    ]
    if auth and csrf_token:
        header_children.append(account_summary(auth, csrf_token=csrf_token))
    header = html.header(
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
    skip = html.a("Skip to main content", class_="skip-link", href=page_href("/#main-content"))
    footer = html.footer(
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
            html.main(
                html.div(*body, id="main-panel", class_="main-panel"),
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
        head=document_head(page_title=page_title, app_name=settings.app_name),
    )


def page_heading(eyebrow: str, title: str, lead: str, *extra: object) -> object:
    return html.div(
        html.div(
            html.p(eyebrow, class_="eyebrow"),
            html.h1(title),
            html.p(lead),
        ),
        *extra,
        class_="page-heading",
    )


def main_panel(*body: object) -> object:
    """Authenticated main panel root used for in-shell HTMX navigation swaps."""
    return html.div(*body, id="main-panel", class_="main-panel")
