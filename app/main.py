from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
import uuid

from fastapi import FastAPI, HTTPException, Request
from fastapi.exception_handlers import http_exception_handler
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from app.config import get_settings
from app.database import SessionLocal, create_schema
from app.dependencies import clear_auth_cookies, set_auth_cookies
from app.routes.api import router as api_router
from app.routes.web import router as web_router
from app.services.auth import ensure_default_roles


settings = get_settings()


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    create_schema()
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


@app.middleware("http")
async def security_and_session_middleware(request: Request, call_next):
    request.state.request_id = request.headers.get("x-request-id", str(uuid.uuid4()))[:64]
    response = await call_next(request)
    rotated = getattr(request.state, "rotated_tokens", None)
    if rotated:
        set_auth_cookies(response, rotated, settings)
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
    if settings.is_production:
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return response


@app.exception_handler(HTTPException)
async def friendly_http_errors(request: Request, exc: HTTPException):
    accepts_html = "text/html" in request.headers.get("accept", "")
    if exc.status_code == 401 and accepts_html:
        next_path = request.url.path
        if request.url.query:
            next_path += f"?{request.url.query}"
        response = RedirectResponse(f"/login?next={next_path}", status_code=303)
        clear_auth_cookies(response, settings)
        if request.headers.get("HX-Request") == "true":
            response.headers["HX-Redirect"] = str(response.headers["location"])
        return response
    if exc.status_code == 403 and accepts_html:
        return JSONResponse(
            {"detail": "You do not have permission to perform this action."}, status_code=403
        )
    return await http_exception_handler(request, exc)


@app.get("/health", include_in_schema=False)
def health() -> dict[str, str]:
    return {"status": "ok"}


app.include_router(api_router)
app.include_router(web_router)

static_directory = Path(__file__).parent / "static"
if hasattr(app, "frontend"):
    app.frontend("/assets", directory=static_directory, fallback=None)  # type: ignore[attr-defined]
else:
    app.mount("/assets", StaticFiles(directory=static_directory), name="assets")

