"""UI route registrars for Data Mover."""

from __future__ import annotations

from fastapi import Request, status
from fastapi.responses import RedirectResponse, Response
from hedron import Hedron, HedronRouter
from hedron.htmx import is_htmx_request

from app.dependencies import Auth, DbSession, OptionalAuth, RequireCsrf, SettingsDep
from app.security.cookies import set_application_cookie
from app.ui.layout import (
    THEME_CHOICES,
    THEME_COOKIE,
    UI_PREFERENCE_MAX_AGE,
    set_color_mode_cookie,
)
from app.ui.params import DarkModeForm, NextForm, ThemeNameForm
from app.ui.routes.admin import register_admin_routes
from app.ui.routes.auth import register_auth_routes
from app.ui.routes.pipeline import register_pipeline_routes
from app.ui.routes.profile import register_profile_routes
from app.ui.routes.security import register_security_routes
from app.ui.urls import redirect_path


def register_routes(app: Hedron) -> None:
    fragment_router = HedronRouter(provenance="access-registry fragment views")

    @app.page("/", include_in_schema=False)
    def home(request: Request, auth: OptionalAuth):
        return RedirectResponse(
            redirect_path(request, "/pipeline" if auth else "/login"),
            status_code=status.HTTP_303_SEE_OTHER,
        )

    @app.action("/preferences/theme", include_in_schema=False)
    def theme_preferences(
        request: Request,
        auth: Auth,
        db: DbSession,
        settings: SettingsDep,
        _csrf: RequireCsrf,
        theme: ThemeNameForm = "",
        dark_mode: DarkModeForm = False,
        next: NextForm = "/pipeline",
    ):
        from hedron_core.builtins import resolve_theme_preference

        from app.ui.http import safe_next

        preference = resolve_theme_preference(
            theme,
            "dark" if dark_mode else "light",
            allowed_themes=THEME_CHOICES,
        )
        auth.user.preferred_color_mode = preference.color_mode
        db.commit()
        response = (
            Response(status_code=status.HTTP_204_NO_CONTENT)
            if is_htmx_request(request)
            else RedirectResponse(
                redirect_path(request, safe_next(next)),
                status_code=status.HTTP_303_SEE_OTHER,
            )
        )
        set_application_cookie(
            response,
            request,
            settings,
            THEME_COOKIE,
            preference.theme,
            max_age=UI_PREFERENCE_MAX_AGE,
        )
        set_color_mode_cookie(
            response,
            request=request,
            settings=settings,
            color_mode=preference.color_mode,
        )
        return response

    register_auth_routes(app)
    register_pipeline_routes(app)
    register_profile_routes(app)
    register_security_routes(app, fragment_router)
    register_admin_routes(app, fragment_router)
    app.include_router(fragment_router)
