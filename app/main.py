"""Hedron Data Mover application factory."""

from __future__ import annotations

import logging
import re
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal
from urllib.parse import urlencode

from fastapi import HTTPException, Request, Response, status
from fastapi.exception_handlers import (
    http_exception_handler,
    request_validation_exception_handler,
)
from fastapi.exceptions import RequestValidationError
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from hedron import Card, Heading, Hedron, html
from hedron.responses import render_component_response
from hedron_core import RenderMode
from pydantic import BaseModel
from sqlalchemy import text

from app.config import get_settings
from app.dependencies import clear_auth_cookies, set_auth_cookies
from app.logging_config import bind_request_id, clear_request_id, configure_logging
from app.routing import (
    WorkbenchPathMiddleware,
    application_path,
    is_htmx_request,
    redirect_path,
)
from app.schema import assert_schema_current
from app.services.auth import ensure_default_roles
from app.ui.layout import alert_box, app_shell
from app.ui.partials import request_error
from app.ui.routes import register_routes
from app.ui.security_policy import access_registry_security_policy

configure_logging()
settings = get_settings()
log = logging.getLogger(__name__)
REQUEST_ID_PATTERN = re.compile(r"[A-Za-z0-9._:-]{1,64}\Z")

# Data Mover owns CSRF; disable Hedron's Starlette-session CSRF.
AR_SECURITY = access_registry_security_policy()


@asynccontextmanager
async def lifespan(app: Hedron) -> AsyncIterator[None]:
    from app.database import SessionLocal, engine

    cfg = get_settings()
    app.state.settings = cfg
    app.state.ready = False
    from app.connectors.registry import load_builtin_connectors

    load_builtin_connectors(demo=cfg.is_demo_mode)
    if cfg.app_env != "test":
        assert_schema_current()
    with SessionLocal() as db:
        ensure_default_roles(db)
    app.state.ready = True
    try:
        yield
    finally:
        app.state.ready = False
        engine.dispose()


app = Hedron(
    title=settings.app_name,
    version="0.1.0",
    docs_url=None if settings.is_production else "/docs",
    redoc_url=None,
    lifespan=lifespan,
    security=AR_SECURITY,
    session_secret=settings.csrf_secret,
    enable_sessions=False,
    htmx_extensions=("preload", "sse", "head-support"),
    explorer="off",
    default_styles=False,
)
app.add_middleware(WorkbenchPathMiddleware)

static_directory = Path(__file__).resolve().parent / "static"
app.mount("/assets", StaticFiles(directory=static_directory), name="assets")

register_routes(app)


@app.middleware("http")
async def security_and_session_middleware(request: Request, call_next):
    supplied_request_id = request.headers.get("x-request-id", "")
    request.state.request_id = (
        supplied_request_id
        if REQUEST_ID_PATTERN.fullmatch(supplied_request_id)
        else str(uuid.uuid4())
    )
    bind_request_id(request.state.request_id)
    started = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        duration_ms = int((time.perf_counter() - started) * 1000)
        log.exception(
            "request failed method=%s path=%s duration_ms=%s",
            request.method,
            request.url.path,
            duration_ms,
        )
        clear_request_id()
        raise
    rotated = getattr(request.state, "rotated_tokens", None)
    if rotated:
        set_auth_cookies(response, rotated, settings, request)
    response.headers["X-Request-ID"] = request.state.request_id
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
        "font-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; "
        "form-action 'self'"
    )
    if not application_path(request).startswith(("/assets/", "/hedron-static/", "/hedron-assets/")):
        response.headers["Cache-Control"] = "no-store"
    if settings.is_production:
        hsts = "max-age=31536000"
        if settings.hsts_include_subdomains:
            hsts += "; includeSubDomains"
        response.headers["Strict-Transport-Security"] = hsts
    duration_ms = int((time.perf_counter() - started) * 1000)
    log.info(
        "method=%s path=%s status=%s duration_ms=%s",
        request.method,
        request.url.path,
        response.status_code,
        duration_ms,
    )
    clear_request_id()
    return response


@app.exception_handler(HTTPException)
async def friendly_http_errors(request: Request, exc: HTTPException):
    is_htmx = is_htmx_request(request)
    accepts_html = "text/html" in request.headers.get("accept", "")
    if exc.status_code == status.HTTP_401_UNAUTHORIZED and (accepts_html or is_htmx):
        next_path = application_path(request)
        if request.url.query:
            next_path += f"?{request.url.query}"
        response = RedirectResponse(
            redirect_path(request, f"/login?{urlencode({'next': next_path})}"),
            status_code=status.HTTP_303_SEE_OTHER,
        )
        clear_auth_cookies(response, settings, request)
        if is_htmx:
            response.headers["HX-Redirect"] = str(response.headers["location"])
        return response
    detail = exc.detail if isinstance(exc.detail, str) else "The request could not be completed."
    if is_htmx:
        response = render_component_response(
            request_error(detail),
            request=request,
            mode=RenderMode.FRAGMENT,
            status_code=exc.status_code,
            extra_headers=exc.headers,
            allow_undeclared_targets=True,
        )
        response.headers["HX-Retarget"] = "#global-feedback"
        response.headers["HX-Reswap"] = "innerHTML"
        return response
    if exc.status_code == status.HTTP_429_TOO_MANY_REQUESTS and accepts_html:
        page = app_shell(
            Card(
                Heading("Too many requests", level=1),
                html.p(
                    f"Please wait {(exc.headers or {}).get('Retry-After', '60')} seconds and try again."
                ),
                class_="panel auth-card",
            ),
            request=request,
            settings=settings,
            auth=None,
            page_title="Too many requests",
        )
        return render_component_response(
            page,
            request=request,
            mode=RenderMode.PAGE,
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            extra_headers=exc.headers,
        )
    if accepts_html and 400 <= exc.status_code < 600:
        if exc.status_code == status.HTTP_403_FORBIDDEN:
            detail = "You do not have permission to perform this action."
        elif exc.status_code == status.HTTP_404_NOT_FOUND:
            detail = "The requested page or record was not found."
        page = app_shell(
            Card(
                Heading("Request error", level=1),
                alert_box(detail),
                html.p(f"Status {exc.status_code}"),
                class_="panel auth-card",
            ),
            request=request,
            settings=settings,
            auth=None,
            page_title="Request error",
        )
        return render_component_response(
            page,
            request=request,
            mode=RenderMode.PAGE,
            status_code=exc.status_code,
            extra_headers=exc.headers,
        )
    return await http_exception_handler(request, exc)


@app.exception_handler(RequestValidationError)
async def friendly_validation_errors(request: Request, exc: RequestValidationError):
    if not is_htmx_request(request) and "text/html" not in request.headers.get("accept", ""):
        return await request_validation_exception_handler(request, exc)
    message = "Check the submitted values and try again."
    if is_htmx_request(request):
        response = render_component_response(
            request_error(message),
            request=request,
            mode=RenderMode.FRAGMENT,
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            allow_undeclared_targets=True,
        )
        response.headers["HX-Retarget"] = "#global-feedback"
        response.headers["HX-Reswap"] = "innerHTML"
        return response
    page = app_shell(
        Card(Heading("Request error", level=1), alert_box(message), class_="panel auth-card"),
        request=request,
        settings=settings,
        auth=None,
        page_title="Request error",
    )
    return render_component_response(
        page,
        request=request,
        mode=RenderMode.PAGE,
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
    )


class HealthStatus(BaseModel):
    status: Literal["ok"]


class ReadyStatus(BaseModel):
    status: Literal["ready", "unavailable"]


@app.get("/health", include_in_schema=False, response_model=HealthStatus)
def health() -> HealthStatus:
    return HealthStatus(status="ok")


@app.get("/ready", include_in_schema=False, response_model=ReadyStatus)
def ready(request: Request, response: Response) -> ReadyStatus:
    if not getattr(request.app.state, "ready", False):
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return ReadyStatus(status="unavailable")
    from app.database import SessionLocal

    try:
        with SessionLocal() as db:
            db.execute(text("SELECT 1"))
    except Exception:
        log.exception("Readiness database check failed")
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return ReadyStatus(status="unavailable")
    return ReadyStatus(status="ready")
