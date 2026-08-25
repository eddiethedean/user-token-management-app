"""Authenticated profile routes."""

from __future__ import annotations

from fastapi import Request
from hedron import Badge, Hedron
from starlette.responses import Response

from app.dependencies import Auth, DbSession, RequireCsrf, SettingsDep
from app.services.accounts import ProfileValues, security_page_values, update_profile
from app.ui import partials as ui
from app.ui.http import mutation_response, render_authenticated_view
from app.ui.interactions import ok_fragment
from app.ui.layout import alert_box, page_heading
from app.ui.params import (
    FullNameForm,
    JobTitleForm,
    NoticeQuery,
    OrganizationForm,
    PhoneForm,
    UpdatedQuery,
)
from app.ui.regions import (
    ACCOUNT_SUMMARY,
    MAIN_PANEL,
    PROFILE_FORM,
    PROFILE_IDENTITY,
    SIDE_NAV,
    TOAST_HOST,
)
from app.ui.urls import mounted_redirect_path


def register_profile_routes(app: Hedron) -> None:
    @app.page(
        "/profile",
        fragment_regions=(MAIN_PANEL, SIDE_NAV),
        include_in_schema=False,
    )
    async def profile_page(
        request: Request,
        auth: Auth,
        db: DbSession,
        settings: SettingsDep,
        updated: UpdatedQuery = False,
        notice: NoticeQuery = "",
        tab: NoticeQuery = "",
    ) -> Response:
        request.state.hedron_authenticated = True
        csrf = auth.session.csrf_token
        verified_badge = Badge("Verified email", tone="success")
        values = security_page_values(db, auth.user, settings)
        notices = {"session-revoked": "The browser session was revoked."}
        body = [
            page_heading(
                "Account settings",
                "Account",
                "Manage your profile, password, active sessions, and recent account activity.",
                verified_badge,
            ),
            alert_box(
                "Your profile has been updated." if updated else notices.get(notice, ""),
                kind="success",
            ),
            ui.account_tabs(
                request,
                csrf_token=csrf,
                local_password=values["local_password"],
                sessions=values["sessions"],
                auth=auth,
                profile_content=ui.account_profile_panel(request, auth, csrf_token=csrf),
                active=tab,
            ),
        ]
        return await render_authenticated_view(
            request,
            body=body,
            auth=auth,
            settings=settings,
            page_title="Account",
            csrf_token=csrf,
            push_path="/profile",
        )

    @app.action(
        "/profile",
        fragment_regions=(PROFILE_FORM, PROFILE_IDENTITY, ACCOUNT_SUMMARY, TOAST_HOST),
        include_in_schema=False,
    )
    async def profile_submit(
        request: Request,
        auth: Auth,
        db: DbSession,
        settings: SettingsDep,
        _csrf: RequireCsrf,
        full_name: FullNameForm = "",
        organization: OrganizationForm = "",
        job_title: JobTitleForm = "",
        phone: PhoneForm = "",
    ) -> Response:
        update_profile(
            db,
            user=auth.user,
            values=ProfileValues(
                full_name=full_name, organization=organization, job_title=job_title, phone=phone
            ),
            request=request,
        )
        form, oob = ui.profile_response(
            request, auth, csrf_token=auth.session.csrf_token, success=""
        )
        return await mutation_response(
            request,
            redirect=mounted_redirect_path(request, "/profile?updated=true", settings),
            fragment=ok_fragment(
                form,
                oob=oob,
                toast="Your profile has been updated.",
            ),
        )
