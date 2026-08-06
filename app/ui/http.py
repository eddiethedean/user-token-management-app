"""Shared HTMX detection and page/fragment render helpers for UI routes."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from fastapi import Request, status
from fastapi.responses import RedirectResponse
from hedron import Card, InteractionResult, Page, html
from hedron.responses import render_component_response
from hedron_core import NodeLike, RenderMode
from starlette.responses import Response

from app.config import Settings
from app.dependencies import AuthContext
from app.routing import app_path, is_htmx_request
from app.ui.interactions import interaction_response, ok_fragment
from app.ui.layout import app_shell, main_panel, side_nav_oob
from app.ui.urls import mounted_path


def hx_target(request: Request) -> str:
    raw = (request.headers.get("HX-Target") or "").strip()
    if raw and not raw.startswith(("#", ".", "[")) and " " not in raw:
        return f"#{raw}"
    return raw


def is_history_restore(request: Request) -> bool:
    return request.headers.get("HX-History-Restore-Request", "").casefold() == "true"


def is_main_panel_nav(request: Request) -> bool:
    """In-shell nav fragment swap (not a history-restore full refresh)."""
    return (
        is_htmx_request(request)
        and hx_target(request) == "#main-panel"
        and not is_history_restore(request)
    )


def is_filter_fragment(request: Request, *targets: str) -> bool:
    """True for in-page filter/body swaps; false for nav or history restore."""
    if not is_htmx_request(request) or is_history_restore(request):
        return False
    return hx_target(request) in targets


def safe_next(value: str) -> str:
    is_local_path = (
        value.startswith("/")
        and not value.startswith("//")
        and "\\" not in value
        and not any(ord(character) < 32 for character in value)
    )
    return value if is_local_path else "/profile"


def render_page(
    page: Page,
    *,
    request: Request | None = None,
    status_code: int = 200,
    headers: Mapping[str, str] | None = None,
    authenticated: bool = False,
) -> Response:
    response = render_component_response(
        page,
        request=request,
        mode=RenderMode.PAGE,
        status_code=status_code,
        extra_headers=dict(headers) if headers is not None else None,
        authenticated=authenticated,
    )
    # Hedron forbids <script> nodes in the tree; inject AR progressive-enhancement JS here.
    html_text = bytes(response.body).decode(response.charset or "utf-8")
    script_src = (
        mounted_path(request, "/assets/app.js") if request is not None else "/assets/app.js"
    )
    app_script = f'<script src="{script_src}" defer></script>'
    if "app.js" not in html_text:
        if "</body>" in html_text:
            html_text = html_text.replace("</body>", f"{app_script}</body>", 1)
        else:
            html_text += app_script
        response.body = html_text.encode(response.charset or "utf-8")
        response.headers["content-length"] = str(len(response.body))
    return response


def render_fragment(
    *nodes: NodeLike, request: Request | None = None, status_code: int = 200
) -> Response:
    content = html.div(*nodes) if len(nodes) != 1 else nodes[0]
    return render_component_response(
        content,
        request=request,
        mode=RenderMode.FRAGMENT,
        status_code=status_code,
    )


def auth_card(*children: NodeLike) -> Card:
    return Card(*children, class_="panel auth-card")


async def render_authenticated_view(
    request: Request,
    *,
    body: Sequence[NodeLike],
    auth: AuthContext,
    settings: Settings,
    page_title: str,
    csrf_token: str,
    push_path: str,
    headers: Mapping[str, str] | None = None,
) -> Response:
    """Serve main-panel nav fragment or full authenticated document."""
    if is_main_panel_nav(request):
        return await interaction_response(
            request,
            ok_fragment(
                main_panel(*body),
                oob=(side_nav_oob(request, auth),),
                push_url=app_path(request, push_path),
            ),
        )
    return render_page(
        app_shell(
            *body,
            request=request,
            settings=settings,
            auth=auth,
            page_title=page_title,
            csrf_token=csrf_token,
        ),
        request=request,
        authenticated=True,
        headers=headers,
    )


async def mutation_response(
    request: Request,
    *,
    redirect: str,
    fragment: InteractionResult,
) -> Response:
    """Authenticated POST result: full-page 303 redirect, or HTMX InteractionResult."""
    if not is_htmx_request(request):
        return RedirectResponse(redirect, status_code=status.HTTP_303_SEE_OTHER)
    return await interaction_response(request, fragment)
