"""UI route registrars for Data Mover."""

from __future__ import annotations

from fastapi import Request, status
from fastapi.responses import RedirectResponse
from hedron import Hedron

from app.dependencies import OptionalAuth
from app.routing import redirect_path
from app.ui.routes.admin import register_admin_routes
from app.ui.routes.auth import register_auth_routes
from app.ui.routes.pipeline import register_pipeline_routes
from app.ui.routes.profile import register_profile_routes
from app.ui.routes.security import register_security_routes


def register_routes(app: Hedron) -> None:
    @app.page("/", include_in_schema=False)
    def home(request: Request, auth: OptionalAuth):
        return RedirectResponse(
            redirect_path(request, "/pipeline" if auth else "/login"),
            status_code=status.HTTP_303_SEE_OTHER,
        )

    register_auth_routes(app)
    register_pipeline_routes(app)
    register_profile_routes(app)
    register_security_routes(app)
    register_admin_routes(app)
