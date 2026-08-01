from pathlib import Path

from fastapi import Request
from fastapi.templating import Jinja2Templates

from app.config import get_settings
from app.dependencies import AuthContext
from app.routing import app_base_url

templates = Jinja2Templates(directory=Path(__file__).parent / "templates")


def template_context(
    request: Request,
    *,
    auth: AuthContext | None = None,
    page_title: str = "",
    **values,
) -> dict:
    settings = get_settings()
    return {
        "request": request,
        "app_name": settings.app_name,
        "page_title": page_title,
        "auth": auth,
        "current_user": auth.user if auth else None,
        "csrf_token": auth.session.csrf_token if auth else "",
        "app_base_url": app_base_url(request),
        **values,
    }
