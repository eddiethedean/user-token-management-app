"""Hedron Data Mover application factory."""

from __future__ import annotations

import logging
import re
import sys
import threading
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Literal, cast
from urllib.parse import urlencode

from fastapi import HTTPException, Request, Response, status
from fastapi.exception_handlers import (
    http_exception_handler,
)
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from hedron import Heading, RenderMode, html
from hedron.htmx import is_htmx_request
from hedron.responses import render_component_response
from hedron_core.request_budget import RequestBudget, reset_request_budget, set_request_budget
from hedron_posit import ConnectConfig, HedronPosit, PositConfig
from pydantic import BaseModel
from sqlalchemy import text
from starlette._utils import get_route_path

from app import APP_VERSION
from app.application.feedback import request_failure
from app.config import get_settings
from app.dependencies import clear_auth_cookies, set_auth_cookies, set_session_csrf_cookie
from app.infrastructure.catalog_factory import build_catalog_runner
from app.infrastructure.pipeline_authoring import build_pipeline_authoring_operation
from app.logging_config import (
    bind_reference,
    bind_request_id,
    clear_request_id,
    configure_logging,
    log_event,
    safe_exception_traceback,
)
from app.schema import assert_schema_current
from app.security.cookies import APPLICATION_COOKIE_NAMES
from app.services.auth import ensure_default_roles
from app.ui.design_system import surface_card
from app.ui.hedron_styles import desktop_default_styles
from app.ui.interactions import (
    ERROR_RESPONSE_POLICY,
    htmx_redirect,
    interaction_response,
    ok_fragment,
    request_feedback_oob,
)
from app.ui.layout import alert_box, app_shell
from app.ui.partials import request_error
from app.ui.routes import register_routes
from app.ui.security_policy import access_registry_security_policy
from app.ui.urls import htmx_redirect_path, redirect_path

configure_logging()
settings = get_settings()
log = logging.getLogger(__name__)
REQUEST_ID_PATTERN = re.compile(r"[A-Za-z0-9._:-]{1,64}\Z")


def _safe_request_id(value: str) -> str:
    return value if REQUEST_ID_PATTERN.fullmatch(value) else str(uuid.uuid4())


def _log_admission_failure(*, request_id: str, reference_id: str, method: str, path: str) -> None:
    bind_request_id(request_id)
    bind_reference(reference_id=reference_id)
    try:
        log_event(
            log,
            "http.request.completed",
            outcome="error",
            request_id=request_id,
            reference_id=reference_id,
            method=method,
            path=path,
            status=status.HTTP_503_SERVICE_UNAVAILABLE,
            duration_ms=0,
        )
    finally:
        clear_request_id()


async def _admission_response(scope: dict, receive, send) -> None:
    """Return a referenceable response when runtime admission is unavailable."""

    headers = dict(scope.get("headers", ()))
    supplied = headers.get(b"x-request-id", b"")
    request_id = _safe_request_id(supplied.decode("latin-1", errors="ignore"))
    reference_id = f"ref-{uuid.uuid4().hex[:16]}"
    path = get_route_path(scope)
    _log_admission_failure(
        request_id=request_id,
        reference_id=reference_id,
        method=str(scope.get("method", "")),
        path=path,
    )
    response = Response(
        content=f"Data Mover is temporarily unavailable. Reference: {reference_id}.",
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        media_type="text/plain",
        headers={
            "X-Request-ID": request_id,
            "X-Support-Reference": reference_id,
            "Cache-Control": "no-store",
        },
    )
    await response(scope, receive, send)


def _service_unavailable_response(request: Request) -> Response:
    """Return the same referenceable admission response from request middleware."""

    request_id = _safe_request_id(request.headers.get("x-request-id", ""))
    reference_id = f"ref-{uuid.uuid4().hex[:16]}"
    _log_admission_failure(
        request_id=request_id,
        reference_id=reference_id,
        method=request.method,
        path=request.url.path,
    )
    return Response(
        content=f"Data Mover is temporarily unavailable. Reference: {reference_id}.",
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        media_type="text/plain",
        headers={
            "X-Request-ID": request_id,
            "X-Support-Reference": reference_id,
            "Cache-Control": "no-store",
        },
    )


# Data Mover owns CSRF; disable Hedron's Starlette-session CSRF.
AR_SECURITY = access_registry_security_policy()


@asynccontextmanager
async def lifespan(app: HedronPosit) -> AsyncIterator[None]:
    from app.database import engine, stable_session_factory
    from app.infrastructure.runtime import ExecutionRuntime
    from app.services.pipeline_tasks import abort_legacy_startup, begin_legacy_startup

    cfg = get_settings()
    startup_token = await begin_legacy_startup()
    app.state.settings = cfg
    app.state.ready = False
    app.state.runtime_lifecycle = "starting"
    try:
        from app.connectors.registry import load_builtin_connectors

        app.state.connectors = load_builtin_connectors(demo=cfg.is_demo_mode, settings=cfg)
        app.state.connectors.validate_writer_settings(cfg)
        app.state.execution = ExecutionRuntime(
            cfg, stable_session_factory(), app.state.connectors.snapshot(cfg)
        )
        if cfg.app_env != "test":
            assert_schema_current()
            if cfg.data_mover_mode == "real" and cfg.pipeline_apply_internal_ca_fix:
                from app.connectors.tls import apply_internal_ca_fix

                apply_internal_ca_fix()
        with app.state.execution.sessions() as db:
            ensure_default_roles(db)
            if cfg.is_demo_mode:
                from app.services.catalogs import clear_demo_catalog_cache
                from app.services.demo import restore_demo_foundry_datasets

                clear_demo_catalog_cache(db)
                restore_demo_foundry_datasets(db, cfg, app.state.connectors)
        pipeline_stop_event = threading.Event()
        app.state.pipeline_stop_event = pipeline_stop_event
        app.state.execution.stop_event = pipeline_stop_event
        from app.services.pipeline_tasks import install_legacy_runtime

        install_legacy_runtime(app.state.execution, transition_token=startup_token)
        startup_token = None
        app.state.ready = True
        app.state.runtime_lifecycle = "accepting"
    except BaseException:
        app.state.ready = False
        if getattr(app.state, "execution", None) is not None:
            app.state.execution.begin_shutdown()
        app.state.runtime_lifecycle = "closed"
        if startup_token is not None:
            abort_legacy_startup(startup_token)
        raise
    from app.services.pipeline_tasks import release_legacy_runtime

    background_runtime = None
    try:
        if cfg.app_env != "test":
            from app.services.pipeline_tasks import start_background_runtime

            background_runtime = start_background_runtime(app.state.execution)
    except BaseException:
        app.state.ready = False
        app.state.execution.begin_shutdown()
        from app.services.pipeline_tasks import mark_legacy_runtime_closed

        mark_legacy_runtime_closed(app.state.execution)
        app.state.runtime_lifecycle = "closed"
        raise
    body_error: BaseException | None = None
    try:
        yield
    except BaseException as exc:
        body_error = exc
    finally:
        app.state.ready = False
        app.state.runtime_lifecycle = "draining"
        from app.services.pipeline_tasks import (
            drain_background_runtime,
            mark_legacy_runtime_closed,
            raise_runtime_shutdown_outcome,
        )

        drain_error: BaseException | None = None
        drain_outcome = None
        disposal_error: BaseException | None = None
        try:
            drain_outcome = await drain_background_runtime(background_runtime, app.state.execution)
        except BaseException as exc:
            drain_error = exc
        else:
            drain_error = drain_outcome.drain_error
        if drain_error is None:
            try:
                engine.dispose()
            except BaseException as exc:
                disposal_error = exc
            else:
                release_legacy_runtime(app.state.execution)
        if drain_error is not None or disposal_error is not None:
            mark_legacy_runtime_closed(app.state.execution)
        try:
            raise_runtime_shutdown_outcome(
                body_error=body_error,
                drain_error=drain_error,
                drain_outcome=drain_outcome,
                disposal_error=disposal_error,
            )
        finally:
            app.state.runtime_lifecycle = "closed"


app = HedronPosit(
    title=settings.app_name,
    version=APP_VERSION,
    docs_url=None if settings.is_production else "/docs",
    redoc_url=None,
    lifespan=lifespan,
    security=AR_SECURITY,
    session_secret=settings.csrf_secret,
    enable_sessions=False,
    htmx_extensions=("preload", "sse", "head-support"),
    explorer="off",
    theme="folio",
    default_styles=False,
    external_base_url=settings.public_base_url,
    posit=PositConfig(
        connect=ConnectConfig(
            trusted_peers=tuple(sorted({"127.0.0.1", "::1", *settings.trusted_proxy_ip_set})),
            owned_cookie_names=tuple(sorted(APPLICATION_COOKIE_NAMES)),
        )
    ),
)
app.state.settings = settings
app.state.ready = False
app.state.execution = None
app.state.runtime_lifecycle = "not_started"

static_directory = Path(__file__).resolve().parent / "static"


@app.get("/app-assets/hedron-desktop.css", include_in_schema=False)
def hedron_desktop_styles() -> Response:
    """Serve Hedron's native stylesheet, including responsive viewport rules."""

    return Response(
        desktop_default_styles(),
        media_type="text/css",
        headers={"Cache-Control": "public, max-age=3600"},
    )


app.mount("/assets", StaticFiles(directory=static_directory), name="assets")


def _request_execution(request: Request):
    """Resolve the runtime admitted for this request."""

    state = request.app.state
    request_execution = getattr(getattr(request, "state", None), "execution", None)
    lifecycle = getattr(state, "runtime_lifecycle", None)
    if request_execution is not None:
        return request_execution
    if lifecycle in {"accepting", "draining", "fixture"}:
        execution = getattr(state, "execution", None)
        if execution is not None:
            return execution
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE)
    raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE)


register_routes(
    app,
    catalog_runner_factory=lambda request: build_catalog_runner(_request_execution(request)),
    authoring_operation_factory=lambda request: build_pipeline_authoring_operation(
        _request_execution(request)
    ),
)


def create_app(settings_override=None) -> HedronPosit:
    """Compose an independent app instance for tests and embedded deployments.

    ``app.main:app`` remains the production entrypoint. A caller that needs a
    second instance supplies settings explicitly; it receives its own
    database runtime, connector registry, startup state, and dependency
    override rather than rebinding module globals.
    """

    if settings_override is None:
        return app
    from contextlib import asynccontextmanager

    from app.bootstrap import compose_application
    from app.config import get_settings as settings_dependency
    from app.infrastructure.runtime import ExecutionRuntime
    from app.services.auth import ensure_default_roles

    composition = compose_application(settings_override)

    @asynccontextmanager
    async def composed_lifespan(instance: HedronPosit):
        from app.services.pipeline_tasks import (
            drain_background_runtime,
            raise_runtime_shutdown_outcome,
        )

        instance.state.ready = False
        instance.state.runtime_lifecycle = "starting"
        runtime = composition.execution
        background_runtime = None
        body_error: BaseException | None = None
        try:
            if not runtime.is_accepting():
                if not runtime.lifetime.is_idle():
                    raise RuntimeError("The previous composed runtime did not finish draining.")
                runtime = ExecutionRuntime(
                    settings_override, composition.database.sessions, composition.connectors
                )
                composition.execution = runtime
            instance.state.execution = runtime
            instance.state.settings = settings_override
            instance.state.database = composition.database
            instance.state.connectors = composition.connectors
            composition.connectors.validate_writer_settings(settings_override)
            if settings_override.app_env != "test":
                assert_schema_current(composition.database.engine)
                if (
                    settings_override.data_mover_mode == "real"
                    and settings_override.pipeline_apply_internal_ca_fix
                ):
                    from app.connectors.tls import apply_internal_ca_fix

                    apply_internal_ca_fix()
            with composition.database.session() as db:
                ensure_default_roles(db)
                if settings_override.is_demo_mode:
                    from app.services.catalogs import clear_demo_catalog_cache

                    clear_demo_catalog_cache(db)
            instance.state.pipeline_stop_event = runtime.stop_event
            if settings_override.app_env != "test":
                from app.services.pipeline_tasks import start_background_runtime

                background_runtime = start_background_runtime(runtime)
            instance.state.ready = True
            instance.state.runtime_lifecycle = "accepting"
            yield
        except BaseException as exc:
            body_error = exc
        finally:
            instance.state.ready = False
            instance.state.runtime_lifecycle = "draining"
            drain_error: BaseException | None = None
            drain_outcome = None
            disposal_error: BaseException | None = None
            try:
                drain_outcome = await drain_background_runtime(background_runtime, runtime)
            except BaseException as exc:
                drain_error = exc
            else:
                drain_error = drain_outcome.drain_error
            if drain_error is None:
                try:
                    composition.close()
                except BaseException as exc:
                    disposal_error = exc
            try:
                raise_runtime_shutdown_outcome(
                    body_error=body_error,
                    drain_error=drain_error,
                    drain_outcome=drain_outcome,
                    disposal_error=disposal_error,
                )
            finally:
                instance.state.runtime_lifecycle = "closed"

    instance = HedronPosit(
        title=settings_override.app_name,
        version=APP_VERSION,
        docs_url=None if settings_override.is_production else "/docs",
        redoc_url=None,
        lifespan=composed_lifespan,
        security=AR_SECURITY,
        session_secret=settings_override.csrf_secret,
        enable_sessions=False,
        htmx_extensions=("preload", "sse", "head-support"),
        explorer="off",
        theme="folio",
        default_styles=False,
        external_base_url=settings_override.public_base_url,
        posit=PositConfig(
            connect=ConnectConfig(
                trusted_peers=tuple(
                    sorted({"127.0.0.1", "::1", *settings_override.trusted_proxy_ip_set})
                ),
                owned_cookie_names=tuple(sorted(APPLICATION_COOKIE_NAMES)),
            )
        ),
    )
    instance.state.composition = composition
    instance.state.settings = settings_override
    instance.state.ready = False
    instance.state.execution = None
    instance.state.runtime_lifecycle = "not_started"

    def composed_settings(request: Request):
        return getattr(request.state, "settings", settings_override)

    instance.dependency_overrides[settings_dependency] = composed_settings
    instance.mount("/assets", StaticFiles(directory=static_directory), name="assets")
    register_routes(
        instance,
        catalog_runner_factory=lambda request: build_catalog_runner(_request_execution(request)),
        authoring_operation_factory=lambda request: build_pipeline_authoring_operation(
            _request_execution(request)
        ),
    )
    instance.middleware("http")(security_and_session_middleware)
    instance.add_exception_handler(HTTPException, cast(Any, friendly_http_errors))
    instance.add_exception_handler(RequestValidationError, cast(Any, friendly_validation_errors))
    instance.add_exception_handler(Exception, cast(Any, friendly_unexpected_errors))

    @instance.get("/health", include_in_schema=False, response_model=HealthStatus)
    def composed_health() -> HealthStatus:
        return HealthStatus(status="ok")

    @instance.get("/ready", include_in_schema=False, response_model=ReadyStatus)
    def composed_ready(request: Request, response: Response) -> ReadyStatus:
        if not getattr(instance.state, "ready", False):
            response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
            return ReadyStatus(status="unavailable")
        execution = getattr(request.state, "execution", None)
        if execution is None:
            response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
            return ReadyStatus(status="unavailable")
        try:
            with execution.sessions() as db:
                db.execute(text("SELECT 1"))
        except Exception:
            response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
            return ReadyStatus(status="unavailable")
        return ReadyStatus(status="ready")

    @instance.get("/app-assets/hedron-desktop.css", include_in_schema=False)
    def composed_hedron_desktop_styles() -> Response:
        return Response(
            desktop_default_styles(),
            media_type="text/css",
            headers={"Cache-Control": "public, max-age=3600"},
        )

    instance.add_middleware(RuntimeOwnershipMiddleware)
    return instance


class RuntimeOwnershipMiddleware:
    """Capture one runtime and admission for the complete downstream ASGI call."""

    def __init__(self, application) -> None:
        self.application = application

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.application(scope, receive, send)
            return
        state = scope.setdefault("state", {})
        app = scope.get("app", self.application)
        app_state = getattr(app, "state", None)
        if app_state is None:
            await self.application(scope, receive, send)
            return
        lifecycle = getattr(app_state, "runtime_lifecycle", None)
        path = get_route_path(scope)
        is_liveness = path == "/health"
        if lifecycle not in {"accepting", "draining", "fixture"} and not is_liveness:
            await _admission_response(scope, receive, send)
            return
        execution = (
            getattr(app_state, "execution", None)
            if lifecycle in {"accepting", "draining", "fixture"}
            else None
        )
        if lifecycle in {"accepting", "draining"} and execution is None and not is_liveness:
            await _admission_response(scope, receive, send)
            return
        if execution is None:
            await self.application(scope, receive, send)
            return

        from app.connectors.registry import bind_registry, unbind_registry
        from app.database import bind_runtime, unbind_runtime
        from app.infrastructure.runtime import RuntimeAdmissionError

        runtime_token = bind_runtime(execution)
        state_settings = getattr(app_state, "settings", None)
        captured_settings = (
            state_settings if lifecycle == "fixture" else execution.execution_settings
        )
        registry_token = bind_registry(execution.connectors, captured_settings)
        state["execution"] = execution
        state["settings"] = captured_settings
        state["runtime_bound"] = True
        operation = None
        operation_entered = False
        try:
            if not is_liveness:
                operation = execution.operation_scope()
                try:
                    operation.__enter__()
                except RuntimeAdmissionError:
                    operation = None
                    await _admission_response(scope, receive, send)
                    return
                operation_entered = True
            await self.application(scope, receive, send)
        finally:
            if operation is not None and operation_entered:
                operation.__exit__(*sys.exc_info())
            unbind_runtime(runtime_token)
            unbind_registry(registry_token)


@app.middleware("http")
async def security_and_session_middleware(request: Request, call_next):
    state = request.app.state
    lifecycle = getattr(state, "runtime_lifecycle", None)
    owner_active = lifecycle in {"accepting", "draining", "fixture"}
    execution = getattr(request.state, "execution", None)
    if execution is None:
        execution = getattr(state, "execution", None) if owner_active else None
    route_path = get_route_path(request.scope)
    is_liveness = route_path == "/health"
    if lifecycle not in {"accepting", "draining", "fixture"} and not is_liveness:
        return _service_unavailable_response(request)
    if owner_active and execution is None and not is_liveness:
        return _service_unavailable_response(request)
    active_settings = getattr(request.state, "settings", None) or (
        getattr(state, "settings", None) if owner_active else None
    )
    if active_settings is None and not is_liveness:
        return _service_unavailable_response(request)
    active_settings = active_settings or get_settings()
    from app.connectors.registry import bind_registry, unbind_registry
    from app.database import bind_runtime, unbind_runtime

    owns_binding = not getattr(request.state, "runtime_bound", False)
    runtime_token = bind_runtime(execution) if owns_binding else None
    registry_token = (
        bind_registry(
            execution.connectors
            if execution is not None
            else (getattr(state, "connectors", None) if owner_active else None),
            active_settings,
        )
        if owns_binding
        else None
    )
    request.state.execution = execution
    budget_limits = getattr(AR_SECURITY, "request_budget_limits", None)
    budget_token = (
        set_request_budget(RequestBudget(limits=budget_limits)) if budget_limits else None
    )
    supplied_request_id = request.headers.get("x-request-id", "")
    request.state.request_id = (
        supplied_request_id
        if REQUEST_ID_PATTERN.fullmatch(supplied_request_id)
        else str(uuid.uuid4())
    )
    request.state.support_reference = f"ref-{uuid.uuid4().hex[:16]}"
    bind_request_id(request.state.request_id)
    bind_reference(reference_id=request.state.support_reference)
    started = time.perf_counter()
    request.state.request_started_at = started
    try:
        response = await call_next(request)
        rotated = getattr(request.state, "rotated_tokens", None)
        if not getattr(request.state, "auth_cookies_cleared", False):
            if rotated:
                set_auth_cookies(response, rotated, active_settings, request)
            else:
                csrf_session = getattr(request.state, "session_csrf_proof", None)
                if csrf_session is not None:
                    set_session_csrf_cookie(response, request, active_settings, csrf_session)
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
        if not route_path.startswith(
            ("/assets/", "/app-assets/", "/hedron-static/", "/hedron-assets/")
        ):
            response.headers["Cache-Control"] = "no-store"
        if active_settings.is_production:
            hsts = "max-age=31536000"
            if active_settings.hsts_include_subdomains:
                hsts += "; includeSubDomains"
            response.headers["Strict-Transport-Security"] = hsts
        duration_ms = int((time.perf_counter() - started) * 1000)
        log_event(
            log,
            "http.request.completed",
            outcome="success" if response.status_code < 400 else "error",
            reference_id=request.state.support_reference,
            method=request.method,
            path=request.url.path,
            status=response.status_code,
            duration_ms=duration_ms,
        )
        return response
    except Exception as exc:
        return await friendly_unexpected_errors(request, exc)
    finally:
        clear_request_id()
        if budget_token is not None:
            reset_request_budget(budget_token)
        if owns_binding:
            unbind_runtime(runtime_token)
            unbind_registry(registry_token)


app.add_middleware(RuntimeOwnershipMiddleware)


@app.exception_handler(HTTPException)
async def friendly_http_errors(request: Request, exc: HTTPException):
    active_settings = getattr(request.state, "settings", None) or getattr(
        request.app.state, "settings", settings
    )
    is_htmx = is_htmx_request(request)
    accepts_html = "text/html" in request.headers.get("accept", "")
    if exc.status_code == status.HTTP_401_UNAUTHORIZED and (accepts_html or is_htmx):
        next_path = get_route_path(request.scope)
        if request.url.query:
            next_path += f"?{request.url.query}"
        login_path = f"/login?{urlencode({'next': next_path})}"
        if is_htmx:
            response = await interaction_response(
                request,
                htmx_redirect(htmx_redirect_path(login_path)),
                authenticated=False,
            )
        else:
            response = RedirectResponse(
                redirect_path(request, login_path),
                status_code=status.HTTP_303_SEE_OTHER,
            )
        clear_auth_cookies(response, active_settings, request)
        return response
    reference_id = getattr(request.state, "support_reference", "")
    detail = exc.detail if isinstance(exc.detail, str) else "The request could not be completed."
    if exc.status_code == status.HTTP_429_TOO_MANY_REQUESTS:
        retry_after = max(1, int((exc.headers or {}).get("Retry-After", "60")))
        detail = f"Too many requests. Please wait {retry_after} seconds before trying again."
    outcome = None
    if exc.status_code >= 500:
        outcome = request_failure(reference_id=reference_id)
        detail = outcome.message
    if is_htmx:
        feedback_title = (
            "Too many requests"
            if exc.status_code == status.HTTP_429_TOO_MANY_REQUESTS
            else "Request could not be completed."
        )
        if outcome is not None:
            feedback_title = outcome.title
        return await interaction_response(
            request,
            ok_fragment(
                html.div(
                    request_error(detail),
                    html.p(f"Reference: {reference_id}", role="caption") if reference_id else None,
                ),
                oob=(
                    request_feedback_oob(
                        detail,
                        title=feedback_title,
                        reference_id=reference_id,
                    ),
                ),
                status_code=exc.status_code,
                headers=exc.headers,
                retarget="#hedron-toast",
                reswap="innerHTML",
                policy=ERROR_RESPONSE_POLICY,
            ),
            authenticated=bool(getattr(request.state, "hedron_authenticated", False)),
            allow_undeclared_targets=True,
        )
    if exc.status_code == status.HTTP_429_TOO_MANY_REQUESTS and accepts_html:
        page = app_shell(
            surface_card(
                Heading("Too many requests", level=1),
                html.p(
                    f"Please wait {(exc.headers or {}).get('Retry-After', '60')} seconds and try again."
                ),
                html.p(f"Reference: {reference_id}", role="caption") if reference_id else None,
                recipe="data-mover-auth-panel",
                class_="auth-card",
            ),
            request=request,
            settings=active_settings,
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
            surface_card(
                Heading("Request error", level=1),
                alert_box(detail),
                html.p(f"Reference: {reference_id}", role="caption") if reference_id else None,
                html.p(f"Status {exc.status_code}"),
                recipe="data-mover-auth-panel",
                class_="auth-card",
            ),
            request=request,
            settings=active_settings,
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
    active_settings = getattr(request.state, "settings", None) or getattr(
        request.app.state, "settings", settings
    )
    reference_id = getattr(request.state, "support_reference", "")
    if not is_htmx_request(request) and "text/html" not in request.headers.get("accept", ""):
        return JSONResponse(
            {"detail": "Check the submitted values and try again.", "reference_id": reference_id},
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            headers={"X-Support-Reference": reference_id} if reference_id else None,
        )
    message = "Check the submitted values and try again."
    if is_htmx_request(request):
        return await interaction_response(
            request,
            ok_fragment(
                html.div(
                    request_error(message),
                    html.p(f"Reference: {reference_id}", role="caption") if reference_id else None,
                ),
                oob=(
                    request_feedback_oob(
                        message,
                        title="Check the submitted values",
                        reference_id=reference_id,
                    ),
                ),
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                retarget="#hedron-toast",
                reswap="innerHTML",
                policy=ERROR_RESPONSE_POLICY,
            ),
            authenticated=bool(getattr(request.state, "hedron_authenticated", False)),
            allow_undeclared_targets=True,
        )
    page = app_shell(
        surface_card(
            Heading("Request error", level=1),
            alert_box(message),
            html.p(
                f"Reference: {reference_id}",
                role="caption",
            )
            if reference_id
            else None,
            recipe="data-mover-auth-panel",
            class_="auth-card",
        ),
        request=request,
        settings=active_settings,
        auth=None,
        page_title="Request error",
    )
    return render_component_response(
        page,
        request=request,
        mode=RenderMode.PAGE,
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
    )


@app.exception_handler(Exception)
async def friendly_unexpected_errors(request: Request, exc: Exception):
    """Return a safe support reference when an unexpected request error escapes a route."""

    reference_id = getattr(request.state, "support_reference", "") or f"ref-{uuid.uuid4().hex[:16]}"
    request_id = _safe_request_id(
        getattr(request.state, "request_id", "") or request.headers.get("x-request-id", "")
    )
    started = getattr(request.state, "request_started_at", None)
    duration_ms = int((time.perf_counter() - started) * 1000) if started else 0
    log_event(
        log,
        "http.request.failed",
        outcome="failed",
        request_id=request_id,
        reference_id=reference_id,
        method=request.method,
        path=request.url.path,
        status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        duration_ms=duration_ms,
        exception_type=type(exc).__name__,
        traceback=safe_exception_traceback((type(exc), exc, exc.__traceback__)),
    )
    outcome = request_failure(reference_id=reference_id)
    active_settings = getattr(request.state, "settings", None) or getattr(
        request.app.state, "settings", settings
    )
    accepts_html = "text/html" in request.headers.get("accept", "")
    if is_htmx_request(request):
        response = await interaction_response(
            request,
            ok_fragment(
                html.div(
                    request_error(outcome.message),
                    html.p(f"Reference: {reference_id}", role="caption"),
                ),
                oob=(
                    request_feedback_oob(
                        outcome.message,
                        title=outcome.title,
                        reference_id=reference_id,
                    ),
                ),
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                retarget="#hedron-toast",
                reswap="innerHTML",
                policy=ERROR_RESPONSE_POLICY,
            ),
            authenticated=bool(getattr(request.state, "hedron_authenticated", False)),
            allow_undeclared_targets=True,
        )
    elif accepts_html:
        response = render_component_response(
            app_shell(
                surface_card(
                    Heading(outcome.title, level=1),
                    alert_box(outcome.message),
                    html.p(f"Reference: {reference_id}", role="caption"),
                    recipe="data-mover-auth-panel",
                    class_="auth-card",
                ),
                request=request,
                settings=active_settings,
                auth=None,
                page_title=outcome.title,
            ),
            request=request,
            mode=RenderMode.PAGE,
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )
    else:
        response = JSONResponse(
            {
                "detail": outcome.message,
                "reference_id": reference_id,
            },
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )
    response.headers["X-Request-ID"] = request_id
    response.headers["X-Support-Reference"] = reference_id
    return response


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
    try:
        execution = getattr(request.state, "execution", None)
        if execution is None:
            response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
            return ReadyStatus(status="unavailable")
        with execution.sessions() as db:
            db.execute(text("SELECT 1"))
    except Exception:
        log.exception("Readiness database check failed")
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return ReadyStatus(status="unavailable")
    return ReadyStatus(status="ready")
