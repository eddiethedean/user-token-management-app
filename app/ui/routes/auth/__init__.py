"""Unauthenticated auth flows: login, registration, password reset, invitations."""

from __future__ import annotations

from hedron import Hedron

from app.ui.routes.auth.invitations import register_invitation_routes
from app.ui.routes.auth.login import register_login_routes
from app.ui.routes.auth.password import register_password_routes
from app.ui.routes.auth.registration import register_registration_routes


def register_auth_routes(app: Hedron) -> None:
    register_login_routes(app)
    register_registration_routes(app)
    register_password_routes(app)
    register_invitation_routes(app)
