"""Authenticated profile routes."""

from __future__ import annotations

from fastapi import Depends, Form, Request
from fastapi.responses import RedirectResponse
from hedron import Hedron, html
from sqlalchemy.orm import Session
from starlette.responses import Response

from app.config import Settings, get_settings
from app.database import get_db
from app.dependencies import AuthContext, require_auth
from app.routing import app_path, is_htmx_request
from app.security.csrf import require_csrf
from app.services.accounts import ProfileValues, update_profile
from app.ui import partials as ui
from app.ui.http import render_authenticated_view
from app.ui.interactions import interaction_response, ok_fragment
from app.ui.layout import alert_box, page_heading


def register_profile_routes(app: Hedron) -> None:
    @app.get("/profile", include_in_schema=False)
    async def profile_page(
        request: Request,
        updated: bool = False,
        auth: AuthContext = Depends(require_auth),
        settings: Settings = Depends(get_settings),
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
                    ui.profile_form(auth, csrf_token=csrf),
                    class_="panel panel-main",
                ),
                ui.profile_identity(auth),
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

    @app.post("/profile", include_in_schema=False)
    async def profile_submit(
        request: Request,
        full_name: str = Form(default=""),
        organization: str = Form(default=""),
        job_title: str = Form(default=""),
        phone: str = Form(default=""),
        auth: AuthContext = Depends(require_auth),
        db: Session = Depends(get_db),
    ) -> Response:
        await require_csrf(request, auth.session.csrf_token)
        update_profile(
            db,
            user=auth.user,
            values=ProfileValues(
                full_name=full_name, organization=organization, job_title=job_title, phone=phone
            ),
            request=request,
        )
        if not is_htmx_request(request):
            return RedirectResponse(app_path(request, "/profile?updated=true"), status_code=303)
        return await interaction_response(
            request,
            ok_fragment(
                html.div(
                    *ui.profile_response(auth, csrf_token=auth.session.csrf_token, success="")
                ),
                toast="Your profile has been updated.",
            ),
        )
