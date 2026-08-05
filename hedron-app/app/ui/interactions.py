"""Hedron InteractionResult helpers for HTMX-first authenticated surfaces."""

from __future__ import annotations

from collections.abc import Sequence

from fastapi import Request
from hedron import FragmentRegion, InteractionPolicy, InteractionResult, OobUpdate, Toast
from hedron.routing.route import HedronRoute
from hedron.security.policy import SecurityPolicy
from hedron_core import RenderMode

from app.ui import regions as region_defs

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
    region_defs.SECRET_SLOT_ADE,
    region_defs.SECRET_SLOT_MSS,
    region_defs.INVITATION_PANEL,
    region_defs.USER_DIRECTORY,
    region_defs.USER_DIRECTORY_BODY,
    region_defs.USER_TABLE,
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

_RENDER_POLICY = SecurityPolicy(
    csrf_enabled=False,
    security_headers=False,
    explorer_enabled=False,
    private_authenticated_cache=True,
    content_security_policy=None,
)


def toast_oob(message: str, *, tone: str = "success", duration_ms: int = 4500) -> OobUpdate:
    from hedron import html

    return OobUpdate(
        content=html.div(
            Toast(message, tone=tone),  # type: ignore[arg-type]
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


def ok_fragment(
    content: object,
    *,
    oob: Sequence[OobUpdate] = (),
    push_url: str | bool | None = None,
    toast: str | None = None,
    toast_tone: str = "success",
    redirect: str | None = None,
    status_code: int = 200,
    region_id: str | None = None,
    swap: str | None = None,
    reswap: str | None = None,
) -> InteractionResult:
    extras = list(oob)
    if toast:
        extras.append(toast_oob(toast, tone=toast_tone))
    return InteractionResult(
        content=content,
        status_code=status_code,
        oob=tuple(extras),
        push_url=push_url,
        redirect=redirect,
        policy=APP_POLICY,
        region_id=region_id,
        swap=swap,
        reswap=reswap,
        cache="no-store",
    )


def form_error(content: object, *, status_code: int = 400) -> InteractionResult:
    return InteractionResult(
        content=content,
        status_code=status_code,
        retarget="#global-feedback",
        reswap="innerHTML",
        policy=APP_POLICY,
        cache="no-store",
    )


async def interaction_response(
    request: Request,
    result: InteractionResult,
    *,
    authenticated: bool = True,
) -> object:
    """Render an InteractionResult through Hedron's converter."""
    request.state.hedron_authenticated = authenticated
    return await HedronRoute._convert_interaction_result(
        request,
        result,
        mode=RenderMode.FRAGMENT,
        kind="component",
        policy=_RENDER_POLICY,
        authenticated=authenticated,
        fragment_regions=APP_REGIONS,
    )
