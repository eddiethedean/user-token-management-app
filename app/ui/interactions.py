"""Hedron InteractionResult helpers for HTMX-first authenticated surfaces."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Literal

from fastapi import Request
from hedron import (
    FragmentRegion,
    InteractionPolicy,
    InteractionResult,
    OobUpdate,
    Toast,
)
from hedron import swap as build_swap
from hedron.responses import render_interaction
from hedron_core import NodeLike, RenderMode
from starlette.responses import Response

from app.ui import regions as region_defs
from app.ui.security_policy import access_registry_security_policy

ToastTone = Literal["info", "success", "warning", "danger"]

APP_REGIONS: tuple[FragmentRegion, ...] = (
    region_defs.MAIN_PANEL,
    region_defs.TOAST_HOST,
    region_defs.SIDE_NAV,
    region_defs.DIALOG_HOST,
    region_defs.ACCOUNT_SUMMARY,
    region_defs.PROFILE_IDENTITY,
    region_defs.PROFILE_FORM,
    region_defs.PASSWORD_FORM,
    region_defs.SESSION_LIST,
    region_defs.SESSION_COUNT,
    region_defs.SECRET_SLOT_ADVANA,
    region_defs.SECRET_SLOT_MSS,
    region_defs.SECRET_SLOT_POSTGRES,
    region_defs.SECRET_SLOT_MONGODB,
    region_defs.CONNECTION_STATUS_LIST,
    region_defs.CSV_INSPECTION,
    region_defs.INVITATION_PANEL,
    region_defs.USER_DIRECTORY,
    region_defs.USER_DIRECTORY_BODY,
    region_defs.USER_MATCH_COUNT,
    region_defs.AUDIT_RESULTS,
    region_defs.AUDIT_RESULTS_BODY,
    region_defs.AUDIT_MATCH_COUNT,
    region_defs.SECURITY_ACTIVITY,
    region_defs.GLOBAL_FEEDBACK,
)

APP_POLICY = InteractionPolicy(
    declared_regions=APP_REGIONS,
    allow_undeclared_targets=False,
    error_retarget="#global-feedback",
    error_reswap="innerHTML",
    indicator="#global-request-indicator",
)


def toast_oob(
    message: str,
    *,
    tone: ToastTone = "success",
    duration_ms: int = 4500,
) -> OobUpdate:
    from hedron import html

    return OobUpdate(
        content=html.div(
            Toast(message, tone=tone),
            class_="toast-item",
            data={"toast-ms": str(duration_ms)},
        ),
        element_id="toast-host",
        swap="beforeend",
    )


def user_match_count_oob(total: int) -> OobUpdate:
    from hedron import html

    return OobUpdate(
        content=html.span(f"{total} matching accounts", class_="verification-badge"),
        element_id="user-match-count",
        swap="outerHTML",
    )


def audit_match_count_oob(total: int) -> OobUpdate:
    from hedron import html

    return OobUpdate(
        content=html.span(f"{total} matching events", class_="verification-badge"),
        element_id="audit-match-count",
        swap="outerHTML",
    )


def session_count_oob(count: int) -> OobUpdate:
    from hedron import html

    return OobUpdate(
        content=html.span(str(count), class_="count-badge"),
        element_id="session-count",
        swap="outerHTML",
    )


def security_activity_oob(events) -> OobUpdate:
    from app.ui.partials.security import security_activity

    return OobUpdate(
        content=security_activity(events, oob=False),
        element_id="security-activity",
        swap="outerHTML",
    )


def connection_status_oob(request: Request, secret_slots, *, csrf_token: str) -> OobUpdate:
    from app.ui.partials.security import connection_status_list

    return OobUpdate(
        content=connection_status_list(
            request,
            secret_slots,
            csrf_token=csrf_token,
        ),
        element_id="connection-status-list",
        swap="outerHTML",
    )


def ok_fragment(
    content: NodeLike | None,
    *,
    oob: Sequence[OobUpdate] = (),
    push_url: str | bool | None = None,
    toast: str | None = None,
    toast_tone: ToastTone = "success",
    redirect: str | None = None,
    status_code: int = 200,
    region_id: str | None = None,
    swap: str | None = None,
    reswap: str | None = None,
) -> InteractionResult:
    """Build an InteractionResult via Hedron ``swap``, with AR toast-host + policy."""
    return build_swap(
        content,
        toast=toast_oob(toast, tone=toast_tone) if toast else None,
        oob=oob,
        push_url=push_url,
        redirect=redirect,
        status_code=status_code,
        region_id=region_id,
        swap=swap,
        reswap=reswap,
        policy=APP_POLICY,
        cache="no-store",
    )


def htmx_redirect(url: str) -> InteractionResult:
    """HX-Redirect InteractionResult with Data Mover policy defaults."""
    return build_swap(None, redirect=url, policy=APP_POLICY, cache="no-store")


async def interaction_response(
    request: Request,
    result: InteractionResult,
    *,
    authenticated: bool = True,
) -> Response:
    """Render an InteractionResult through Hedron's public response API."""
    request.state.hedron_authenticated = authenticated
    return await render_interaction(
        request,
        result,
        mode=RenderMode.FRAGMENT,
        kind="component",
        policy=access_registry_security_policy(),
        authenticated=authenticated,
        fragment_regions=APP_REGIONS,
    )
