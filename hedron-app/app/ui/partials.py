"""UI fragment builders for Access Registry Hedron port."""

from __future__ import annotations

from urllib.parse import urlencode

from hedron import html

from app.dependencies import AuthContext
from app.models import AuditEvent, Invitation, RefreshSession, Role, User, UserStatus
from app.services.secrets import SecretProvider
from app.ui.layout import account_summary, alert_box
from app.ui.urls import form_action, hx_attrs, page_href


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
        user.last_login_at.strftime("%b %d, %Y %H:%M")
        if user.last_login_at
        else "First session"
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


def _password_field(label: str, field_id: str, *, autocomplete: str, minlength: str | None = None) -> list[object]:
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
    ]


def password_form(*, csrf_token: str, error: str = "", success: str = "") -> object:
    if success:
        return html.div(
            alert_box(success, kind="success"),
            html.a("Return to sign in", class_="button button-primary", href=page_href("/login")),
            id="password-form-region",
            class_="form-column",
        )
    return html.div(
        alert_box(error),
        html.form(
            html.input(type="hidden", name="csrf_token", value=csrf_token),
            *_password_field("Current password", "current_password", autocomplete="current-password"),
            *_password_field("New password", "new_password", autocomplete="new-password", minlength="15"),
            *_password_field(
                "Confirm new password", "new_password_confirm", autocomplete="new-password", minlength="15"
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
            html.label(f"{provider.label} API token", for_=f"{provider.name}-token", class_="sr-only"),
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
                class_="button button-primary button-small",
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
            ),
        ),
        (
            html.form(
                html.input(type="hidden", name="csrf_token", value=csrf_token),
                html.button(
                    "Delete token",
                    class_="button button-danger button-small",
                    type="submit",
                ),
                class_="secret-delete-form",
                action=form_action(f"security/secrets/{provider.name}/delete"),
                method="post",
                **hx_attrs(
                    path=f"security/secrets/{provider.name}/delete",
                    target=f"#secret-slot-{provider.name}",
                    sync="closest .secret-card:drop",
                    disabled_elt="find button",
                    confirm=(
                        f"Delete your {provider.label} API token? "
                        "Runs using it will stop working."
                    ),
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
            action_node = html.form(
                html.input(type="hidden", name="csrf_token", value=csrf_token),
                html.button(
                    "Revoke",
                    class_="button button-danger button-small",
                    type="submit",
                ),
                action=form_action(f"security/sessions/{session.id}/revoke"),
                method="post",
                **hx_attrs(
                    path=f"security/sessions/{session.id}/revoke",
                    target="#session-list",
                    sync="#session-list:drop",
                    disabled_elt="find button",
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
                        html.button(label, class_="button button-small", type="submit"),
                        action=form_action(f"admin/users/{user.id}/{action}"),
                        method="post",
                        **hx_attrs(
                            path=f"admin/users/{user.id}/{action}",
                            target="#user-table",
                            include="closest form",
                            disabled_elt="find button",
                        ),
                    )
                )
        else:
            actions.append(
                html.form(
                    html.input(type="hidden", name="csrf_token", value=csrf_token),
                    html.input(type="hidden", name="q", value=query),
                    html.input(type="hidden", name="status", value=status_filter),
                    html.input(type="hidden", name="page", value=str(page)),
                    html.button(
                        "Disable" if user.status == UserStatus.ACTIVE.value else "Enable",
                        class_="button button-small",
                        type="submit",
                    ),
                    action=form_action(f"admin/users/{user.id}/toggle"),
                    method="post",
                    **hx_attrs(
                        path=f"admin/users/{user.id}/toggle",
                        target="#user-table",
                        disabled_elt="find button",
                    ),
                )
            )
        rows.append(
            html.tr(
                html.td(html.strong(user.email_original), html.small(user.full_name or "")),
                html.td(user.status),
                html.td(", ".join(user.role_names) or "user"),
                html.td(*actions, class_="table-actions"),
            )
        )
    pagination = []
    params = {"q": query, "status": status_filter}
    if page > 1:
        prev_q = urlencode({**params, "page": page - 1})
        pagination.append(
            html.a(
                "Previous",
                class_="button button-quiet button-small",
                href=page_href(f"admin/users?{prev_q}"),
                **hx_attrs(
                    method="get",
                    path=f"admin/users?{prev_q}",
                    target="#user-directory",
                    push_url=True,
                ),
            )
        )
    pagination.append(html.span(f"Page {page} of {page_count}"))
    if page < page_count:
        next_q = urlencode({**params, "page": page + 1})
        pagination.append(
            html.a(
                "Next",
                class_="button button-quiet button-small",
                href=page_href(f"admin/users?{next_q}"),
                **hx_attrs(
                    method="get",
                    path=f"admin/users?{next_q}",
                    target="#user-directory",
                    push_url=True,
                ),
            )
        )
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
                html.tbody(*rows) if rows else html.tbody(html.tr(html.td("No users found.", colspan="4"))),
            ),
            class_="table-wrap",
        ),
        html.nav(*pagination, class_="pagination", aria={"label": "User result pages"})
        if page_count > 1
        else html.div(),
        id="user-table",
    )


def user_directory(
    users: list[User],
    *,
    csrf_token: str,
    query: str,
    status_filter: str,
    page: int,
    page_count: int,
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
            ),
        ),
        user_table(
            users,
            csrf_token=csrf_token,
            query=query,
            status_filter=status_filter,
            page=page,
            page_count=page_count,
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
) -> object:
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
            actions.append(
                html.form(
                    html.input(type="hidden", name="csrf_token", value=csrf_token),
                    html.button("Revoke", class_="button button-danger button-small", type="submit"),
                    action=form_action(f"admin/invitations/{invitation.id}/revoke"),
                    method="post",
                    **hx_attrs(
                        path=f"admin/invitations/{invitation.id}/revoke",
                        target="#invitation-panel",
                        sync="#invitation-panel:drop",
                        disabled_elt="find button",
                        confirm=f"Revoke the invitation for {invitation.email_original}?",
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
    return html.div(
        html.h2("Invitations"),
        html.p("Send a government-email invitation with an initial role."),
        alert_box(error),
        alert_box(success, kind="success"),
        html.form(
            html.input(type="hidden", name="csrf_token", value=csrf_token),
            html.label("Government email", for_="invite_email"),
            html.input(id="invite_email", name="email", type="email", required=True),
            html.label("Initial role", for_="invite_role"),
            html.select(
                *[html.option(role.name.title(), value=role.name) for role in roles],
                id="invite_role",
                name="role",
            ),
            html.button("Send invitation", class_="button button-primary button-wide", type="submit"),
            class_="stack-form",
            action=form_action("admin/invitations"),
            method="post",
            **hx_attrs(
                path="admin/invitations",
                target="#invitation-panel",
                sync="#invitation-panel:drop",
                disabled_elt="find button",
            ),
        ),
        html.div(*pending_rows, class_="pending-list") if pending_rows else html.p("No invitations yet."),
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
    params = {"event_type": event_type_filter, "outcome": outcome_filter}
    pagination = []
    if current_page > 1:
        prev_q = urlencode({**params, "page": current_page - 1})
        pagination.append(
            html.a(
                "Previous",
                class_="button button-quiet button-small",
                href=page_href(f"admin/audit?{prev_q}"),
                **hx_attrs(
                    method="get",
                    path=f"admin/audit?{prev_q}",
                    target="#audit-results-region",
                    push_url=True,
                ),
            )
        )
    pagination.append(html.span(f"Page {current_page} of {page_count}"))
    if current_page < page_count:
        next_q = urlencode({**params, "page": current_page + 1})
        pagination.append(
            html.a(
                "Next",
                class_="button button-quiet button-small",
                href=page_href(f"admin/audit?{next_q}"),
                **hx_attrs(
                    method="get",
                    path=f"admin/audit?{next_q}",
                    target="#audit-results-region",
                    push_url=True,
                ),
            )
        )
    return html.div(
        html.form(
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
            ),
        ),
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
                else html.tbody(html.tr(html.td(f"No events ({total_events} total).", colspan="5"))),
            ),
            class_="table-wrap",
        ),
        html.nav(*pagination, class_="pagination", aria={"label": "Audit result pages"})
        if page_count > 1
        else html.div(),
        id="audit-results-region",
    )


def profile_response(auth: AuthContext, *, csrf_token: str, success: str) -> list[object]:
    return [
        profile_form(auth, csrf_token=csrf_token, success=success),
        account_summary(auth, csrf_token=csrf_token, oob=True),
        profile_identity(auth, oob=True),
    ]


def request_error(message: str) -> object:
    return alert_box(message or "The request could not be completed.")
