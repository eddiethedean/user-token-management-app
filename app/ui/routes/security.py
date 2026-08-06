"""Authenticated security routes: password, sessions, secrets, activity."""

from __future__ import annotations

from fastapi import HTTPException, Request
from fastapi.responses import RedirectResponse
from hedron import Hedron, InteractionResult, html
from sqlalchemy import select
from starlette.responses import Response

from app.dependencies import Auth, DbSession, RequireCsrf, SettingsDep, clear_auth_cookies
from app.models import RefreshSession, UserSecret
from app.routing import app_path, is_htmx_request
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
    store_user_secret,
)
from app.ui import partials as ui
from app.ui.http import mutation_response, render_authenticated_view, render_page
from app.ui.interactions import htmx_redirect, interaction_response, ok_fragment
from app.ui.layout import alert_box, app_shell, page_heading
from app.ui.params import (
    NoticeQuery,
    PasswordForm,
    SecretProviderPath,
    SecretTokenForm,
    SessionIdPath,
)
from app.ui.regions import SECURITY_ACTIVITY


def register_security_routes(app: Hedron) -> None:
    @app.get("/security", include_in_schema=False)
    async def security_page(
        request: Request,
        auth: Auth,
        db: DbSession,
        settings: SettingsDep,
        notice: NoticeQuery = "",
    ) -> Response:
        request.state.hedron_authenticated = True
        notices = {
            "session-revoked": "The browser session was revoked.",
            "secret-saved": "The API token was saved.",
            "secret-deleted": "The API token was deleted.",
        }
        values = security_page_values(
            db, auth.user, settings, security_success=notices.get(notice, "")
        )
        csrf = auth.session.csrf_token
        body = [
            page_heading(
                "Account protection",
                "Security",
                "Manage your password, API tokens, sessions, and recent account activity.",
            ),
            alert_box(values["security_success"], kind="success"),
            html.div(
                ui.security_tabs(
                    csrf_token=csrf,
                    local_password=values["local_password"],
                    secret_slots=values["secret_slots"],
                    sessions=values["sessions"],
                    auth=auth,
                ),
                class_="security-stack",
            ),
        ]
        return await render_authenticated_view(
            request,
            body=body,
            auth=auth,
            settings=settings,
            page_title="Security",
            csrf_token=csrf,
            push_path="/security",
            headers={"Cache-Control": "no-store"},
        )

    @app.fragment("/security/activity", region=SECURITY_ACTIVITY, include_in_schema=False)
    async def security_activity_fragment(
        request: Request,
        auth: Auth,
        db: DbSession,
        settings: SettingsDep,
    ) -> InteractionResult | RedirectResponse:
        request.state.hedron_authenticated = True
        if not is_htmx_request(request):
            return RedirectResponse(app_path(request, "/security"), status_code=303)
        try:
            values = security_page_values(db, auth.user, settings)
            return ok_fragment(ui.security_activity(values["events"]))
        except Exception:
            return ok_fragment(ui.security_activity_error())

    @app.post("/security/password", include_in_schema=False)
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
            raise HTTPException(status_code=403, detail="Password changes are disabled")
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
                    htmx_redirect(app_path(request, "/login?password=changed")),
                )
                clear_auth_cookies(response, settings, request)
                return response
            response = RedirectResponse(
                app_path(request, "/login?password=changed"), status_code=303
            )
            clear_auth_cookies(response, settings, request)
            return response
        if is_htmx_request(request):
            return await interaction_response(
                request,
                ok_fragment(
                    ui.password_form(
                        csrf_token=auth.session.csrf_token,
                        error=error,
                        field_errors=field_errors,
                    ),
                    status_code=400,
                ),
            )
        csrf = auth.session.csrf_token
        return render_page(
            app_shell(
                page_heading(
                    "Account protection",
                    "Security",
                    "Manage your password, API tokens, sessions, and recent account activity.",
                ),
                html.section(
                    ui.password_form(csrf_token=csrf, error=error, field_errors=field_errors),
                    class_="panel",
                ),
                request=request,
                settings=settings,
                auth=auth,
                page_title="Security",
                csrf_token=csrf,
            ),
            status_code=400,
            headers={"Cache-Control": "no-store"},
            request=request,
            authenticated=True,
        )

    @app.post("/security/sessions/{session_id}/revoke", include_in_schema=False)
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
            raise HTTPException(status_code=404, detail="Session not found")
        revoke_session(db, session, actor=auth.user, request=request)
        values = security_page_values(db, auth.user, settings)
        return await mutation_response(
            request,
            redirect=app_path(request, "/security?notice=session-revoked"),
            fragment=ok_fragment(
                html.div(
                    ui.session_list(
                        values["sessions"], auth=auth, csrf_token=auth.session.csrf_token
                    ),
                    ui.session_count(values["sessions"], oob=True),
                    ui.security_activity(values["events"], oob=True),
                ),
                toast="The browser session was revoked.",
            ),
        )

    @app.post("/security/secrets/{provider}", include_in_schema=False)
    async def secret_submit(
        provider: SecretProviderPath,
        request: Request,
        auth: Auth,
        db: DbSession,
        settings: SettingsDep,
        _csrf: RequireCsrf,
        token: SecretTokenForm,
    ) -> Response:
        try:
            specification = require_secret_provider(provider)
            stored = store_user_secret(
                db, settings, user=auth.user, provider=provider, token=token, request=request
            )
            error = ""
            response_status = 200
        except (ValueError, SecretStorageError) as exc:
            try:
                specification = require_secret_provider(provider)
            except ValueError as provider_exc:
                raise HTTPException(
                    status_code=404, detail="API token provider not found"
                ) from provider_exc
            stored = db.scalar(
                select(UserSecret).where(
                    UserSecret.user_id == auth.user.id, UserSecret.provider == specification.name
                )
            )
            error = str(exc)
            response_status = 503 if isinstance(exc, SecretStorageError) else 400
        events = security_page_values(db, auth.user, settings)["events"]
        slot = ui.secret_slot(
            specification,
            stored,
            csrf_token=auth.session.csrf_token,
            error=error,
            success=f"{specification.label} API token saved." if not error else "",
        )
        if error:
            if not is_htmx_request(request):
                raise HTTPException(status_code=response_status, detail=error)
            return await interaction_response(
                request,
                ok_fragment(
                    html.div(slot, ui.security_activity(events, oob=True)),
                    status_code=response_status,
                    toast=error,
                    toast_tone="danger",
                ),
            )
        return await mutation_response(
            request,
            redirect=app_path(request, "/security?notice=secret-saved"),
            fragment=ok_fragment(
                html.div(slot, ui.security_activity(events, oob=True)),
                toast=f"{specification.label} API token saved.",
            ),
        )

    @app.post("/security/secrets/{provider}/delete", include_in_schema=False)
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
            raise HTTPException(status_code=404, detail="API token provider not found") from exc
        if not deleted:
            raise HTTPException(status_code=404, detail="API token is not configured.")
        events = security_page_values(db, auth.user, settings)["events"]
        return await mutation_response(
            request,
            redirect=app_path(request, "/security?notice=secret-deleted"),
            fragment=ok_fragment(
                html.div(
                    ui.secret_slot(
                        specification,
                        None,
                        csrf_token=auth.session.csrf_token,
                        success=f"{specification.label} API token deleted.",
                    ),
                    ui.security_activity(events, oob=True),
                ),
                toast=f"{specification.label} API token deleted.",
            ),
        )
