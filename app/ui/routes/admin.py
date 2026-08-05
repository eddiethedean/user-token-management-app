"""Administrator routes: users, invitations, and audit."""

from __future__ import annotations

from fastapi import Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from hedron import Hedron, html
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.responses import Response

from app.config import Settings, get_settings
from app.database import get_db
from app.dependencies import AuthContext, require_admin
from app.models import Invitation, Role, User, UserStatus
from app.routing import app_path, is_htmx_request
from app.security.csrf import require_csrf
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
from app.ui.http import hx_target, is_filter_fragment, render_authenticated_view
from app.ui.interactions import (
    audit_match_count_oob,
    interaction_response,
    ok_fragment,
    user_match_count_oob,
)
from app.ui.layout import page_heading


def register_admin_routes(app: Hedron) -> None:
    @app.get("/admin/users", include_in_schema=False)
    async def users_page(
        request: Request,
        q: str = "",
        status: str = "",
        page: int = 1,
        notice: str = "",
        auth: AuthContext = Depends(require_admin),
        db: Session = Depends(get_db),
        settings: Settings = Depends(get_settings),
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
            html.div(
                html.section(
                    html.div(
                        html.h2("Directory"),
                        html.p("All application-managed identities."),
                        class_="panel-heading",
                    ),
                    directory,
                    class_="panel panel-main",
                ),
                html.aside(
                    ui.invitation_panel(
                        invitations,
                        roles,
                        csrf_token=csrf,
                        success=invitation_notices.get(notice, ""),
                    )
                ),
                class_="admin-layout",
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

    @app.post("/admin/invitations", include_in_schema=False)
    async def invite_submit(
        request: Request,
        email: str = Form(max_length=320),
        role: str = Form(default="user", max_length=64),
        auth: AuthContext = Depends(require_admin),
        db: Session = Depends(get_db),
        settings: Settings = Depends(get_settings),
    ) -> Response:
        await require_csrf(request, auth.session.csrf_token)
        error = ""
        field_errors: dict[str, str] = {}
        response_status = 200
        try:
            await validate_directory_email(email, settings)
            create_invitation(
                db, settings, email=email, role_name=role, inviter=auth.user, request=request
            )
        except DirectoryUnavailableError as exc:
            error = str(exc)
            field_errors["invite_email"] = error
            response_status = 503
        except ValueError as exc:
            error = str(exc)
            lowered = error.lower()
            if "role" in lowered:
                field_errors["invite_role"] = error
            else:
                field_errors["invite_email"] = error
            response_status = 400
        invitations = list(
            db.scalars(select(Invitation).order_by(Invitation.created_at.desc()).limit(25)).all()
        )
        roles = list(db.scalars(select(Role).order_by(Role.name)).all())
        if not is_htmx_request(request) and not error:
            return RedirectResponse(
                app_path(request, "/admin/users?notice=invitation-queued"), status_code=303
            )
        if not is_htmx_request(request):
            raise HTTPException(status_code=response_status, detail=error or "Invitation error")
        panel = ui.invitation_panel(
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
        request, user_id, auth, db, settings, *, action: str, q="", status="", page=1
    ) -> Response:
        await require_csrf(request, auth.session.csrf_token)
        if not lock_administrator_action(db, auth.user):
            db.rollback()
            raise HTTPException(status_code=403, detail="Administrator required")
        user = db.get(User, user_id)
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        notice = "status-updated"
        toast = "The account status was updated."
        if action == "toggle":
            if user.id == auth.user.id:
                raise HTTPException(status_code=400, detail="You cannot disable your own account")
            if user.status == UserStatus.PENDING.value:
                raise HTTPException(status_code=400, detail="Use the registration approval action")
            if user.status == UserStatus.DISABLED.value and (
                not user.email_verified_at
                or (settings.authentication_mode == "local_password" and not user.password_hash)
            ):
                raise HTTPException(
                    status_code=400,
                    detail="This account cannot be enabled until its government email is verified",
                )
            user.status = (
                UserStatus.DISABLED.value
                if user.status == UserStatus.ACTIVE.value
                else UserStatus.ACTIVE.value
            )
            user.security_version += 1
            if not user.is_active:
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
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            notice = "registration-approved"
            toast = "The registration was approved."
        elif action == "deny":
            try:
                deny_self_registration(
                    db, settings, user=user, administrator=auth.user, request=request
                )
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            notice = "registration-denied"
            toast = "The registration was denied."
        if not is_htmx_request(request):
            return RedirectResponse(
                user_listing_path(request, query=q, status_filter=status, page=page, notice=notice),
                status_code=303,
            )
        listing = user_listing_values(db, query=q, status_filter=status, page=page)
        return await interaction_response(
            request,
            ok_fragment(
                ui.user_table(
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

    @app.post("/admin/users/{user_id}/toggle", include_in_schema=False)
    async def toggle_user(
        user_id: str,
        request: Request,
        q: str = Form(default=""),
        status: str = Form(default=""),
        page: int = Form(default=1),
        auth: AuthContext = Depends(require_admin),
        db: Session = Depends(get_db),
        settings: Settings = Depends(get_settings),
    ) -> Response:
        return await _admin_user_mutation(
            request, user_id, auth, db, settings, action="toggle", q=q, status=status, page=page
        )

    @app.post("/admin/users/{user_id}/approve", include_in_schema=False)
    async def approve_user(
        user_id: str,
        request: Request,
        q: str = Form(default=""),
        status: str = Form(default=""),
        page: int = Form(default=1),
        auth: AuthContext = Depends(require_admin),
        db: Session = Depends(get_db),
        settings: Settings = Depends(get_settings),
    ) -> Response:
        return await _admin_user_mutation(
            request, user_id, auth, db, settings, action="approve", q=q, status=status, page=page
        )

    @app.post("/admin/users/{user_id}/deny", include_in_schema=False)
    async def deny_user(
        user_id: str,
        request: Request,
        q: str = Form(default=""),
        status: str = Form(default=""),
        page: int = Form(default=1),
        auth: AuthContext = Depends(require_admin),
        db: Session = Depends(get_db),
        settings: Settings = Depends(get_settings),
    ) -> Response:
        return await _admin_user_mutation(
            request, user_id, auth, db, settings, action="deny", q=q, status=status, page=page
        )

    @app.post("/admin/invitations/{invitation_id}/revoke", include_in_schema=False)
    async def revoke_invitation_submit(
        invitation_id: str,
        request: Request,
        auth: AuthContext = Depends(require_admin),
        db: Session = Depends(get_db),
        settings: Settings = Depends(get_settings),
    ) -> Response:
        await require_csrf(request, auth.session.csrf_token)
        invitation = db.get(Invitation, invitation_id)
        if not invitation:
            raise HTTPException(status_code=404, detail="Invitation not found")
        try:
            revoke_invitation(
                db,
                invitation=invitation,
                administrator=auth.user,
                request=request,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if not is_htmx_request(request):
            return RedirectResponse(
                app_path(request, "/admin/users?notice=invitation-revoked"), status_code=303
            )
        invitations = list(
            db.scalars(select(Invitation).order_by(Invitation.created_at.desc()).limit(25)).all()
        )
        roles = list(db.scalars(select(Role).order_by(Role.name)).all())
        return await interaction_response(
            request,
            ok_fragment(
                ui.invitation_panel(
                    invitations,
                    roles,
                    csrf_token=auth.session.csrf_token,
                    success="The invitation was revoked.",
                ),
                toast="The invitation was revoked.",
            ),
        )

    @app.get("/admin/audit", include_in_schema=False)
    async def audit_page(
        request: Request,
        event_type: str = "",
        outcome: str = "",
        page: int = 1,
        auth: AuthContext = Depends(require_admin),
        db: Session = Depends(get_db),
        settings: Settings = Depends(get_settings),
    ) -> Response:
        request.state.hedron_authenticated = True
        events, total, page, et, oc = list_audit_events(
            db, event_type=event_type, outcome=outcome, page=page
        )
        page_count = max(1, (total + AUDIT_PAGE_SIZE - 1) // AUDIT_PAGE_SIZE)
        results = ui.audit_results(
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
            html.section(
                ui.audit_results_lazy(event_type=et, outcome=oc, page=page)
                if not (et or oc or page > 1)
                else results,
                class_="panel",
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
