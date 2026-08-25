"""Administrator routes: users, invitations, and audit."""

from __future__ import annotations

from typing import cast

from fastapi import HTTPException, Request, status
from fastapi.responses import RedirectResponse
from hedron import Hedron, InteractionResult, PageHeader, SplitView, html
from hedron.htmx import is_htmx_request
from hedron_posit import HedronPosit
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from starlette.responses import Response

from app.dependencies import AdminAuth, DbSession, RequireCsrf, SettingsDep
from app.models import Invitation, Role, User, UserStatus
from app.services.audit import AUDIT_PAGE_SIZE, list_audit_events, record_event
from app.services.auth import (
    approve_self_registration,
    create_invitation,
    deny_self_registration,
    lock_administrator_action,
    revoke_all_sessions,
    revoke_invitation,
)
from app.services.directory import (
    ADMIN_PAGE_SIZE,
    DirectoryUnavailableError,
    user_listing_path,
    user_listing_values,
    validate_directory_email,
)
from app.ui import partials as ui
from app.ui.design_system import surface_card
from app.ui.http import hx_target, is_filter_fragment, mutation_response, render_authenticated_view
from app.ui.interactions import (
    audit_match_count_oob,
    interaction_response,
    ok_fragment,
    user_match_count_oob,
)
from app.ui.layout import page_heading
from app.ui.params import (
    EmailForm,
    EventTypeQuery,
    InvitationIdPath,
    ListingPageForm,
    ListingQueryForm,
    ListingStatusForm,
    NoticeQuery,
    OutcomeQuery,
    PageQuery,
    RoleForm,
    SearchQuery,
    StatusFilterQuery,
    UserIdPath,
)
from app.ui.regions import (
    AUDIT_MATCH_COUNT,
    AUDIT_RESULTS,
    AUDIT_RESULTS_BODY,
    AUDIT_RESULTS_LAZY_BODY,
    INVITATION_PANEL,
    MAIN_PANEL,
    SIDE_NAV,
    TOAST_HOST,
    USER_DIRECTORY,
    USER_DIRECTORY_BODY,
    USER_MATCH_COUNT,
)
from app.ui.urls import mounted_path


def register_admin_routes(app: Hedron) -> None:
    @app.page(
        "/admin/users",
        fragment_regions=(
            MAIN_PANEL,
            SIDE_NAV,
            USER_DIRECTORY,
            USER_DIRECTORY_BODY,
            USER_MATCH_COUNT,
        ),
        include_in_schema=False,
    )
    async def users_page(
        request: Request,
        auth: AdminAuth,
        db: DbSession,
        settings: SettingsDep,
        q: SearchQuery = "",
        status: StatusFilterQuery = "",
        page: PageQuery = 1,
        notice: NoticeQuery = "",
    ) -> Response:
        request.state.hedron_authenticated = True
        user_notices = {
            "status-updated": "The account status was updated.",
            "registration-approved": "The registration was approved.",
            "registration-denied": "The registration was denied.",
        }
        invitation_notices = {
            "invitation-queued": "The invitation was queued for delivery.",
            "invitation-revoked": "The invitation was revoked.",
        }
        listing = user_listing_values(
            db, query=q, status_filter=status, page=page, user_success=user_notices.get(notice, "")
        )
        csrf = auth.session.csrf_token
        directory = ui.user_directory(
            request,
            listing["users"],
            csrf_token=csrf,
            query=listing["user_query"],
            status_filter=listing["status_filter"],
            page=listing["current_page"],
            page_count=listing["page_count"],
            total_users=listing["total_users"],
            page_size=ADMIN_PAGE_SIZE,
            success=listing["user_success"],
        )
        if is_filter_fragment(request, "#user-directory", "#user-directory-body"):
            target = hx_target(request)
            if target == "#user-directory-body":
                body = ui.user_table(
                    request,
                    listing["users"],
                    csrf_token=csrf,
                    query=listing["user_query"],
                    status_filter=listing["status_filter"],
                    page=listing["current_page"],
                    page_count=listing["page_count"],
                    total_users=listing["total_users"],
                    page_size=ADMIN_PAGE_SIZE,
                )
                return await interaction_response(
                    request,
                    ok_fragment(
                        body,
                        oob=(user_match_count_oob(listing["total_users"]),),
                        reswap="outerHTML",
                    ),
                )
            return await interaction_response(
                request,
                ok_fragment(
                    directory,
                    oob=(user_match_count_oob(listing["total_users"]),),
                ),
            )
        invitations = list(
            db.scalars(select(Invitation).order_by(Invitation.created_at.desc()).limit(25)).all()
        )
        roles = list(db.scalars(select(Role).order_by(Role.name)).all())
        body = [
            page_heading(
                "Administration",
                "Users and invitations",
                "Provision accounts, review status, and control application access.",
                ui.user_match_count(listing["total_users"]),
            ),
            SplitView(
                primary=surface_card(
                    PageHeader(
                        "Directory",
                        eyebrow="Identity management",
                        description="Search, review, and manage every application account.",
                        level=2,
                        density="compact",
                    ),
                    directory,
                ),
                secondary=html.aside(
                    surface_card(
                        ui.invitation_panel(
                            request,
                            invitations,
                            roles,
                            csrf_token=csrf,
                            success=invitation_notices.get(notice, ""),
                        )
                    )
                ),
                ratio="2:1",
                gap="lg",
                collapse="lg",
            ),
        ]
        return await render_authenticated_view(
            request,
            body=body,
            auth=auth,
            settings=settings,
            page_title="User administration",
            csrf_token=csrf,
            push_path="/admin/users",
        )

    @app.action(
        "/admin/invitations",
        fragment_regions=(INVITATION_PANEL, TOAST_HOST),
        include_in_schema=False,
    )
    async def invite_submit(
        request: Request,
        auth: AdminAuth,
        db: DbSession,
        settings: SettingsDep,
        _csrf: RequireCsrf,
        email: EmailForm,
        role: RoleForm = "user",
    ) -> Response:
        error = ""
        field_errors: dict[str, str] = {}
        response_status = status.HTTP_200_OK
        try:
            await validate_directory_email(email, settings)
            create_invitation(
                db, settings, email=email, role_name=role, inviter=auth.user, request=request
            )
        except DirectoryUnavailableError as exc:
            error = str(exc)
            field_errors["invite_email"] = error
            response_status = status.HTTP_503_SERVICE_UNAVAILABLE
        except ValueError as exc:
            error = str(exc)
            lowered = error.lower()
            if "role" in lowered:
                field_errors["invite_role"] = error
            else:
                field_errors["invite_email"] = error
            response_status = status.HTTP_400_BAD_REQUEST
        invitations = list(
            db.scalars(select(Invitation).order_by(Invitation.created_at.desc()).limit(25)).all()
        )
        roles = list(db.scalars(select(Role).order_by(Role.name)).all())
        if not is_htmx_request(request) and not error:
            return RedirectResponse(
                mounted_path(request, "/admin/users?notice=invitation-queued"),
                status_code=status.HTTP_303_SEE_OTHER,
            )
        if not is_htmx_request(request):
            raise HTTPException(status_code=response_status, detail=error or "Invitation error")
        panel = ui.invitation_panel(
            request,
            invitations,
            roles,
            csrf_token=auth.session.csrf_token,
            error=error,
            field_errors=field_errors,
            success="Invitation queued for delivery." if not error else "",
        )
        if error:
            return await interaction_response(
                request,
                ok_fragment(panel, status_code=response_status, toast=error, toast_tone="danger"),
            )
        return await interaction_response(
            request,
            ok_fragment(panel, toast="Invitation queued for delivery."),
        )

    async def _admin_user_mutation(
        request, user_id, auth, db, settings, *, action: str, q="", status_filter="", page=1
    ) -> Response:
        if not lock_administrator_action(db, auth.user):
            db.rollback()
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail="Administrator required"
            )
        user = db.get(User, user_id)
        if not user:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
        notice = "status-updated"
        toast = "The account status was updated."
        if action == "toggle":
            if user.id == auth.user.id:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="You cannot disable your own account",
                )
            if user.status == UserStatus.PENDING.value:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Use the registration approval action",
                )
            if user.status == UserStatus.DISABLED.value and (
                not user.email_verified_at
                or (settings.authentication_mode == "local_password" and not user.password_hash)
            ):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="This account cannot be enabled until its government email is verified",
                )
            user.status = (
                UserStatus.DISABLED.value
                if user.status == UserStatus.ACTIVE.value
                else UserStatus.ACTIVE.value
            )
            user.security_version += 1
            if user.is_active:
                user.failed_login_attempts = 0
                user.locked_until = None
            else:
                revoke_all_sessions(db, user)
            record_event(
                db,
                "admin.user.status_changed",
                request=request,
                actor=auth.user,
                target=user,
                detail={"status": user.status},
            )
            db.commit()
        elif action == "approve":
            try:
                approve_self_registration(
                    db, settings, user=user, administrator=auth.user, request=request
                )
            except ValueError as exc:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
                ) from exc
            notice = "registration-approved"
            toast = "The registration was approved."
        elif action == "deny":
            try:
                deny_self_registration(
                    db, settings, user=user, administrator=auth.user, request=request
                )
            except ValueError as exc:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
                ) from exc
            notice = "registration-denied"
            toast = "The registration was denied."
        listing = user_listing_values(db, query=q, status_filter=status_filter, page=page)
        return await mutation_response(
            request,
            redirect=user_listing_path(
                request, query=q, status_filter=status_filter, page=page, notice=notice
            ),
            fragment=ok_fragment(
                ui.user_table(
                    request,
                    listing["users"],
                    csrf_token=auth.session.csrf_token,
                    query=listing["user_query"],
                    status_filter=listing["status_filter"],
                    page=listing["current_page"],
                    page_count=listing["page_count"],
                    total_users=listing["total_users"],
                    page_size=ADMIN_PAGE_SIZE,
                ),
                oob=(user_match_count_oob(listing["total_users"]),),
                toast=toast,
                reswap="outerHTML",
            ),
        )

    @app.action(
        "/admin/users/{user_id}/toggle",
        fragment_regions=(USER_DIRECTORY_BODY, USER_MATCH_COUNT, TOAST_HOST),
        include_in_schema=False,
    )
    async def toggle_user(
        user_id: UserIdPath,
        request: Request,
        auth: AdminAuth,
        db: DbSession,
        settings: SettingsDep,
        _csrf: RequireCsrf,
        q: ListingQueryForm = "",
        status: ListingStatusForm = "",
        page: ListingPageForm = 1,
    ) -> Response:
        return await _admin_user_mutation(
            request,
            user_id,
            auth,
            db,
            settings,
            action="toggle",
            q=q,
            status_filter=status,
            page=page,
        )

    @app.action(
        "/admin/users/{user_id}/approve",
        fragment_regions=(USER_DIRECTORY_BODY, USER_MATCH_COUNT, TOAST_HOST),
        include_in_schema=False,
    )
    async def approve_user(
        user_id: UserIdPath,
        request: Request,
        auth: AdminAuth,
        db: DbSession,
        settings: SettingsDep,
        _csrf: RequireCsrf,
        q: ListingQueryForm = "",
        status: ListingStatusForm = "",
        page: ListingPageForm = 1,
    ) -> Response:
        return await _admin_user_mutation(
            request,
            user_id,
            auth,
            db,
            settings,
            action="approve",
            q=q,
            status_filter=status,
            page=page,
        )

    @app.action(
        "/admin/users/{user_id}/deny",
        fragment_regions=(USER_DIRECTORY_BODY, USER_MATCH_COUNT, TOAST_HOST),
        include_in_schema=False,
    )
    async def deny_user(
        user_id: UserIdPath,
        request: Request,
        auth: AdminAuth,
        db: DbSession,
        settings: SettingsDep,
        _csrf: RequireCsrf,
        q: ListingQueryForm = "",
        status: ListingStatusForm = "",
        page: ListingPageForm = 1,
    ) -> Response:
        return await _admin_user_mutation(
            request,
            user_id,
            auth,
            db,
            settings,
            action="deny",
            q=q,
            status_filter=status,
            page=page,
        )

    @app.action(
        "/admin/invitations/{invitation_id}/revoke",
        fragment_regions=(INVITATION_PANEL, TOAST_HOST),
        include_in_schema=False,
    )
    async def revoke_invitation_submit(
        invitation_id: InvitationIdPath,
        request: Request,
        auth: AdminAuth,
        db: DbSession,
        settings: SettingsDep,
        _csrf: RequireCsrf,
    ) -> Response:
        invitation = db.get(Invitation, invitation_id)
        if not invitation:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Invitation not found"
            )
        try:
            revoke_invitation(
                db,
                invitation=invitation,
                administrator=auth.user,
                request=request,
            )
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
        invitations = list(
            db.scalars(select(Invitation).order_by(Invitation.created_at.desc()).limit(25)).all()
        )
        roles = list(db.scalars(select(Role).order_by(Role.name)).all())
        return await mutation_response(
            request,
            redirect=mounted_path(request, "/admin/users?notice=invitation-revoked"),
            fragment=ok_fragment(
                ui.invitation_panel(
                    request,
                    invitations,
                    roles,
                    csrf_token=auth.session.csrf_token,
                    success="The invitation was revoked.",
                ),
                toast="The invitation was revoked.",
            ),
        )

    @app.page(
        "/admin/audit",
        fragment_regions=(
            MAIN_PANEL,
            SIDE_NAV,
            AUDIT_RESULTS,
            AUDIT_RESULTS_BODY,
            AUDIT_RESULTS_LAZY_BODY,
            AUDIT_MATCH_COUNT,
        ),
        include_in_schema=False,
    )
    async def audit_page(
        request: Request,
        auth: AdminAuth,
        db: DbSession,
        settings: SettingsDep,
        event_type: EventTypeQuery = "",
        outcome: OutcomeQuery = "",
        page: PageQuery = 1,
    ) -> Response:
        request.state.hedron_authenticated = True
        events, total, page, et, oc = list_audit_events(
            db, event_type=event_type, outcome=outcome, page=page
        )
        page_count = max(1, (total + AUDIT_PAGE_SIZE - 1) // AUDIT_PAGE_SIZE)
        results = ui.audit_results(
            request,
            events,
            event_type_filter=et,
            outcome_filter=oc,
            current_page=page,
            page_count=page_count,
            total_events=total,
            page_size=AUDIT_PAGE_SIZE,
        )
        if is_filter_fragment(request, "#audit-results-region", "#audit-results-body"):
            target = hx_target(request)
            if target == "#audit-results-body":
                body = ui.audit_results_body(
                    request,
                    events,
                    event_type_filter=et,
                    outcome_filter=oc,
                    current_page=page,
                    page_count=page_count,
                    total_events=total,
                    page_size=AUDIT_PAGE_SIZE,
                )
                return await interaction_response(
                    request,
                    ok_fragment(
                        body,
                        oob=(audit_match_count_oob(total),),
                        reswap="outerHTML",
                    ),
                )
            return await interaction_response(
                request,
                ok_fragment(results, oob=(audit_match_count_oob(total),)),
            )
        csrf = auth.session.csrf_token
        body = [
            page_heading(
                "Administration",
                "Audit activity",
                "Security-relevant actions recorded by the application.",
                ui.audit_match_count(total),
            ),
            surface_card(
                ui.audit_panel(
                    request,
                    events,
                    event_type_filter=et,
                    outcome_filter=oc,
                    current_page=page,
                    page_count=page_count,
                    total_events=total,
                    page_size=AUDIT_PAGE_SIZE,
                    lazy=not (et or oc or page > 1),
                ),
            ),
        ]
        return await render_authenticated_view(
            request,
            body=body,
            auth=auth,
            settings=settings,
            page_title="Audit activity",
            csrf_token=csrf,
            push_path="/admin/audit",
        )

    @app.component(
        "/admin/audit/results",
        fragment_regions=(AUDIT_RESULTS, AUDIT_RESULTS_LAZY_BODY, AUDIT_MATCH_COUNT),
        include_in_schema=False,
    )
    async def audit_results_fragment(
        request: Request,
        auth: AdminAuth,
        db: DbSession,
        event_type: EventTypeQuery = "",
        outcome: OutcomeQuery = "",
        page: PageQuery = 1,
    ) -> InteractionResult | RedirectResponse:
        request.state.hedron_authenticated = True
        if not is_htmx_request(request):
            return RedirectResponse(
                cast(HedronPosit, request.app).href("/admin/audit", request=request),
                status_code=status.HTTP_303_SEE_OTHER,
            )
        try:
            events, total, page, et, oc = list_audit_events(
                db, event_type=event_type, outcome=outcome, page=page
            )
            page_count = max(1, (total + AUDIT_PAGE_SIZE - 1) // AUDIT_PAGE_SIZE)
            return ok_fragment(
                ui.audit_results(
                    request,
                    events,
                    event_type_filter=et,
                    outcome_filter=oc,
                    current_page=page,
                    page_count=page_count,
                    total_events=total,
                    page_size=AUDIT_PAGE_SIZE,
                ),
                oob=(audit_match_count_oob(total),),
            )
        except SQLAlchemyError:
            return ok_fragment(
                ui.audit_results_error(
                    request,
                    event_type=event_type or "",
                    outcome=outcome or "",
                    page=page,
                ),
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
