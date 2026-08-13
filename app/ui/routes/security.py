"""Authenticated connection routes and account security actions."""

from __future__ import annotations

from fastapi import HTTPException, Request, status
from fastapi.responses import RedirectResponse
from hedron import Hedron, InteractionResult, html
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from starlette.responses import Response

from app.dependencies import Auth, DbSession, RequireCsrf, SettingsDep, clear_auth_cookies
from app.models import RefreshSession, UserSecret
from app.routing import is_htmx_request, redirect_path
from app.security.passwords import PasswordPolicyError
from app.services.accounts import (
    CurrentPasswordError,
    security_page_values,
)
from app.services.accounts import (
    change_password as change_account_password,
)
from app.services.auth import revoke_session
from app.services.secrets import (
    SecretStorageError,
    delete_user_secret,
    require_secret_provider,
    store_user_credentials,
    test_user_connection,
    wake_provider_runtime,
)
from app.ui import partials as ui
from app.ui.http import mutation_response, render_authenticated_view, render_page
from app.ui.interactions import (
    connection_status_oob,
    htmx_redirect,
    interaction_response,
    ok_fragment,
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
from app.ui.regions import (
    CONNECTION_STATUS_LIST,
    MAIN_PANEL,
    PASSWORD_FORM,
    SECRET_SLOT_ADVANA,
    SECRET_SLOT_MONGODB,
    SECRET_SLOT_MSS,
    SECRET_SLOT_POSTGRES,
    SECURITY_ACTIVITY,
    SESSION_COUNT,
    SESSION_LIST,
    SIDE_NAV,
    TOAST_HOST,
)


def register_security_routes(app: Hedron) -> None:
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
            "secret-saved": "The connection credentials were saved.",
            "secret-deleted": "The connection credentials were deleted.",
        }
        values = security_page_values(
            db, auth.user, settings, security_success=notices.get(notice, "")
        )
        csrf = auth.session.csrf_token
        body = [
            page_heading(
                "Workspace settings",
                "Connections",
                "Manage the encrypted credentials Data Mover uses to reach your remote sources.",
            ),
            alert_box(values["security_success"], kind="success"),
            html.div(
                ui.security_tabs(
                    request,
                    csrf_token=csrf,
                    secret_slots=values["secret_slots"],
                ),
                class_="security-stack",
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
        )

    @app.fragment("/profile/activity", region=SECURITY_ACTIVITY, include_in_schema=False)
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
            return ok_fragment(ui.security_activity(values["events"]))
        except SQLAlchemyError:
            return ok_fragment(
                ui.security_activity_error(request),
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
                    htmx_redirect(redirect_path(request, "/login?password=changed")),
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
                html.div(
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
                    class_="security-stack",
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
            SECRET_SLOT_ADVANA,
            SECRET_SLOT_MONGODB,
            SECRET_SLOT_MSS,
            SECRET_SLOT_POSTGRES,
            CONNECTION_STATUS_LIST,
            SECURITY_ACTIVITY,
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
        try:
            specification = require_secret_provider(provider)
            submitted = await request.form()
            credentials = {
                field.name: str(submitted.get(field.name, "")) for field in specification.fields
            }
            stored = store_user_credentials(
                db,
                settings,
                user=auth.user,
                provider=provider,
                credentials=credentials,
                request=request,
            )
            error = ""
            response_status = status.HTTP_200_OK
        except (ValueError, SecretStorageError) as exc:
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
            error = str(exc)
            response_status = (
                status.HTTP_503_SERVICE_UNAVAILABLE
                if isinstance(exc, SecretStorageError)
                else status.HTTP_400_BAD_REQUEST
            )
        values = security_page_values(db, auth.user, settings)
        events = values["events"]
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
            error=error,
            success=f"{specification.label} credentials saved." if not error else "",
        )
        if error:
            if not is_htmx_request(request):
                raise HTTPException(status_code=response_status, detail=error)
            return await interaction_response(
                request,
                ok_fragment(
                    slot,
                    oob=(security_activity_oob(events), status_list),
                    status_code=response_status,
                    toast=error,
                    toast_tone="danger",
                ),
            )
        return await mutation_response(
            request,
            redirect=redirect_path(request, "/security?notice=secret-saved"),
            fragment=ok_fragment(
                slot,
                oob=(security_activity_oob(events), status_list),
                toast=f"{specification.label} credentials saved.",
            ),
        )

    @app.action(
        "/security/secrets/{provider}/delete",
        fragment_regions=(
            SECRET_SLOT_ADVANA,
            SECRET_SLOT_MONGODB,
            SECRET_SLOT_MSS,
            SECRET_SLOT_POSTGRES,
            CONNECTION_STATUS_LIST,
            SECURITY_ACTIVITY,
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
        events = values["events"]
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
                    security_activity_oob(events),
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
        fragment_regions=(CONNECTION_STATUS_LIST, SECURITY_ACTIVITY, TOAST_HOST),
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
            test_user_connection(db, user=auth.user, provider=provider, request=request)
        except (ValueError, SecretStorageError) as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
        values = security_page_values(db, auth.user, settings)
        return await mutation_response(
            request,
            redirect=redirect_path(request, "/security"),
            fragment=ok_fragment(
                ui.connection_status_list(
                    request,
                    values["secret_slots"],
                    csrf_token=auth.session.csrf_token,
                ),
                oob=(security_activity_oob(values["events"]),),
                toast=f"{specification.label} connection passed its health check.",
            ),
        )

    @app.action(
        "/security/secrets/{provider}/wake",
        fragment_regions=(CONNECTION_STATUS_LIST, SECURITY_ACTIVITY, TOAST_HOST),
        include_in_schema=False,
    )
    async def connection_wake_submit(
        provider: SecretProviderPath,
        request: Request,
        auth: Auth,
        db: DbSession,
        settings: SettingsDep,
        _csrf: RequireCsrf,
    ) -> Response:
        try:
            specification = require_secret_provider(provider)
            wake_provider_runtime(db, user=auth.user, provider=provider, request=request)
        except (ValueError, SecretStorageError) as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
        values = security_page_values(db, auth.user, settings)
        return await mutation_response(
            request,
            redirect=redirect_path(request, "/security"),
            fragment=ok_fragment(
                ui.connection_status_list(
                    request,
                    values["secret_slots"],
                    csrf_token=auth.session.csrf_token,
                ),
                oob=(security_activity_oob(values["events"]),),
                toast=f"{specification.label} Databricks cluster is running.",
            ),
        )
