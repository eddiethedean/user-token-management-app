"""Security page fragments."""

from __future__ import annotations

from hedron import ComponentRef, Dialog, ErrorState, Lazy, Loading, Tabs, html

from app.dependencies import AuthContext
from app.models import AuditEvent, RefreshSession
from app.services.secrets import SecretProvider
from app.ui.layout import INDICATOR, alert_box
from app.ui.partials.shared import field_error
from app.ui.urls import form_action, hx_attrs, page_href


def _password_field(
    label: str,
    field_id: str,
    *,
    autocomplete: str,
    minlength: str | None = None,
    error: str = "",
) -> list[object]:
    attrs: dict[str, object] = {
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
    attrs: dict[str, object] = {"id": "session-count", "class_": "count-badge"}
    if oob:
        attrs["hx-swap-oob"] = "outerHTML"
    return html.span(str(len(sessions)), **attrs)


def security_activity(events: list[AuditEvent], *, oob: bool = False) -> object:
    attrs: dict[str, object] = {"id": "security-activity", "class_": "event-list"}
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
