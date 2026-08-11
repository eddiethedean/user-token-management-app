"""Authenticated profile routes."""

from __future__ import annotations

from fastapi import Request
from hedron import Hedron, html
from starlette.responses import Response

from app.dependencies import Auth, DbSession, RequireCsrf, SettingsDep
from app.routing import redirect_path
from app.services.accounts import ProfileValues, update_profile
from app.ui import partials as ui
from app.ui.http import mutation_response, render_authenticated_view
from app.ui.interactions import ok_fragment
from app.ui.layout import alert_box, page_heading
from app.ui.params import (
    FullNameForm,
    JobTitleForm,
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


def register_profile_routes(app: Hedron) -> None:
    @app.page(
        "/profile",
        fragment_regions=(MAIN_PANEL, SIDE_NAV),
        include_in_schema=False,
    )
    async def profile_page(
        request: Request,
        auth: Auth,
        settings: SettingsDep,
        updated: UpdatedQuery = False,
    ) -> Response:
        request.state.hedron_authenticated = True
        csrf = auth.session.csrf_token
        verified_badge = html.span(
            html.span("✓", aria={"hidden": "true"}),
            " Verified email",
            class_="verification-badge",
        )
        body = [
            page_heading(
                "Account profile",
                "Your information",
                "Keep your contact and organizational details current.",
                verified_badge,
            ),
            alert_box("Your profile has been updated." if updated else "", kind="success"),
            html.div(
                html.section(
                    html.div(
                        html.div(
                            html.h2("Profile details"),
                            html.p("Information shown to application administrators."),
                        ),
                        class_="panel-heading",
                    ),
                    ui.profile_form(request, auth, csrf_token=csrf),
                    class_="panel panel-main",
                ),
                ui.profile_identity(request, auth),
                class_="content-grid profile-grid",
            ),
        ]
        return await render_authenticated_view(
            request,
            body=body,
            auth=auth,
            settings=settings,
            page_title="Your profile",
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
            redirect=redirect_path(request, "/profile?updated=true"),
            fragment=ok_fragment(
                form,
                oob=oob,
                toast="Your profile has been updated.",
            ),
        )
