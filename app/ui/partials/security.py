"""Security page fragments."""

from __future__ import annotations

from typing import Any

from fastapi import Request
from hedron import (
    Badge,
    ComponentRef,
    Dialog,
    ErrorState,
    Form,
    FormField,
    Heading,
    Lazy,
    Loading,
    RefreshButton,
    Section,
    Tabs,
    Text,
    TextInput,
    html,
)
from hedron_core import Component, HtmlAttrValue, NodeLike

from app.dependencies import AuthContext
from app.models import AuditEvent, RefreshSession
from app.services.secrets import SecretProvider
from app.ui.forms import csrf_hidden, submit_button
from app.ui.layout import INDICATOR, alert_box
from app.ui.regions import SECURITY_ACTIVITY
from app.ui.urls import form_action, hx_attrs, mounted_path, page_href


def _password_field(
    label: str,
    field_id: str,
    *,
    autocomplete: str,
    error: str = "",
) -> NodeLike:
    return html.div(
        FormField(
            name=field_id,
            label=label,
            id=field_id,
            required=True,
            error=error or None,
            control=TextInput(
                field_id,
                id=field_id,
                type="password",
                autocomplete=autocomplete,
                required=True,
            ),
        ),
        html.button(
            "Show password",
            class_="password-toggle",
            type="button",
            data={"password-toggle": field_id},
            aria={"pressed": "false"},
        ),
        class_="password-field",
    )


def password_form(
    request: Request,
    *,
    csrf_token: str,
    error: str = "",
    success: str = "",
    field_errors: dict[str, str] | None = None,
) -> Component[Any]:
    field_errors = field_errors or {}
    if success:
        return Section(
            alert_box(success, kind="success"),
            html.a(
                "Return to sign in",
                class_="button button-primary",
                href=page_href(request, "/login"),
            ),
            id="password-form-region",
            class_="form-column",
        )
    top_error = error if error and not field_errors else ""
    return Section(
        alert_box(top_error),
        Form(
            csrf_hidden(csrf_token),
            _password_field(
                "Current password",
                "current_password",
                autocomplete="current-password",
                error=field_errors.get("current_password", ""),
            ),
            _password_field(
                "New password",
                "new_password",
                autocomplete="new-password",
                error=field_errors.get("new_password", ""),
            ),
            _password_field(
                "Confirm new password",
                "new_password_confirm",
                autocomplete="new-password",
                error=field_errors.get("new_password_confirm", ""),
            ),
            submit_button("Change password"),
            class_="stack-form",
            action=form_action(request, "security/password"),
            method="post",
            **hx_attrs(
                request,
                path="security/password",
                target="#password-form-region",
                sync="this:drop",
                indicator=INDICATOR,
            ),
        ),
        id="password-form-region",
        class_="form-column",
    )


def secret_slot(
    request: Request,
    provider: SecretProvider,
    secret,
    *,
    csrf_token: str,
    error: str = "",
    success: str = "",
) -> Component[Any]:
    configured = secret is not None
    if configured:
        metadata = (
            f"Saved {secret.updated_at.strftime('%b %d, %Y at %H:%M')}. "
            "The stored value cannot be revealed."
        )
    else:
        metadata = f"No {provider.label} token is available to your runs."
    return Section(
        html.div(
            html.div(
                html.span(provider.mark, class_="secret-provider-mark", aria={"hidden": "true"}),
                html.div(
                    Heading(provider.label, level=3),
                    html.code(provider.environment_variable),
                ),
            ),
            Badge(
                "Configured" if configured else "Not configured",
                tone="success" if configured else "neutral",
            ),
            class_="secret-card-heading",
        ),
        alert_box(error),
        alert_box(success, kind="success"),
        html.p(metadata, class_="secret-metadata"),
        Form(
            csrf_hidden(csrf_token),
            FormField(
                name="token",
                label=f"{provider.label} API token",
                id=f"{provider.name}-token",
                control=TextInput(
                    "token",
                    id=f"{provider.name}-token",
                    type="password",
                    autocomplete="new-password",
                    required=True,
                    placeholder=f"Paste {provider.label} API token",
                ),
            ),
            html.button(
                "Replace" if configured else "Save",
                class_="button button-primary button-small button-action",
                type="submit",
            ),
            class_="secret-form",
            action=form_action(request, f"security/secrets/{provider.name}"),
            method="post",
            **hx_attrs(
                request,
                path=f"security/secrets/{provider.name}",
                target=f"#secret-slot-{provider.name}",
                sync="closest .secret-card:drop",
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
                    Form(
                        csrf_hidden(csrf_token),
                        html.button(
                            "Delete token",
                            class_="button button-danger",
                            type="submit",
                        ),
                        action=form_action(request, f"security/secrets/{provider.name}/delete"),
                        method="post",
                        **hx_attrs(
                            request,
                            path=f"security/secrets/{provider.name}/delete",
                            target=f"#secret-slot-{provider.name}",
                            sync="closest .secret-card:drop",
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
    request: Request,
    sessions: list[RefreshSession],
    *,
    auth: AuthContext,
    csrf_token: str,
) -> Component[Any]:
    rows: list[NodeLike] = []
    for session in sessions:
        is_current = session.id == auth.session.id
        if is_current:
            action_node: NodeLike = Badge("Current", tone="success")
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
                    Form(
                        csrf_hidden(csrf_token),
                        html.button(
                            "Revoke session",
                            class_="button button-danger",
                            type="submit",
                        ),
                        action=form_action(request, f"security/sessions/{session.id}/revoke"),
                        method="post",
                        **hx_attrs(
                            request,
                            path=f"security/sessions/{session.id}/revoke",
                            target="#session-list",
                            sync="#session-list:drop",
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
    return Section(*rows, id="session-list", class_="session-list")


def session_count(sessions: list[RefreshSession], *, oob: bool = False) -> NodeLike:
    attrs: dict[str, HtmlAttrValue] = {"id": "session-count", "class_": "count-badge"}
    if oob:
        attrs["hx-swap-oob"] = "outerHTML"
    return html.span(str(len(sessions)), **attrs)


def security_activity(events: list[AuditEvent], *, oob: bool = False) -> NodeLike:
    attrs: dict[str, HtmlAttrValue] = {"id": "security-activity", "class_": "event-list"}
    if oob:
        attrs["hx-swap-oob"] = "outerHTML"
    items: list[NodeLike] = []
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
    request: Request,
    message: str = "Could not load security activity.",
) -> NodeLike:
    """ErrorState wrapped so Lazy outerHTML swaps keep a stable region id."""
    activity_path = mounted_path(request, "/security/activity")
    return html.div(
        ErrorState(
            message,
            retry_href=activity_path,
            target="#security-activity",
        ),
        id="security-activity",
        class_="event-list",
        data={"lazy-error": "security-activity"},
    )


def security_activity_refresh(request: Request) -> NodeLike:
    return html.div(
        RefreshButton.for_region(
            SECURITY_ACTIVITY,
            href=mounted_path(request, "/security/activity"),
            label="Refresh",
        ),
        class_="lazy-refresh",
    )


def security_activity_lazy(request: Request) -> Lazy:
    """Deferred activity panel loaded via Hedron Lazy after first paint."""
    return Lazy(
        ref=ComponentRef(
            logical_id="security-activity",
            path=mounted_path(request, "/security/activity"),
            swap="outerHTML",
        ),
        placeholder=Loading("Loading activity…"),
        target_id="security-activity",
    )


def security_tabs(
    request: Request,
    *,
    csrf_token: str,
    local_password: bool,
    secret_slots,
    sessions: list[RefreshSession],
    auth: AuthContext,
) -> Tabs:
    panels: list[tuple[str, NodeLike]] = []
    if local_password:
        panels.append(
            (
                "Password",
                Section(
                    html.div(
                        Heading("Change password", level=2),
                        Text(
                            "Changing your password signs out every active session, including this one."
                        ),
                    ),
                    password_form(request, csrf_token=csrf_token),
                    class_="panel split-panel",
                ),
            )
        )
    panels.append(
        (
            "Tokens",
            Section(
                html.div(
                    Heading("API tokens", level=2),
                    Text(
                        "Add only an approved service token. Saved values are encrypted and cannot be viewed again."
                    ),
                    class_="panel-heading",
                ),
                html.div(
                    *[secret_slot(request, p, s, csrf_token=csrf_token) for p, s in secret_slots],
                    class_="secret-grid",
                ),
                class_="panel",
            ),
        )
    )
    panels.append(
        (
            "Sessions",
            Section(
                html.div(
                    html.div(
                        Heading("Active sessions", level=2),
                        Text("Revoke any browser or client you no longer recognize."),
                    ),
                    session_count(sessions),
                    class_="panel-heading",
                ),
                session_list(request, sessions, auth=auth, csrf_token=csrf_token),
                class_="panel",
            ),
        )
    )
    panels.append(
        (
            "Activity",
            Section(
                html.div(
                    html.div(
                        Heading("Recent security activity", level=2),
                        Text("Latest events associated with your account."),
                    ),
                    security_activity_refresh(request),
                    class_="panel-heading",
                ),
                security_activity_lazy(request),
                class_="panel",
            ),
        )
    )
    return Tabs(*panels, id="security-tabs")
