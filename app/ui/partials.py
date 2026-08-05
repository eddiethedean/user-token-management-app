"""UI fragment builders for Access Registry Hedron port."""

from __future__ import annotations

from urllib.parse import urlencode

from hedron import ComponentRef, Dialog, ErrorState, Lazy, Loading, Pagination, Tabs, html

from app.dependencies import AuthContext
from app.models import AuditEvent, Invitation, RefreshSession, Role, User, UserStatus
from app.services.secrets import SecretProvider
from app.ui.layout import INDICATOR, account_summary, alert_box
from app.ui.urls import form_action, hx_attrs, page_href


def field_error(field_id: str, message: str = "") -> object:
    """Inline field error slot (empty when valid)."""
    attrs: dict = {
        "id": f"field-error-{field_id}",
        "class_": "field-error" + (" is-active" if message else ""),
    }
    if message:
        attrs["role"] = "alert"
    return html.div(message, **attrs)


def hedron_pagination(
    *,
    page: int,
    page_size: int,
    total: int,
    base_path: str,
    target: str,
) -> object:
    """Hedron Pagination builtin (innerHTML into a dedicated body region)."""
    pages = max(1, (total + page_size - 1) // page_size) if total else 1
    if pages <= 1:
        return html.div()
    return Pagination(
        page=page,
        page_size=page_size,
        total=total,
        base_path=base_path,
        target=target,
    )


def _filter_base_path(path: str, **params: str) -> str:
    cleaned = {key: value for key, value in params.items() if value}
    if not cleaned:
        return path
    return f"{path}?{urlencode(cleaned)}"


def profile_form(auth: AuthContext, *, csrf_token: str, success: str = "") -> object:
    user = auth.user
    return html.div(
        alert_box(success, kind="success"),
        html.form(
            html.input(type="hidden", name="csrf_token", value=csrf_token),
            html.div(
                html.div(
                    html.label("Government email", for_="email"),
                    html.input(id="email", value=user.email_original, disabled=True),
                    html.p(
                        "Verified addresses can only be changed through an administrator.",
                        class_="field-help",
                    ),
                    class_="field-full",
                ),
                html.div(
                    html.label("Full name", for_="full_name"),
                    html.input(
                        id="full_name",
                        name="full_name",
                        value=user.full_name or "",
                        autocomplete="name",
                        maxlength="160",
                    ),
                    class_="field-full",
                ),
                html.div(
                    html.label("Organization", for_="organization"),
                    html.input(
                        id="organization",
                        name="organization",
                        value=user.organization or "",
                        maxlength="160",
                    ),
                ),
                html.div(
                    html.label("Job title", for_="job_title"),
                    html.input(
                        id="job_title",
                        name="job_title",
                        value=user.job_title or "",
                        maxlength="160",
                    ),
                ),
                html.div(
                    html.label("Work phone", for_="phone"),
                    html.input(
                        id="phone",
                        name="phone",
                        value=user.phone or "",
                        autocomplete="tel",
                        maxlength="40",
                    ),
                    class_="field-full",
                ),
                class_="field-grid",
            ),
            html.div(
                html.button("Save changes", class_="button button-primary", type="submit"),
                html.span("Saving…", class_="htmx-indicator"),
                class_="form-actions",
            ),
            class_="stack-form",
            action=form_action("profile"),
            method="post",
            **hx_attrs(
                path="profile",
                target="#profile-form-region",
                sync="this:drop",
                disabled_elt="find button",
                indicator=INDICATOR,
            ),
        ),
        id="profile-form-region",
    )


def profile_identity(auth: AuthContext, *, oob: bool = False) -> object:
    user = auth.user
    attrs: dict = {"id": "profile-identity", "class_": "panel identity-panel"}
    if oob:
        attrs["hx-swap-oob"] = "outerHTML"
    initial = (user.full_name or user.email_original or "?")[:1].upper()
    last_login = (
        user.last_login_at.strftime("%b %d, %Y %H:%M") if user.last_login_at else "First session"
    )
    return html.aside(
        html.div(initial, class_="identity-avatar"),
        html.h2(user.full_name or "Account holder"),
        html.p(user.email_original),
        html.dl(
            html.div(
                html.dt("Account status"),
                html.dd(html.span(user.status, class_="pill pill-active")),
            ),
            html.div(
                html.dt("Access level"),
                html.dd((", ".join(user.role_names) or "user").title()),
            ),
            html.div(
                html.dt("Created"),
                html.dd(user.created_at.strftime("%b %d, %Y")),
            ),
            html.div(
                html.dt("Last sign-in"),
                html.dd(last_login),
            ),
            class_="detail-list",
        ),
        html.a(
            "Review account security",
            class_="button button-secondary button-wide",
            href=page_href("/security"),
        ),
        **attrs,
    )


def _password_field(
    label: str,
    field_id: str,
    *,
    autocomplete: str,
    minlength: str | None = None,
    error: str = "",
) -> list[object]:
    attrs: dict = {
        "id": field_id,
        "name": field_id,
        "type": "password",
        "required": True,
        "autocomplete": autocomplete,
        "maxlength": "128",
    }
    if minlength:
        attrs["minlength"] = minlength
    if error:
        attrs["aria"] = {"invalid": "true", "describedby": f"field-error-{field_id}"}
    return [
        html.label(label, for_=field_id),
        html.input(**attrs),
        html.button(
            "Show password",
            class_="password-toggle",
            type="button",
            data={"password-toggle": field_id},
            aria={"pressed": "false"},
        ),
        field_error(field_id, error),
    ]


def password_form(
    *,
    csrf_token: str,
    error: str = "",
    success: str = "",
    field_errors: dict[str, str] | None = None,
) -> object:
    field_errors = field_errors or {}
    if success:
        return html.div(
            alert_box(success, kind="success"),
            html.a("Return to sign in", class_="button button-primary", href=page_href("/login")),
            id="password-form-region",
            class_="form-column",
        )
    top_error = error if error and not field_errors else ""
    return html.div(
        alert_box(top_error),
        html.form(
            html.input(type="hidden", name="csrf_token", value=csrf_token),
            *_password_field(
                "Current password",
                "current_password",
                autocomplete="current-password",
                error=field_errors.get("current_password", ""),
            ),
            *_password_field(
                "New password",
                "new_password",
                autocomplete="new-password",
                minlength="15",
                error=field_errors.get("new_password", ""),
            ),
            *_password_field(
                "Confirm new password",
                "new_password_confirm",
                autocomplete="new-password",
                minlength="15",
                error=field_errors.get("new_password_confirm", ""),
            ),
            html.button("Change password", class_="button button-primary", type="submit"),
            class_="stack-form",
            action=form_action("security/password"),
            method="post",
            **hx_attrs(
                path="security/password",
                target="#password-form-region",
                sync="this:drop",
                disabled_elt="find button",
                indicator=INDICATOR,
            ),
        ),
        id="password-form-region",
        class_="form-column",
    )


def secret_slot(
    provider: SecretProvider,
    secret,
    *,
    csrf_token: str,
    error: str = "",
    success: str = "",
) -> object:
    configured = secret is not None
    if configured:
        metadata = (
            f"Saved {secret.updated_at.strftime('%b %d, %Y at %H:%M')}. "
            "The stored value cannot be revealed."
        )
    else:
        metadata = f"No {provider.label} token is available to your runs."
    return html.div(
        html.div(
            html.div(
                html.span(provider.mark, class_="secret-provider-mark", aria={"hidden": "true"}),
                html.div(
                    html.h3(provider.label),
                    html.code(provider.environment_variable),
                ),
            ),
            html.span(
                "Configured" if configured else "Not configured",
                class_=f"pill {'pill-active' if configured else 'pill-muted'}",
            ),
            class_="secret-card-heading",
        ),
        alert_box(error),
        alert_box(success, kind="success"),
        html.p(metadata, class_="secret-metadata"),
        html.form(
            html.input(type="hidden", name="csrf_token", value=csrf_token),
            html.label(
                f"{provider.label} API token", for_=f"{provider.name}-token", class_="sr-only"
            ),
            html.input(
                id=f"{provider.name}-token",
                name="token",
                type="password",
                autocomplete="new-password",
                minlength="8",
                maxlength="8192",
                placeholder=f"Paste {provider.label} API token",
                required=True,
            ),
            html.button(
                "Replace" if configured else "Save",
                class_="button button-primary button-small button-action",
                type="submit",
            ),
            class_="secret-form",
            action=form_action(f"security/secrets/{provider.name}"),
            method="post",
            **hx_attrs(
                path=f"security/secrets/{provider.name}",
                target=f"#secret-slot-{provider.name}",
                sync="closest .secret-card:drop",
                disabled_elt="find button",
                indicator=INDICATOR,
            ),
        ),
        (
            html.div(
                html.button(
                    "Delete token",
                    class_="button button-danger button-small",
                    type="button",
                    data={"hedron-dialog-open": f"#delete-secret-{provider.name}"},
                ),
                Dialog(
                    f"Delete {provider.label} token",
                    html.p(
                        f"Delete your {provider.label} API token? Runs using it will stop working."
                    ),
                    html.form(
                        html.input(type="hidden", name="csrf_token", value=csrf_token),
                        html.button(
                            "Delete token",
                            class_="button button-danger",
                            type="submit",
                        ),
                        action=form_action(f"security/secrets/{provider.name}/delete"),
                        method="post",
                        **hx_attrs(
                            path=f"security/secrets/{provider.name}/delete",
                            target=f"#secret-slot-{provider.name}",
                            sync="closest .secret-card:drop",
                            disabled_elt="find button",
                            indicator=INDICATOR,
                        ),
                    ),
                    id=f"delete-secret-{provider.name}",
                    open=False,
                ),
            )
            if configured
            else html.div()
        ),
        id=f"secret-slot-{provider.name}",
        class_="secret-card",
    )


def session_list(
    sessions: list[RefreshSession],
    *,
    auth: AuthContext,
    csrf_token: str,
) -> object:
    rows = []
    for session in sessions:
        is_current = session.id == auth.session.id
        if is_current:
            action_node: object = html.span("Current", class_="pill pill-active")
        else:
            dialog_id = f"revoke-session-{session.id}"
            action_node = html.div(
                html.button(
                    "Revoke",
                    class_="button button-danger button-small",
                    type="button",
                    data={"hedron-dialog-open": f"#{dialog_id}"},
                ),
                Dialog(
                    "Revoke session",
                    html.p("Revoke this browser session? The device will need to sign in again."),
                    html.form(
                        html.input(type="hidden", name="csrf_token", value=csrf_token),
                        html.button(
                            "Revoke session",
                            class_="button button-danger",
                            type="submit",
                        ),
                        action=form_action(f"security/sessions/{session.id}/revoke"),
                        method="post",
                        **hx_attrs(
                            path=f"security/sessions/{session.id}/revoke",
                            target="#session-list",
                            sync="#session-list:drop",
                            disabled_elt="find button",
                            indicator=INDICATOR,
                        ),
                    ),
                    id=dialog_id,
                    open=False,
                ),
            )
        rows.append(
            html.div(
                html.div("▣", class_="session-device", aria={"hidden": "true"}),
                html.div(
                    html.strong("Current session" if is_current else "Browser session"),
                    html.span((session.user_agent or "Unknown client")[:90]),
                    html.small(
                        f"Last active {session.last_seen_at.strftime('%b %d, %Y %H:%M')} · "
                        f"{session.source_ip or 'source unavailable'}"
                    ),
                    class_="session-copy",
                ),
                action_node,
                class_="session-row",
            )
        )
    if not rows:
        rows.append(html.p("No active sessions.", class_="empty-state"))
    return html.div(*rows, id="session-list", class_="session-list")


def session_count(sessions: list[RefreshSession], *, oob: bool = False) -> object:
    attrs: dict = {"id": "session-count", "class_": "count-badge"}
    if oob:
        attrs["hx-swap-oob"] = "outerHTML"
    return html.span(str(len(sessions)), **attrs)


def security_activity(events: list[AuditEvent], *, oob: bool = False) -> object:
    attrs: dict = {"id": "security-activity", "class_": "event-list"}
    if oob:
        attrs["hx-swap-oob"] = "outerHTML"
    items = []
    for event in events:
        title = event.event_type.replace(".", " ").title()
        items.append(
            html.div(
                html.span("•", class_="event-icon", aria={"hidden": "true"}),
                html.div(
                    html.strong(title),
                    html.small(
                        f"{event.occurred_at.strftime('%b %d, %Y at %H:%M')} · {event.outcome}"
                    ),
                ),
                class_="event-row",
            )
        )
    if not items:
        items.append(html.p("No recent security activity.", class_="empty-state"))
    return html.div(*items, **attrs)


def security_activity_error(
    message: str = "Could not load security activity.",
) -> object:
    """ErrorState wrapped so Lazy outerHTML swaps keep a stable region id."""
    return html.div(
        ErrorState(
            message,
            retry_href="/security/activity",
            target="#security-activity",
        ),
        id="security-activity",
        class_="event-list",
        data={"lazy-error": "security-activity"},
    )


def security_activity_lazy() -> object:
    """Deferred activity panel loaded via Hedron Lazy after first paint."""
    return Lazy(
        ref=ComponentRef(
            logical_id="security-activity",
            path="/security/activity",
            swap="outerHTML",
        ),
        placeholder=Loading("Loading activity…"),
        target_id="security-activity",
    )


def security_tabs(
    *,
    csrf_token: str,
    local_password: bool,
    secret_slots,
    sessions: list[RefreshSession],
    auth: AuthContext,
) -> object:
    panels: list[tuple[str, object]] = []
    if local_password:
        panels.append(
            (
                "Password",
                html.section(
                    html.div(
                        html.h2("Change password"),
                        html.p(
                            "Changing your password signs out every active session, including this one."
                        ),
                    ),
                    password_form(csrf_token=csrf_token),
                    class_="panel split-panel",
                ),
            )
        )
    panels.append(
        (
            "Tokens",
            html.section(
                html.div(
                    html.h2("API tokens"),
                    html.p(
                        "Add only an approved service token. Saved values are encrypted and cannot be viewed again."
                    ),
                    class_="panel-heading",
                ),
                html.div(
                    *[secret_slot(p, s, csrf_token=csrf_token) for p, s in secret_slots],
                    class_="secret-grid",
                ),
                class_="panel",
            ),
        )
    )
    panels.append(
        (
            "Sessions",
            html.section(
                html.div(
                    html.div(
                        html.h2("Active sessions"),
                        html.p("Revoke any browser or client you no longer recognize."),
                    ),
                    session_count(sessions),
                    class_="panel-heading",
                ),
                session_list(sessions, auth=auth, csrf_token=csrf_token),
                class_="panel",
            ),
        )
    )
    panels.append(
        (
            "Activity",
            html.section(
                html.div(
                    html.h2("Recent security activity"),
                    html.p("Latest events associated with your account."),
                    class_="panel-heading",
                ),
                security_activity_lazy(),
                class_="panel",
            ),
        )
    )
    return Tabs(*panels, id="security-tabs")


def user_match_count(total: int, *, oob: bool = False) -> object:
    attrs: dict = {"id": "user-match-count", "class_": "verification-badge"}
    if oob:
        attrs["hx-swap-oob"] = "outerHTML"
    return html.span(f"{total} matching accounts", **attrs)


def user_table(
    users: list[User],
    *,
    csrf_token: str,
    query: str,
    status_filter: str,
    page: int,
    page_count: int,
    total_users: int | None = None,
    page_size: int = 50,
) -> object:
    rows = []
    for user in users:
        actions = []
        if user.status == UserStatus.PENDING.value:
            for action, label in (("approve", "Approve"), ("deny", "Deny")):
                actions.append(
                    html.form(
                        html.input(type="hidden", name="csrf_token", value=csrf_token),
                        html.input(type="hidden", name="q", value=query),
                        html.input(type="hidden", name="status", value=status_filter),
                        html.input(type="hidden", name="page", value=str(page)),
                        html.button(
                            label, class_="button button-small button-action", type="submit"
                        ),
                        action=form_action(f"admin/users/{user.id}/{action}"),
                        method="post",
                        **hx_attrs(
                            path=f"admin/users/{user.id}/{action}",
                            target="#user-directory-body",
                            include="closest form",
                            disabled_elt="find button",
                            indicator=INDICATOR,
                        ),
                    )
                )
        else:
            is_active = user.status == UserStatus.ACTIVE.value
            action_label = "Disable" if is_active else "Enable"
            dialog_id = f"toggle-user-{user.id}"
            toggle_form = html.form(
                html.input(type="hidden", name="csrf_token", value=csrf_token),
                html.input(type="hidden", name="q", value=query),
                html.input(type="hidden", name="status", value=status_filter),
                html.input(type="hidden", name="page", value=str(page)),
                html.button(
                    action_label,
                    class_="button button-small button-action",
                    type="submit",
                ),
                action=form_action(f"admin/users/{user.id}/toggle"),
                method="post",
                **hx_attrs(
                    path=f"admin/users/{user.id}/toggle",
                    target="#user-directory-body",
                    disabled_elt="find button",
                    indicator=INDICATOR,
                ),
            )
            if is_active:
                actions.append(
                    html.div(
                        html.button(
                            action_label,
                            class_="button button-small",
                            type="button",
                            data={"hedron-dialog-open": f"#{dialog_id}"},
                        ),
                        Dialog(
                            "Disable account",
                            html.p(
                                f"Disable {user.email_original}? "
                                "Active sessions for this account will be revoked."
                            ),
                            toggle_form,
                            id=dialog_id,
                            open=False,
                        ),
                    )
                )
            else:
                actions.append(toggle_form)
        rows.append(
            html.tr(
                html.td(html.strong(user.email_original), html.small(user.full_name or "")),
                html.td(user.status),
                html.td(", ".join(user.role_names) or "user"),
                html.td(*actions, class_="table-actions"),
            )
        )
    total = (
        total_users
        if total_users is not None
        else max(0, (page_count - 1) * page_size + len(users))
    )
    base = _filter_base_path("/admin/users", q=query, status=status_filter)
    return html.div(
        html.div(
            html.table(
                html.thead(
                    html.tr(
                        html.th("Account"),
                        html.th("Status"),
                        html.th("Roles"),
                        html.th("Actions"),
                    )
                ),
                html.tbody(*rows)
                if rows
                else html.tbody(html.tr(html.td("No users found.", colspan="4"))),
            ),
            class_="table-wrap",
        ),
        hedron_pagination(
            page=page,
            page_size=page_size,
            total=total,
            base_path=base,
            target="#user-directory-body",
        ),
        id="user-directory-body",
    )


def user_directory(
    users: list[User],
    *,
    csrf_token: str,
    query: str,
    status_filter: str,
    page: int,
    page_count: int,
    total_users: int | None = None,
    page_size: int = 50,
    success: str = "",
) -> object:
    return html.div(
        alert_box(success, kind="success"),
        html.form(
            html.label("Search", for_="user-query"),
            html.input(id="user-query", name="q", value=query, placeholder="email or name"),
            html.label("Status", for_="status-filter"),
            html.select(
                html.option("All", value="", selected=not status_filter),
                *[
                    html.option(item.value, value=item.value, selected=status_filter == item.value)
                    for item in UserStatus
                ],
                id="status-filter",
                name="status",
            ),
            html.button("Filter", class_="button button-secondary button-small", type="submit"),
            class_="filter-form",
            action=form_action("admin/users"),
            method="get",
            aria={"label": "Filter users"},
            **hx_attrs(
                method="get",
                path="admin/users",
                trigger="submit, input changed delay:350ms",
                target="#user-directory",
                push_url=True,
                sync="this:replace",
                disabled_elt="find button",
                indicator=INDICATOR,
            ),
        ),
        user_table(
            users,
            csrf_token=csrf_token,
            query=query,
            status_filter=status_filter,
            page=page,
            page_count=page_count,
            total_users=total_users,
            page_size=page_size,
        ),
        id="user-directory",
    )


def invitation_panel(
    invitations: list[Invitation],
    roles: list[Role],
    *,
    csrf_token: str,
    error: str = "",
    success: str = "",
    field_errors: dict[str, str] | None = None,
) -> object:
    field_errors = field_errors or {}
    pending_rows = []
    for invitation in invitations:
        if invitation.accepted_at:
            pill = ("accepted", "pill-active")
        elif invitation.revoked_at:
            pill = ("revoked", "pill-muted")
        else:
            pill = ("pending", "pill-pending")
        actions = [html.span(pill[0], class_=f"pill {pill[1]}")]
        if not invitation.accepted_at and not invitation.revoked_at:
            dialog_id = f"revoke-invite-{invitation.id}"
            actions.append(
                html.div(
                    html.button(
                        "Revoke",
                        class_="button button-danger button-small",
                        type="button",
                        data={"hedron-dialog-open": f"#{dialog_id}"},
                    ),
                    Dialog(
                        "Revoke invitation",
                        html.p(f"Revoke the invitation for {invitation.email_original}?"),
                        html.form(
                            html.input(type="hidden", name="csrf_token", value=csrf_token),
                            html.button(
                                "Revoke invitation",
                                class_="button button-danger",
                                type="submit",
                            ),
                            action=form_action(f"admin/invitations/{invitation.id}/revoke"),
                            method="post",
                            **hx_attrs(
                                path=f"admin/invitations/{invitation.id}/revoke",
                                target="#invitation-panel",
                                sync="#invitation-panel:drop",
                                disabled_elt="find button",
                                indicator=INDICATOR,
                            ),
                        ),
                        id=dialog_id,
                        open=False,
                    ),
                )
            )
        pending_rows.append(
            html.div(
                html.div(
                    html.strong(invitation.email_original),
                    html.small(
                        f"{invitation.role_name.title()} · {invitation.created_at.strftime('%b %d')}"
                    ),
                ),
                html.div(*actions, class_="pending-actions"),
                class_="pending-row",
            )
        )
    email_error = field_errors.get("invite_email", "")
    role_error = field_errors.get("invite_role", "")
    top_error = error if error and not field_errors else ""
    email_attrs: dict = {
        "id": "invite_email",
        "name": "email",
        "type": "email",
        "required": True,
    }
    if email_error:
        email_attrs["aria"] = {
            "invalid": "true",
            "describedby": "field-error-invite_email",
        }
    return html.div(
        html.h2("Invitations"),
        html.p("Send a government-email invitation with an initial role."),
        alert_box(top_error),
        alert_box(success, kind="success"),
        html.form(
            html.input(type="hidden", name="csrf_token", value=csrf_token),
            html.label("Government email", for_="invite_email"),
            html.input(**email_attrs),
            field_error("invite_email", email_error),
            html.label("Initial role", for_="invite_role"),
            html.select(
                *[html.option(role.name.title(), value=role.name) for role in roles],
                id="invite_role",
                name="role",
                **(
                    {"aria": {"invalid": "true", "describedby": "field-error-invite_role"}}
                    if role_error
                    else {}
                ),
            ),
            field_error("invite_role", role_error),
            html.button(
                "Send invitation", class_="button button-primary button-wide", type="submit"
            ),
            class_="stack-form",
            action=form_action("admin/invitations"),
            method="post",
            **hx_attrs(
                path="admin/invitations",
                target="#invitation-panel",
                sync="#invitation-panel:drop",
                disabled_elt="find button",
                indicator=INDICATOR,
            ),
        ),
        html.div(*pending_rows, class_="pending-list")
        if pending_rows
        else html.p("No invitations yet."),
        id="invitation-panel",
        class_="panel",
    )


def audit_match_count(total: int, *, oob: bool = False) -> object:
    attrs: dict = {"id": "audit-match-count", "class_": "verification-badge"}
    if oob:
        attrs["hx-swap-oob"] = "outerHTML"
    return html.span(f"{total} matching events", **attrs)


def audit_results(
    events: list[AuditEvent],
    *,
    event_type_filter: str,
    outcome_filter: str,
    current_page: int,
    page_count: int,
    total_events: int,
    page_size: int = 50,
) -> object:
    return html.div(
        _audit_filter_form(event_type_filter, outcome_filter),
        audit_results_body(
            events,
            event_type_filter=event_type_filter,
            outcome_filter=outcome_filter,
            current_page=current_page,
            page_count=page_count,
            total_events=total_events,
            page_size=page_size,
        ),
        id="audit-results-region",
    )


def _audit_filter_form(event_type_filter: str, outcome_filter: str) -> object:
    return html.form(
        html.div(
            html.label("Event type", for_="event-type-filter"),
            html.input(
                id="event-type-filter",
                name="event_type",
                value=event_type_filter,
                placeholder="auth.login",
                autocomplete="off",
            ),
        ),
        html.div(
            html.label("Outcome", for_="outcome-filter"),
            html.input(
                id="outcome-filter",
                name="outcome",
                value=outcome_filter,
                placeholder="success",
                autocomplete="off",
            ),
        ),
        html.button("Apply filters", class_="button button-secondary button-small", type="submit"),
        (
            html.a(
                "Clear",
                class_="button button-quiet button-small",
                href=page_href("admin/audit"),
                **hx_attrs(
                    method="get",
                    path="admin/audit",
                    target="#audit-results-region",
                    push_url=True,
                    indicator=INDICATOR,
                ),
            )
            if event_type_filter or outcome_filter
            else html.div()
        ),
        class_="filter-form",
        action=form_action("admin/audit"),
        method="get",
        aria={"label": "Filter audit events"},
        **hx_attrs(
            method="get",
            path="admin/audit",
            trigger="submit, input changed delay:350ms",
            target="#audit-results-region",
            push_url=True,
            sync="this:replace",
            disabled_elt="find button",
            indicator=INDICATOR,
        ),
    )


def audit_results_body(
    events: list[AuditEvent],
    *,
    event_type_filter: str,
    outcome_filter: str,
    current_page: int,
    page_count: int,
    total_events: int,
    page_size: int = 50,
) -> object:
    rows = [
        html.tr(
            html.td(event.occurred_at.strftime("%Y-%m-%d %H:%M")),
            html.td(event.event_type),
            html.td(event.outcome),
            html.td(event.source_ip or ""),
            html.td((event.detail or "")[:120]),
        )
        for event in events
    ]
    base = _filter_base_path(
        "/admin/audit",
        event_type=event_type_filter,
        outcome=outcome_filter,
    )
    return html.div(
        html.div(
            html.table(
                html.thead(
                    html.tr(
                        html.th("When"),
                        html.th("Event"),
                        html.th("Outcome"),
                        html.th("Source"),
                        html.th("Detail"),
                    )
                ),
                html.tbody(*rows)
                if rows
                else html.tbody(
                    html.tr(html.td(f"No events ({total_events} total).", colspan="5"))
                ),
            ),
            class_="table-wrap",
        ),
        hedron_pagination(
            page=current_page,
            page_size=page_size,
            total=total_events,
            base_path=base,
            target="#audit-results-body",
        ),
        id="audit-results-body",
    )


def audit_results_error(
    message: str = "Could not load audit activity.",
    *,
    event_type: str = "",
    outcome: str = "",
    page: int = 1,
) -> object:
    params: dict[str, str] = {}
    if event_type:
        params["event_type"] = event_type
    if outcome:
        params["outcome"] = outcome
    if page > 1:
        params["page"] = str(page)
    path = "/admin/audit" + (f"?{urlencode(params)}" if params else "")
    return html.div(
        ErrorState(message, retry_href=path, target="#audit-results-region"),
        id="audit-results-region",
        data={"lazy-error": "audit-results-region"},
    )


def audit_results_lazy(*, event_type: str = "", outcome: str = "", page: int = 1) -> object:
    params: dict[str, str] = {}
    if event_type:
        params["event_type"] = event_type
    if outcome:
        params["outcome"] = outcome
    if page > 1:
        params["page"] = str(page)
    path = "/admin/audit" + (f"?{urlencode(params)}" if params else "")
    return Lazy(
        ref=ComponentRef(
            logical_id="audit-results",
            path=path,
            swap="outerHTML",
        ),
        placeholder=Loading("Loading audit activity…"),
        target_id="audit-results-region",
    )


def profile_response(auth: AuthContext, *, csrf_token: str, success: str) -> list[object]:
    return [
        profile_form(auth, csrf_token=csrf_token, success=success),
        account_summary(auth, csrf_token=csrf_token, oob=True),
        profile_identity(auth, oob=True),
    ]


def request_error(message: str) -> object:
    return alert_box(message or "The request could not be completed.")
