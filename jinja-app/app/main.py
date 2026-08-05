import logging
import re
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlencode

from fastapi import FastAPI, HTTPException, Request
from fastapi.exception_handlers import (
    http_exception_handler,
    request_validation_exception_handler,
)
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy import text

from app.config import get_settings
from app.database import SessionLocal
from app.dependencies import clear_auth_cookies, set_auth_cookies
from app.routes.api import router as api_router
from app.routes.web import router as web_router
from app.routing import WorkbenchPathMiddleware, app_base_url, app_path, is_htmx_request
from app.schema import assert_schema_current
from app.services.auth import ensure_default_roles
from app.templating import template_context, templates

settings = get_settings()
log = logging.getLogger(__name__)
REQUEST_ID_PATTERN = re.compile(r"[A-Za-z0-9._:-]{1,64}\Z")


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    if settings.app_env != "test":
        assert_schema_current()
    with SessionLocal() as db:
        ensure_default_roles(db)
    yield


app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    docs_url=None if settings.is_production else "/docs",
    redoc_url=None,
    lifespan=lifespan,
)
app.add_middleware(WorkbenchPathMiddleware)


@app.middleware("http")
async def security_and_session_middleware(request: Request, call_next):
    supplied_request_id = request.headers.get("x-request-id", "")
    request.state.request_id = (
        supplied_request_id
        if REQUEST_ID_PATTERN.fullmatch(supplied_request_id)
        else str(uuid.uuid4())
    )
    response = await call_next(request)
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
    if not str(request.scope.get("path", "")).startswith("/assets/"):
        response.headers["Cache-Control"] = "no-store"
    if settings.is_production:
        hsts = "max-age=31536000"
        if settings.hsts_include_subdomains:
            hsts += "; includeSubDomains"
        response.headers["Strict-Transport-Security"] = hsts
    return response


@app.exception_handler(HTTPException)
async def friendly_http_errors(request: Request, exc: HTTPException):
    is_htmx = is_htmx_request(request)
    accepts_html = "text/html" in request.headers.get("accept", "")
    if exc.status_code == 401 and (accepts_html or is_htmx):
        # Keep the return target app-local so app_path() adds the proxy mount exactly once.
        # An outer middleware can still see the preserved Workbench prefix in scope["path"].
        next_path = str(request.scope.get("path") or "/")
        mount_path = app_base_url(request)
        if mount_path and next_path == mount_path:
            next_path = "/"
        elif mount_path and next_path.startswith(f"{mount_path}/"):
            next_path = next_path[len(mount_path) :]
        if request.url.query:
            next_path += f"?{request.url.query}"
        response = RedirectResponse(
            app_path(request, f"/login?{urlencode({'next': next_path})}"), status_code=303
        )
        clear_auth_cookies(response, settings, request)
        if is_htmx:
            response.headers["HX-Redirect"] = str(response.headers["location"])
        return response
    if is_htmx:
        detail = (
            exc.detail if isinstance(exc.detail, str) else "The request could not be completed."
        )
        response = templates.TemplateResponse(
            request=request,
            name="partials/request_error.html",
            status_code=exc.status_code,
            headers=exc.headers,
            context=template_context(request, error=detail),
        )
        response.headers["HX-Retarget"] = "#global-feedback"
        response.headers["HX-Reswap"] = "innerHTML"
        return response
    if exc.status_code == 429 and accepts_html:
        return templates.TemplateResponse(
            request=request,
            name="auth/rate_limited.html",
            status_code=429,
            headers=exc.headers,
            context=template_context(
                request,
                page_title="Too many requests",
                retry_after=(exc.headers or {}).get("Retry-After", "60"),
            ),
        )
    if accepts_html and 400 <= exc.status_code < 600:
        detail = (
            exc.detail if isinstance(exc.detail, str) else "The request could not be completed."
        )
        if exc.status_code == 403:
            detail = "You do not have permission to perform this action."
        elif exc.status_code == 404:
            detail = "The requested page or record was not found."
        return templates.TemplateResponse(
            request=request,
            name="auth/error.html",
            status_code=exc.status_code,
            headers=exc.headers,
            context=template_context(
                request,
                page_title="Request error",
                error=detail,
                status_code=exc.status_code,
            ),
        )
    return await http_exception_handler(request, exc)


@app.exception_handler(RequestValidationError)
async def friendly_validation_errors(request: Request, exc: RequestValidationError):
    is_htmx = is_htmx_request(request)
    if not is_htmx and "text/html" not in request.headers.get("accept", ""):
        return await request_validation_exception_handler(request, exc)
    if not is_htmx:
        return templates.TemplateResponse(
            request=request,
            name="auth/error.html",
            status_code=422,
            context=template_context(
                request,
                page_title="Request error",
                error="Check the submitted values and try again.",
                status_code=422,
            ),
        )
    response = templates.TemplateResponse(
        request=request,
        name="partials/request_error.html",
        status_code=422,
        context=template_context(
            request,
            error="Check the submitted values and try again.",
        ),
    )
    response.headers["HX-Retarget"] = "#global-feedback"
    response.headers["HX-Reswap"] = "innerHTML"
    return response


@app.exception_handler(Exception)
async def friendly_unexpected_errors(request: Request, exc: Exception):
    log.exception(
        "Unhandled request error",
        exc_info=exc,
        extra={"request_id": getattr(request.state, "request_id", "")},
    )
    message = "An unexpected error prevented the request from completing."
    if is_htmx_request(request):
        response = templates.TemplateResponse(
            request=request,
            name="partials/request_error.html",
            status_code=500,
            context=template_context(request, error=message),
        )
        response.headers["HX-Retarget"] = "#global-feedback"
        response.headers["HX-Reswap"] = "innerHTML"
        return response
    if "text/html" in request.headers.get("accept", ""):
        return templates.TemplateResponse(
            request=request,
            name="auth/error.html",
            status_code=500,
            context=template_context(
                request,
                page_title="Request error",
                error=message,
                status_code=500,
            ),
        )
    return JSONResponse({"detail": message}, status_code=500)


@app.get("/health", include_in_schema=False)
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/ready", include_in_schema=False)
def ready() -> JSONResponse:
    try:
        with SessionLocal() as db:
            db.execute(text("SELECT 1"))
    except Exception:
        log.exception("Readiness database check failed")
        return JSONResponse({"status": "unavailable"}, status_code=503)
    return JSONResponse({"status": "ready"})


app.include_router(api_router)
app.include_router(web_router)

static_directory = Path(__file__).parent / "static"
app.frontend("/assets", directory=static_directory, fallback=None)
