"""Security page fragments."""

from __future__ import annotations

from typing import Any

from fastapi import Request
from hedron import (
    ActionGroup,
    Alert,
    Badge,
    Button,
    Card,
    ComponentRef,
    Dialog,
    ErrorState,
    Form,
    FormField,
    FormGrid,
    Grid,
    Heading,
    Lazy,
    LinkButton,
    Loading,
    Section,
    SplitView,
    StateView,
    Tabs,
    Text,
    TextInput,
    Timeline,
    html,
)
from hedron_core import Component, HtmlAttrValue, NodeLike

from app.dependencies import AuthContext
from app.models import AuditEvent, RefreshSession
from app.services.catalogs import require_catalog_provider
from app.services.secrets import CredentialField, SecretProvider
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
            LinkButton("Return to sign in", href=page_href(request, "/login")),
            id="password-form-region",
            class_="form-column",
        )
    top_error = error if error and not field_errors else ""
    return Section(
        alert_box(top_error),
        Alert(
            "Password requirements: use 15–128 characters; avoid your email and common passwords.",
            tone="info",
        ),
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
            action=form_action(request, "profile/password"),
            method="post",
            **hx_attrs(
                request,
                path="profile/password",
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
        validation_message = secret.validation_message.rstrip(".")
        metadata = (
            f"Credentials saved {secret.updated_at.strftime('%b %d, %Y at %H:%M')}. "
            f"{validation_message}. Encrypted values cannot be revealed."
        )
    else:
        metadata = f"No {provider.label} credentials are available to your runs."

    def credential_field(field: CredentialField) -> NodeLike:
        field_id = f"{provider.name}-{field.name}"
        if field.options:
            control = html.select(
                *[
                    html.option(
                        option,
                        value=option,
                        selected=option == field.default,
                    )
                    for option in field.options
                ],
                id=field_id,
                name=field.name,
                required=field.required,
                autocomplete=field.autocomplete,
            )
        else:
            control = html.input(
                id=field_id,
                name=field.name,
                type=field.input_type,
                autocomplete=field.autocomplete,
                required=field.required,
                placeholder=field.placeholder,
                value=field.default,
                spellcheck="false",
            )
        return html.div(
            html.label(
                field.label,
                html.span("Required" if field.required else "Optional"),
                for_=field_id,
            ),
            control,
            class_=(
                f"credential-field credential-field-{field.name} "
                f"{'is-wide' if field.name == 'endpoint' else ''}"
            ).strip(),
        )

    return Section(
        html.div(
            html.span(provider.mark, class_="secret-provider-mark", aria={"hidden": "true"}),
            html.div(
                Heading(provider.label, level=3),
                html.code(provider.environment_variable),
                class_="secret-card-identity",
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
            FormGrid(
                *[credential_field(field) for field in provider.fields],
                columns={"base": 1, "sm": 2},
                gap="0.6875rem",
                class_="credential-fields",
            ),
            html.div(
                Button(
                    "Replace credentials" if configured else "Save connection",
                    class_="button-small button-action",
                    type="submit",
                ),
                (
                    html.button(
                        "Delete connection",
                        class_="hedron-button hedron-button-danger button-small",
                        type="button",
                        data={"hedron-dialog-open": f"#delete-secret-{provider.name}"},
                    )
                    if configured
                    else None
                ),
                class_="secret-card-actions",
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
            Dialog(
                f"Delete {provider.label} connection",
                html.p(
                    f"Delete your {provider.label} credentials? Runs using them will stop working."
                ),
                Form(
                    csrf_hidden(csrf_token),
                    Button("Delete connection", variant="danger", type="submit"),
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
            )
            if configured
            else html.div()
        ),
        id=f"secret-slot-{provider.name}",
        class_="secret-card",
    )


def connection_status_list(
    request: Request,
    secret_slots,
    *,
    csrf_token: str,
    oob: bool = False,
) -> NodeLike:
    rows: list[NodeLike] = []
    for provider, secret in secret_slots:
        catalog = require_catalog_provider(provider.name)
        configured = secret is not None
        connected = configured and secret.validation_status == "connected"
        failed = configured and secret.validation_status == "failed"
        status_label = (
            "Connected"
            if connected
            else "Failed"
            if failed
            else "Untested"
            if configured
            else "Not configured"
        )
        status_tone = (
            "success"
            if connected
            else "danger"
            if failed
            else "warning"
            if configured
            else "neutral"
        )
        if configured and secret.validated_at:
            detail = (
                f"{secret.validation_message} · Checked "
                f"{secret.validated_at.strftime('%b %d at %H:%M')}"
            )
        elif configured:
            detail = (
                "Credentials are saved. Test the connection before browsing objects or running."
            )
        else:
            detail = "Add credentials before Data Mover can inspect remote objects."

        actions: list[NodeLike] = []
        if configured:
            actions.append(
                Form(
                    csrf_hidden(csrf_token),
                    Button(
                        "Test connection",
                        variant="secondary",
                        class_="button-small button-action",
                        type="submit",
                    ),
                    action=form_action(request, f"security/secrets/{provider.name}/test"),
                    method="post",
                    **hx_attrs(
                        request,
                        path=f"security/secrets/{provider.name}/test",
                        target="#connection-status-list",
                        sync="#connection-status-list:drop",
                        indicator=INDICATOR,
                    ),
                )
            )

        rows.append(
            html.article(
                html.span(
                    provider.mark,
                    class_="connection-status-mark",
                    aria={"hidden": "true"},
                ),
                html.div(
                    html.strong(provider.label),
                    html.span(catalog.technology),
                    class_="connection-status-identity",
                ),
                Badge(status_label, tone=status_tone),
                html.p(detail),
                ActionGroup(
                    *actions,
                    align="end",
                    gap="0.4375rem",
                    collapse="never",
                    class_="connection-status-actions",
                ),
                class_="connection-status-row",
            )
        )

    attrs: dict[str, HtmlAttrValue] = {
        "id": "connection-status-list",
        "class_": "connection-status-list",
    }
    if oob:
        attrs["hx-swap-oob"] = "outerHTML"
    return html.div(*rows, **attrs)


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
                    class_="hedron-button hedron-button-danger button-small",
                    type="button",
                    data={"hedron-dialog-open": f"#{dialog_id}"},
                ),
                Dialog(
                    "Revoke session",
                    html.p("Revoke this browser session? The device will need to sign in again."),
                    Form(
                        csrf_hidden(csrf_token),
                        Button(
                            "Revoke session",
                            variant="danger",
                            type="submit",
                        ),
                        action=form_action(request, f"profile/sessions/{session.id}/revoke"),
                        method="post",
                        **hx_attrs(
                            request,
                            path=f"profile/sessions/{session.id}/revoke",
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
        rows.append(
            StateView(
                "No active sessions.",
                kind="empty",
                description="New browser and client sessions will appear here.",
            )
        )
    return Section(*rows, id="session-list", class_="session-list")


def session_count(sessions: list[RefreshSession], *, oob: bool = False) -> NodeLike:
    attrs: dict[str, HtmlAttrValue] = {"id": "session-count"}
    if oob:
        attrs["hx-swap-oob"] = "outerHTML"
    return html.span(Badge(str(len(sessions)), tone="info"), **attrs)


def security_activity(
    request: Request | None,
    events: list[AuditEvent],
    *,
    oob: bool = False,
    with_polling: bool = True,
) -> NodeLike:
    attrs: dict[str, HtmlAttrValue] = {"id": "security-activity"}
    if oob:
        attrs["hx-swap-oob"] = "outerHTML"
    if with_polling and request is not None:
        attrs.update(
            hx_attrs(
                request,
                path="profile/activity",
                method="get",
                target="#security-activity",
                swap="outerHTML",
                polling=30,
                indicator=INDICATOR,
            )
        )
    entries: list[tuple[str, str, NodeLike]] = []
    for event in events:
        words = event.event_type.replace(".", " ").replace("_", " ").split()
        title = " ".join(
            word.upper() if word.casefold() in {"api", "csv", "ip", "mfa", "sso"} else word.title()
            for word in words
        )
        entries.append(
            (
                event.occurred_at.strftime("%b %d, %Y at %H:%M"),
                title,
                Badge(
                    event.outcome,
                    tone=(
                        "success"
                        if event.outcome == "success"
                        else "danger"
                        if event.outcome == "failure"
                        else "info"
                    ),
                ),
            )
        )
    content: NodeLike
    if entries:
        content = Timeline(entries, label="Recent security activity")
    else:
        content = StateView(
            "No recent security activity.",
            kind="empty",
            description="Security-relevant account events will appear here.",
        )
    return html.div(content, **attrs)


def security_activity_error(
    request: Request,
    message: str = "Could not load security activity.",
) -> NodeLike:
    """ErrorState wrapped so Lazy outerHTML swaps keep a stable region id."""
    activity_path = mounted_path(request, "/profile/activity")
    return html.div(
        ErrorState(
            message,
            retry_href=activity_path,
            target="#security-activity",
        ),
        id="security-activity",
        data={"lazy-error": "security-activity"},
        **hx_attrs(
            request,
            path="profile/activity",
            method="get",
            target="#security-activity",
            swap="outerHTML",
            polling=30,
            indicator=INDICATOR,
        ),
    )


def security_activity_refresh(request: Request) -> NodeLike:
    return ActionGroup(
        html.button(
            "Refresh",
            type="button",
            class_="hedron-button hedron-button-secondary button-small",
            **hx_attrs(
                request,
                path="/profile/activity",
                method="get",
                target=SECURITY_ACTIVITY.selector,
                swap="outerHTML",
            ),
        ),
        align="end",
    )


def security_activity_lazy(request: Request) -> Lazy:
    """Deferred activity panel loaded via Hedron Lazy after first paint."""
    return Lazy(
        ref=ComponentRef(
            logical_id="security-activity",
            path=mounted_path(request, "/profile/activity"),
            swap="innerHTML",
        ),
        placeholder=Loading("Loading activity…"),
        target_id="security-activity",
    )


def security_tabs(
    request: Request,
    *,
    csrf_token: str,
    secret_slots,
) -> Tabs:
    panels: list[tuple[str, NodeLike]] = [
        (
            "Credentials",
            Card(
                html.div(
                    html.div(
                        Heading("Remote connections", level=2),
                        Text(
                            "Add each system once. Data Mover encrypts every field at rest and never reveals saved plaintext."
                        ),
                    ),
                    class_="panel-heading token-panel-heading",
                ),
                Grid(
                    *[secret_slot(request, p, s, csrf_token=csrf_token) for p, s in secret_slots],
                    columns=2,
                    class_="secret-grid",
                ),
                class_="panel panel-main",
            ),
        )
    ]
    panels.append(
        (
            "Status",
            Card(
                html.div(
                    html.div(
                        Heading("Connection status", level=2),
                        Text(
                            "Testing a connection contacts the configured system. Saving credentials does not run a check."
                        ),
                    ),
                    Badge("Connection checks", tone="info"),
                    class_="panel-heading",
                ),
                connection_status_list(
                    request,
                    secret_slots,
                    csrf_token=csrf_token,
                ),
                class_="panel panel-main",
            ),
        )
    )
    return Tabs(*panels, id="security-tabs")


def account_tabs(
    request: Request,
    *,
    csrf_token: str,
    local_password: bool,
    sessions: list[RefreshSession],
    auth: AuthContext,
    profile_content: NodeLike,
    active: str = "Profile",
    password_error: str = "",
    password_field_errors: dict[str, str] | None = None,
) -> Tabs:
    panels: list[tuple[str, NodeLike]] = [("Profile", profile_content)]
    if local_password:
        panels.append(
            (
                "Password",
                Card(
                    SplitView(
                        primary=html.div(
                            Heading("Change password", level=2),
                            Text(
                                "Changing your password signs out every active session, including this one."
                            ),
                        ),
                        secondary=password_form(
                            request,
                            csrf_token=csrf_token,
                            error=password_error,
                            field_errors=password_field_errors,
                        ),
                        ratio="2:3",
                        gap="3.375rem",
                        collapse="md",
                        class_="split-panel",
                    ),
                    class_="panel panel-main",
                ),
            )
        )
    panels.append(
        (
            "Sessions",
            Card(
                html.div(
                    html.div(
                        Heading("Active sessions", level=2),
                        Text("Revoke any browser or client you no longer recognize."),
                    ),
                    session_count(sessions),
                    class_="panel-heading",
                ),
                session_list(request, sessions, auth=auth, csrf_token=csrf_token),
                class_="panel panel-main",
            ),
        )
    )
    panels.append(
        (
            "Activity",
            Card(
                html.div(
                    html.div(
                        Heading("Recent security activity", level=2),
                        Text("Latest events associated with your account."),
                    ),
                    security_activity_refresh(request),
                    class_="panel-heading",
                ),
                security_activity_lazy(request),
                class_="panel panel-main",
            ),
        )
    )
    panel_names = {name for name, _ in panels}
    return Tabs(
        *panels,
        active=active if active in panel_names else "Profile",
        id="account-tabs",
    )
