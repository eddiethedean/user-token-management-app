"""Authenticated connection routes and account security actions."""

from __future__ import annotations

import logging

from fastapi import BackgroundTasks, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from hedron import Hedron, HedronRouter, InteractionResult
from hedron.htmx import is_htmx_request
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from starlette.responses import Response

from app.application.feedback import connection_failure
from app.application.ports import RequestMetadata
from app.connectors.redaction import redact_text
from app.connectors.registry import connection_tester_for
from app.database import current_session_factory
from app.dependencies import Auth, DbSession, RequireCsrf, SettingsDep, clear_auth_cookies
from app.logging_config import log_event
from app.models import RefreshSession, User, UserSecret
from app.security.passwords import PasswordPolicyError
from app.services.accounts import (
    CurrentPasswordError,
    security_page_values,
)
from app.services.accounts import (
    change_password as change_account_password,
)
from app.services.auth import revoke_session
from app.services.mailer import schedule_email_delivery
from app.services.secrets import (
    ConnectionNotConfiguredError,
    SecretStorageError,
    delete_user_secret,
    require_secret_provider,
    store_user_credentials_if_changed,
    test_user_connection,
)
from app.ui import partials as ui
from app.ui.http import hx_target, mutation_response, render_authenticated_view, render_page
from app.ui.interactions import (
    connection_status_oob,
    htmx_redirect,
    interaction_response,
    ok_fragment,
    request_feedback_oob,
    security_activity_oob,
    session_count_oob,
)
from app.ui.layout import alert_box, app_shell, page_heading
from app.ui.params import (
    NoticeQuery,
    PasswordForm,
    SecretProviderPath,
    SessionIdPath,
)
from app.ui.partials.feedback import feedback_panel
from app.ui.presenters.feedback import connection_outcome
from app.ui.regions import (
    CONNECTION_STATUS_LIST,
    MAIN_PANEL,
    PASSWORD_FORM,
    SECRET_SLOT_MCSCOP,
    SECRET_SLOT_MSS,
    SECRET_SLOT_POSTGRES,
    SECURITY_ACTIVITY,
    SECURITY_ACTIVITY_LAZY_BODY,
    SESSION_COUNT,
    SESSION_LIST,
    SIDE_NAV,
    TOAST_HOST,
)
from app.ui.routes.pipeline_context import run_owned_sync
from app.ui.urls import htmx_redirect_path, redirect_path

log = logging.getLogger(__name__)


def _credential_field_errors(specification, message: str) -> dict[str, str]:
    """Attach safe validation messages to the field most likely responsible."""

    safe_message = redact_text(message)[:240]
    folded_message = safe_message.casefold()
    errors: dict[str, str] = {}
    for field in specification.fields:
        aliases = {
            field.name.casefold(),
            field.name.replace("_", " ").casefold(),
            field.label.casefold(),
        }
        if any(alias and alias in folded_message for alias in aliases):
            errors[field.name] = safe_message
    if not errors:
        for field_name in ("token", "endpoint", "host", "port", "connect_timeout"):
            if field_name in folded_message and any(
                field.name == field_name for field in specification.fields
            ):
                errors[field_name] = safe_message
                break
    return errors


def register_security_routes(app: Hedron, fragment_router: HedronRouter) -> None:
    @app.page(
        "/security",
        fragment_regions=(MAIN_PANEL, SIDE_NAV),
        include_in_schema=False,
    )
    async def security_page(
        request: Request,
        auth: Auth,
        db: DbSession,
        settings: SettingsDep,
        notice: NoticeQuery = "",
    ) -> Response:
        request.state.hedron_authenticated = True
        notices = {
            "secret-saved": (
                "The connection credentials were saved. Changed credentials are tested "
                "automatically; the latest result appears under Connection status."
            ),
            "secret-deleted": "The connection credentials were deleted.",
        }
        values = security_page_values(
            db, auth.user, settings, security_success=notices.get(notice, "")
        )
        csrf = auth.session.csrf_token
        page_feedback = getattr(request.state, "security_page_feedback", None)
        body = [
            page_heading(
                "Workspace settings",
                "Connections",
                "Manage the encrypted credentials Data Mover uses to reach your remote sources.",
            ),
            alert_box(values["security_success"], kind="success"),
            feedback_panel(page_feedback, label="Connection feedback") if page_feedback else None,
            ui.security_tabs(
                request,
                csrf_token=csrf,
                secret_slots=values["secret_slots"],
                secret_feedback=getattr(request.state, "secret_feedback", None),
            ),
        ]
        return await render_authenticated_view(
            request,
            body=body,
            auth=auth,
            settings=settings,
            page_title="Connections",
            csrf_token=csrf,
            push_path="/security",
            headers={"Cache-Control": "no-store"},
            status_code=getattr(request.state, "security_response_status", status.HTTP_200_OK),
        )

    @fragment_router.view(
        "/profile/activity",
        fragment_regions=(SECURITY_ACTIVITY, SECURITY_ACTIVITY_LAZY_BODY),
        include_in_schema=False,
    )
    async def security_activity_fragment(
        request: Request,
        auth: Auth,
        db: DbSession,
        settings: SettingsDep,
    ) -> InteractionResult | RedirectResponse:
        request.state.hedron_authenticated = True
        if not is_htmx_request(request):
            return RedirectResponse(
                redirect_path(request, "/profile?tab=Activity"),
                status_code=status.HTTP_303_SEE_OTHER,
            )
        try:
            values = security_page_values(db, auth.user, settings)
            region_id = (
                None
                if hx_target(request) == SECURITY_ACTIVITY_LAZY_BODY.selector
                else SECURITY_ACTIVITY.id
            )
            return ok_fragment(ui.security_activity(request, values["events"], region_id=region_id))
        except SQLAlchemyError:
            region_id = (
                None
                if hx_target(request) == SECURITY_ACTIVITY_LAZY_BODY.selector
                else SECURITY_ACTIVITY.id
            )
            return ok_fragment(
                ui.security_activity_error(request, region_id=region_id),
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

    @app.action(
        "/profile/password",
        fragment_regions=(PASSWORD_FORM,),
        include_in_schema=False,
    )
    async def password_change_submit(
        request: Request,
        auth: Auth,
        db: DbSession,
        settings: SettingsDep,
        background_tasks: BackgroundTasks,
        _csrf: RequireCsrf,
        current_password: PasswordForm,
        new_password: PasswordForm,
        new_password_confirm: PasswordForm,
    ) -> Response:
        if settings.authentication_mode != "local_password":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail="Password changes are disabled"
            )
        error = ""
        field_errors: dict[str, str] = {}
        if new_password != new_password_confirm:
            error = "New passwords do not match."
            field_errors["new_password_confirm"] = error
        else:
            try:
                change_account_password(
                    db,
                    settings,
                    user=auth.user,
                    current_password=current_password,
                    new_password=new_password,
                    request=request,
                )
                schedule_email_delivery(background_tasks, settings)
            except CurrentPasswordError as exc:
                error = str(exc)
                field_errors["current_password"] = error
            except PasswordPolicyError as exc:
                error = str(exc)
                field_errors["new_password"] = error
        if not error:
            if is_htmx_request(request):
                response = await interaction_response(
                    request,
                    htmx_redirect(htmx_redirect_path("/login?password=changed")),
                )
                clear_auth_cookies(response, settings, request)
                return response
            response = RedirectResponse(
                redirect_path(request, "/login?password=changed"),
                status_code=status.HTTP_303_SEE_OTHER,
            )
            clear_auth_cookies(response, settings, request)
            return response
        if is_htmx_request(request):
            return await interaction_response(
                request,
                ok_fragment(
                    ui.password_form(
                        request,
                        csrf_token=auth.session.csrf_token,
                        error=error,
                        field_errors=field_errors,
                    ),
                    status_code=status.HTTP_400_BAD_REQUEST,
                ),
            )
        values = security_page_values(db, auth.user, settings)
        csrf = auth.session.csrf_token
        return render_page(
            app_shell(
                page_heading(
                    "Account settings",
                    "Account",
                    "Manage your profile, password, active sessions, and recent account activity.",
                ),
                ui.account_tabs(
                    request,
                    csrf_token=csrf,
                    local_password=values["local_password"],
                    sessions=values["sessions"],
                    auth=auth,
                    profile_content=ui.account_profile_panel(request, auth, csrf_token=csrf),
                    active="Password",
                    password_error=error,
                    password_field_errors=field_errors,
                ),
                request=request,
                settings=settings,
                auth=auth,
                page_title="Account",
                csrf_token=csrf,
            ),
            status_code=status.HTTP_400_BAD_REQUEST,
            headers={"Cache-Control": "no-store"},
            request=request,
            authenticated=True,
        )

    @app.action(
        "/profile/sessions/{session_id}/revoke",
        fragment_regions=(SESSION_LIST, SESSION_COUNT, SECURITY_ACTIVITY, TOAST_HOST),
        include_in_schema=False,
    )
    async def revoke_session_submit(
        session_id: SessionIdPath,
        request: Request,
        auth: Auth,
        db: DbSession,
        settings: SettingsDep,
        _csrf: RequireCsrf,
    ) -> Response:
        session = db.get(RefreshSession, session_id)
        if not session or session.user_id != auth.user.id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
        revoke_session(db, session, actor=auth.user, request=request)
        values = security_page_values(db, auth.user, settings)
        return await mutation_response(
            request,
            redirect=redirect_path(request, "/profile?notice=session-revoked&tab=Sessions"),
            fragment=ok_fragment(
                ui.session_list(
                    request,
                    values["sessions"],
                    auth=auth,
                    csrf_token=auth.session.csrf_token,
                ),
                oob=(
                    session_count_oob(len(values["sessions"])),
                    security_activity_oob(values["events"]),
                ),
                toast="The browser session was revoked.",
            ),
        )

    @app.action(
        "/security/secrets/{provider}",
        fragment_regions=(
            SECRET_SLOT_MCSCOP,
            SECRET_SLOT_MSS,
            SECRET_SLOT_POSTGRES,
            CONNECTION_STATUS_LIST,
            TOAST_HOST,
        ),
        include_in_schema=False,
    )
    async def secret_submit(
        provider: SecretProviderPath,
        request: Request,
        auth: Auth,
        db: DbSession,
        settings: SettingsDep,
        _csrf: RequireCsrf,
    ) -> Response:
        field_errors: dict[str, str] = {}
        changed = False
        automatic_test_error = ""
        try:
            specification = require_secret_provider(provider)
            submitted = await request.form()
            credentials = {
                field.name: str(submitted.get(field.name, "")) for field in specification.fields
            }
            result = store_user_credentials_if_changed(
                db,
                settings,
                user=auth.user,
                provider=provider,
                credentials=credentials,
                request=request,
            )
            stored = result.secret
            changed = result.changed
            error = ""
            response_status = status.HTTP_200_OK
        except (ValueError, SecretStorageError) as exc:
            db.rollback()
            if isinstance(exc, SecretStorageError):
                log_event(
                    log,
                    "connection.save.failed",
                    outcome="failed",
                    error_code="connection_storage_unavailable",
                    reference_id=getattr(request.state, "support_reference", ""),
                    user_id=auth.user.id,
                    provider=provider,
                    operation="save_credentials",
                    exception_type=type(exc).__name__,
                )
            try:
                specification = require_secret_provider(provider)
            except ValueError as provider_exc:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND, detail="Connection provider not found"
                ) from provider_exc
            stored = db.scalar(
                select(UserSecret).where(
                    UserSecret.user_id == auth.user.id, UserSecret.provider == specification.name
                )
            )
            if isinstance(exc, SecretStorageError):
                safe_outcome = connection_failure(
                    "connection_not_configured"
                    if isinstance(exc, ConnectionNotConfiguredError)
                    else "internal_error",
                    reference_id=getattr(request.state, "support_reference", ""),
                )
                error = (
                    safe_outcome.message
                    if safe_outcome is not None
                    else "The connection could not be saved. Share the reference with your administrator."
                )
                if safe_outcome is not None and safe_outcome.reference_id:
                    error = f"{error} Reference: {safe_outcome.reference_id}."
            else:
                error = redact_text(str(exc))[:240]
                field_errors = _credential_field_errors(specification, error)
            response_status = (
                status.HTTP_503_SERVICE_UNAVAILABLE
                if isinstance(exc, SecretStorageError)
                else status.HTTP_400_BAD_REQUEST
            )
        if not error and changed:
            try:
                stored = await run_owned_sync(
                    request,
                    _test_user_connection_in_thread,
                    settings,
                    auth.user.id,
                    provider,
                    getattr(request.state, "request_id", ""),
                    getattr(request.state, "support_reference", ""),
                )
            except SecretStorageError as exc:
                reference_id = getattr(request.state, "support_reference", "")
                outcome = connection_failure("internal_error", reference_id=reference_id)
                automatic_test_error = (
                    "The credentials were saved, but the connection test could not be completed."
                )
                if outcome.reference_id:
                    automatic_test_error += f" Reference: {outcome.reference_id}."
                response_status = status.HTTP_503_SERVICE_UNAVAILABLE
                log_event(
                    log,
                    "connection.save_test.failed",
                    outcome="failed",
                    error_code="connection_test_unavailable",
                    reference_id=reference_id,
                    user_id=auth.user.id,
                    provider=provider,
                    operation="save_and_test_credentials",
                    exception_type=type(exc).__name__,
                )
            finally:
                db.expire_all()
            if automatic_test_error:
                stored = db.scalar(
                    select(UserSecret).where(
                        UserSecret.user_id == auth.user.id,
                        UserSecret.provider == specification.name,
                    )
                )
        values = security_page_values(db, auth.user, settings)
        status_list = connection_status_oob(
            request,
            values["secret_slots"],
            csrf_token=auth.session.csrf_token,
        )
        slot = ui.secret_slot(
            request,
            specification,
            stored,
            csrf_token=auth.session.csrf_token,
            error=error or automatic_test_error,
            field_errors=field_errors,
            success=(
                ""
                if error
                else f"{specification.label} credentials saved."
                if changed
                else "No credential changes detected. The connection was not retested."
            ),
        )
        if error:
            if not is_htmx_request(request):
                request.state.secret_feedback = {
                    specification.name: {"error": error, "field_errors": field_errors}
                }
                request.state.security_response_status = response_status
                return await security_page(
                    request=request,
                    auth=auth,
                    db=db,
                    settings=settings,
                    notice="",
                )
            return await interaction_response(
                request,
                ok_fragment(
                    slot,
                    oob=(status_list,),
                    status_code=response_status,
                    toast=error,
                    toast_tone="danger",
                ),
            )
        if automatic_test_error:
            if not is_htmx_request(request):
                request.state.security_page_feedback = connection_failure(
                    "internal_error",
                    reference_id=getattr(request.state, "support_reference", ""),
                )
                request.state.security_response_status = response_status
                return await security_page(
                    request=request,
                    auth=auth,
                    db=db,
                    settings=settings,
                    notice="",
                )
            return await interaction_response(
                request,
                ok_fragment(
                    slot,
                    oob=(status_list,),
                    status_code=response_status,
                    toast=automatic_test_error,
                    toast_tone="danger",
                ),
            )
        checked_outcome = connection_outcome(stored) if changed else None
        toast = (
            "No credential changes detected. The connection was not retested."
            if not changed
            else f"{specification.label} credentials saved and connection verified."
            if stored is not None and stored.validation_status == "connected"
            else checked_outcome.title
            if checked_outcome is not None
            else f"{specification.label} credentials saved and checked."
        )
        toast_tone = (
            "info"
            if not changed
            else "success"
            if stored is not None and stored.validation_status == "connected"
            else "danger"
            if stored is not None and stored.validation_status == "failed"
            else "warning"
        )
        return await mutation_response(
            request,
            redirect=redirect_path(request, "/security?notice=secret-saved"),
            fragment=ok_fragment(
                slot,
                oob=(status_list,),
                toast=toast,
                toast_tone=toast_tone,
            ),
        )

    @app.action(
        "/security/secrets/{provider}/delete",
        fragment_regions=(
            SECRET_SLOT_MCSCOP,
            SECRET_SLOT_MSS,
            SECRET_SLOT_POSTGRES,
            CONNECTION_STATUS_LIST,
            TOAST_HOST,
        ),
        include_in_schema=False,
    )
    async def secret_delete_submit(
        provider: SecretProviderPath,
        request: Request,
        auth: Auth,
        db: DbSession,
        settings: SettingsDep,
        _csrf: RequireCsrf,
    ) -> Response:
        try:
            specification = require_secret_provider(provider)
            deleted = delete_user_secret(db, user=auth.user, provider=provider, request=request)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Connection provider not found"
            ) from exc
        if not deleted:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Connection is not configured."
            )
        values = security_page_values(db, auth.user, settings)
        return await mutation_response(
            request,
            redirect=redirect_path(request, "/security?notice=secret-deleted"),
            fragment=ok_fragment(
                ui.secret_slot(
                    request,
                    specification,
                    None,
                    csrf_token=auth.session.csrf_token,
                    success=f"{specification.label} credentials deleted.",
                ),
                oob=(
                    connection_status_oob(
                        request,
                        values["secret_slots"],
                        csrf_token=auth.session.csrf_token,
                    ),
                ),
                toast=f"{specification.label} credentials deleted.",
            ),
        )

    @app.action(
        "/security/secrets/{provider}/test",
        fragment_regions=(CONNECTION_STATUS_LIST, TOAST_HOST),
        include_in_schema=False,
    )
    async def connection_test_submit(
        provider: SecretProviderPath,
        request: Request,
        auth: Auth,
        db: DbSession,
        settings: SettingsDep,
        _csrf: RequireCsrf,
    ) -> Response:
        try:
            specification = require_secret_provider(provider)
            checked = await run_owned_sync(
                request,
                _test_user_connection_in_thread,
                settings,
                auth.user.id,
                provider,
                getattr(request.state, "request_id", ""),
                getattr(request.state, "support_reference", ""),
            )
        except (ValueError, SecretStorageError) as exc:
            reference_id = getattr(request.state, "support_reference", "")
            code = (
                "connection_not_configured"
                if isinstance(exc, ConnectionNotConfiguredError)
                else "internal_error"
                if isinstance(exc, SecretStorageError)
                else "connection_invalid"
            )
            outcome = connection_failure(code, reference_id=reference_id)
            values = security_page_values(db, auth.user, settings)
            if not is_htmx_request(request):
                request.state.security_page_feedback = outcome
                request.state.security_response_status = (
                    status.HTTP_503_SERVICE_UNAVAILABLE
                    if isinstance(exc, SecretStorageError)
                    else status.HTTP_400_BAD_REQUEST
                )
                return await security_page(
                    request=request,
                    auth=auth,
                    db=db,
                    settings=settings,
                    notice="",
                )
            return await mutation_response(
                request,
                redirect=redirect_path(request, "/security"),
                fragment=ok_fragment(
                    ui.connection_status_list(
                        request,
                        values["secret_slots"],
                        csrf_token=auth.session.csrf_token,
                    ),
                    status_code=(
                        status.HTTP_503_SERVICE_UNAVAILABLE
                        if isinstance(exc, SecretStorageError)
                        else status.HTTP_400_BAD_REQUEST
                    ),
                    toast=(
                        f"{outcome.message} Reference: {outcome.reference_id}."
                        if outcome.reference_id
                        else outcome.message
                    ),
                    oob=(
                        request_feedback_oob(
                            outcome.message,
                            title=outcome.title,
                            reference_id=outcome.reference_id,
                        ),
                    ),
                    toast_tone="danger" if isinstance(exc, SecretStorageError) else "warning",
                ),
            )
        values = security_page_values(db, auth.user, settings)
        outcome = connection_outcome(checked)
        toast = (
            outcome.title if outcome is not None else f"{specification.label} connection checked."
        )
        toast_tone = (
            "success"
            if checked.validation_status == "connected"
            else "danger"
            if checked.validation_status == "failed"
            else "warning"
        )
        return await mutation_response(
            request,
            redirect=redirect_path(request, "/security"),
            fragment=ok_fragment(
                ui.connection_status_list(
                    request,
                    values["secret_slots"],
                    csrf_token=auth.session.csrf_token,
                ),
                toast=toast,
                toast_tone=toast_tone,
            ),
        )


def _test_user_connection_in_thread(
    settings,
    user_id: str,
    provider: str,
    request_id: str = "",
    reference_id: str = "",
):
    """Run synchronous connector I/O with a session owned by the worker thread."""

    with current_session_factory()() as db:
        user = db.get(User, user_id)
        if user is None:
            raise SecretStorageError("The account is no longer available.")
        return test_user_connection(
            db,
            settings=settings,
            user=user,
            provider=provider,
            request=RequestMetadata(request_id=request_id, reference_id=reference_id),
            connector_resolver=connection_tester_for,
        )
