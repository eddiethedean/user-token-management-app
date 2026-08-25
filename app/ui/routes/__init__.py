"""UI route registrars for Data Mover."""

from __future__ import annotations

from typing import cast

from fastapi import Request, status
from fastapi.responses import RedirectResponse
from hedron import Hedron
from hedron_posit import HedronPosit

from app.dependencies import Auth, OptionalAuth, RequireCsrf, SettingsDep
from app.ui.layout import (
    COLOR_MODE_COOKIE,
    THEME_CHOICES,
    THEME_COOKIE,
)
from app.ui.params import ColorModeForm, ThemeNameForm
from app.ui.routes.admin import register_admin_routes
from app.ui.routes.auth import register_auth_routes
from app.ui.routes.pipeline import register_pipeline_routes
from app.ui.routes.profile import register_profile_routes
from app.ui.routes.security import register_security_routes


def register_routes(app: Hedron) -> None:
    @app.page("/", include_in_schema=False)
    def home(request: Request, auth: OptionalAuth):
        return RedirectResponse(
            cast(HedronPosit, request.app).href("/pipeline" if auth else "/login", request=request),
            status_code=status.HTTP_303_SEE_OTHER,
        )

    @app.action("/preferences/theme", include_in_schema=False)
    def theme_preferences(
        request: Request,
        auth: Auth,
        settings: SettingsDep,
        _csrf: RequireCsrf,
        theme: ThemeNameForm = "",
        color_mode: ColorModeForm = "system",
    ):
        from hedron_core.builtins import resolve_theme_preference

        preference = resolve_theme_preference(
            theme,
            color_mode,
            allowed_themes=THEME_CHOICES,
        )
        path = "/" if settings.cookie_path == "auto" else settings.cookie_path
        common = {
            "secure": settings.cookie_secure,
            "httponly": True,
            "samesite": "lax",
            "path": path,
            "max_age": 31536000,
        }
        response = RedirectResponse(
            cast(HedronPosit, request.app).href("/pipeline", request=request),
            status_code=status.HTTP_303_SEE_OTHER,
        )
        response.set_cookie(THEME_COOKIE, preference.theme, **common)
        response.set_cookie(COLOR_MODE_COOKIE, preference.color_mode, **common)
        return response

    register_auth_routes(app)
    register_pipeline_routes(app)
    register_profile_routes(app)
    register_security_routes(app)
    register_admin_routes(app)
