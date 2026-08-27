"""Admin user directory and invitation fragments."""

from __future__ import annotations

from typing import Any

from fastapi import Request
from hedron import (
    ActionGroup,
    Badge,
    Button,
    Card,
    Dialog,
    Form,
    FormField,
    FormGrid,
    ResourceList,
    ResourceRow,
    Section,
    Select,
    Stack,
    StateView,
    Table,
    TableColumn,
    Text,
    TextInput,
    html,
)
from hedron_core import Component, HtmlAttrValue, NodeLike

from app.models import Invitation, Role, User, UserStatus
from app.ui.design_system import DataMoverPageHeader as PageHeader
from app.ui.design_system import apply_data_recipe
from app.ui.forms import csrf_hidden, hidden_field, submit_button
from app.ui.layout import INDICATOR, alert_box
from app.ui.partials.shared import _filter_base_path, hedron_pagination
from app.ui.urls import form_action, hx_attrs


def user_match_count(total: int, *, oob: bool = False) -> NodeLike:
    attrs: dict[str, HtmlAttrValue] = {"id": "user-match-count"}
    if oob:
        attrs["hx-swap-oob"] = "outerHTML"
    noun = "account" if total == 1 else "accounts"
    return html.span(Badge(f"{total} matching {noun}", tone="info"), **attrs)


def user_table(
    request: Request,
    users: list[User],
    *,
    csrf_token: str,
    query: str,
    status_filter: str,
    page: int,
    page_count: int,
    total_users: int | None = None,
    page_size: int = 50,
) -> Component[Any]:
    """Directory body with Hedron Table; action cells keep Form/Dialog nodes."""
    rows: list[list[NodeLike]] = []
    for user in users:
        actions: list[NodeLike] = []
        if user.status == UserStatus.PENDING.value:
            for action, label in (("approve", "Approve"), ("deny", "Deny")):
                actions.append(
                    Form(
                        csrf_hidden(csrf_token),
                        hidden_field("q", query),
                        hidden_field("status", status_filter),
                        hidden_field("page", str(page)),
                        Button(label, size="sm", type="submit"),
                        action=form_action(request, f"admin/users/{user.id}/{action}"),
                        method="post",
                        **hx_attrs(
                            request,
                            path=f"admin/users/{user.id}/{action}",
                            target="#user-directory-body",
                            include="closest form",
                            indicator=INDICATOR,
                        ),
                    )
                )
        else:
            is_active = user.status == UserStatus.ACTIVE.value
            action_label = "Disable" if is_active else "Enable"
            dialog_id = f"toggle-user-{user.id}"
            toggle_form = Form(
                csrf_hidden(csrf_token),
                hidden_field("q", query),
                hidden_field("status", status_filter),
                hidden_field("page", str(page)),
                Button(
                    action_label,
                    size="sm",
                    type="submit",
                ),
                action=form_action(request, f"admin/users/{user.id}/toggle"),
                method="post",
                **hx_attrs(
                    request,
                    path=f"admin/users/{user.id}/toggle",
                    target="#user-directory-body",
                    indicator=INDICATOR,
                ),
            )
            if is_active:
                actions.append(
                    html.div(
                        Button(
                            action_label,
                            type="button",
                            variant="secondary",
                            size="sm",
                            attrs={"data-hedron-dialog-open": f"#{dialog_id}"},
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
            [
                Stack(
                    Text(user.email_original, role="label", overflow="truncate"),
                    Text(
                        user.full_name or "Name not provided",
                        role="caption",
                        overflow="truncate",
                    ),
                    gap="xs",
                ),
                Badge(
                    user.status,
                    tone=(
                        "success"
                        if user.status == UserStatus.ACTIVE.value
                        else "warning"
                        if user.status == UserStatus.PENDING.value
                        else "neutral"
                    ),
                ),
                ", ".join(user.role_names) or "user",
                ActionGroup(
                    *actions,
                    gap="xs",
                    collapse="never",
                ),
            ]
        )
    total = (
        total_users
        if total_users is not None
        else max(0, (page_count - 1) * page_size + len(users))
    )
    base = _filter_base_path(request, "/admin/users", q=query, status=status_filter)
    columns = [
        TableColumn(header="Account", size="wide"),
        TableColumn(header="Status", size="narrow"),
        TableColumn(header="Roles"),
        TableColumn(header="Actions", align="end", size="narrow"),
    ]
    table: NodeLike
    if rows:
        table = apply_data_recipe(
            Table(
                rows=rows,
                columns=columns,
                density="compact",
                sticky_header=True,
                zebra=True,
            )
        )
    else:
        table = html.div(
            apply_data_recipe(
                Table(
                    rows=[],
                    columns=columns,
                    density="compact",
                    sticky_header=True,
                )
            ),
            StateView(
                "No users found.",
                kind="empty",
                description="Adjust the search or status filter to broaden the directory.",
            ),
        )
    return Card(
        table,
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
    request: Request,
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
) -> Component[Any]:
    status_options = [("", "All"), *[(item.value, item.value) for item in UserStatus]]
    return Section(
        alert_box(success, kind="success"),
        Form(
            FormGrid(
                FormField(
                    name="q",
                    label="Search",
                    id="user-query",
                    control=TextInput(
                        "q",
                        id="user-query",
                        value=query,
                        placeholder="email or name",
                        type="search",
                    ),
                ),
                FormField(
                    name="status",
                    label="Status",
                    id="status-filter",
                    control=Select(
                        "status",
                        status_options,
                        id="status-filter",
                        value=status_filter or None,
                    ),
                ),
                columns=2,
                gap="sm",
            ),
            ActionGroup(submit_button("Filter", variant="secondary", size="sm"), align="end"),
            action=form_action(request, "admin/users"),
            method="get",
            aria={"label": "Filter users"},
            **hx_attrs(
                request,
                method="get",
                path="admin/users",
                trigger="submit, input changed delay:350ms",
                target="#user-directory",
                push_url=True,
                sync="this:replace",
                indicator=INDICATOR,
            ),
        ),
        user_table(
            request,
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
    request: Request,
    invitations: list[Invitation],
    roles: list[Role],
    *,
    csrf_token: str,
    error: str = "",
    success: str = "",
    field_errors: dict[str, str] | None = None,
) -> Component[Any]:
    field_errors = field_errors or {}
    pending_rows: list[NodeLike] = []
    for invitation in invitations:
        if invitation.accepted_at:
            pill = Badge("accepted", tone="success")
        elif invitation.revoked_at:
            pill = Badge("revoked", tone="neutral")
        else:
            pill = Badge("pending", tone="warning")
        actions: list[NodeLike] = []
        if not invitation.accepted_at and not invitation.revoked_at:
            dialog_id = f"revoke-invite-{invitation.id}"
            actions.append(
                html.div(
                    Button(
                        "Revoke",
                        type="button",
                        variant="danger",
                        size="sm",
                        attrs={"data-hedron-dialog-open": f"#{dialog_id}"},
                    ),
                    Dialog(
                        "Revoke invitation",
                        html.p(f"Revoke the invitation for {invitation.email_original}?"),
                        Form(
                            csrf_hidden(csrf_token),
                            Button(
                                "Revoke invitation",
                                variant="danger",
                                type="submit",
                            ),
                            action=form_action(
                                request, f"admin/invitations/{invitation.id}/revoke"
                            ),
                            method="post",
                            **hx_attrs(
                                request,
                                path=f"admin/invitations/{invitation.id}/revoke",
                                target="#invitation-panel",
                                sync="#invitation-panel:drop",
                                indicator=INDICATOR,
                            ),
                        ),
                        id=dialog_id,
                        open=False,
                    ),
                )
            )
        pending_rows.append(
            ResourceRow(
                invitation.email_original,
                description=(
                    f"{invitation.role_name.title()} access · Sent "
                    f"{invitation.created_at.strftime('%b %d, %Y')}"
                ),
                meta=pill,
                actions=(
                    ActionGroup(*actions, align="end", gap="xs", collapse="never")
                    if actions
                    else None
                ),
                density="comfortable",
            )
        )
    email_error = field_errors.get("invite_email", "")
    role_error = field_errors.get("invite_role", "")
    top_error = error if error and not field_errors else ""
    return Section(
        PageHeader(
            "Invite a teammate",
            eyebrow="Provision access",
            description="Send a government-email invitation with an initial role.",
            level=2,
            density="compact",
            meta=Badge(f"{len(pending_rows)} sent", tone="neutral"),
        ),
        alert_box(top_error),
        alert_box(success, kind="success"),
        Form(
            csrf_hidden(csrf_token),
            FormField(
                name="email",
                label="Government email",
                id="invite_email",
                required=True,
                error=email_error or None,
                control=TextInput(
                    "email",
                    id="invite_email",
                    type="email",
                    required=True,
                ),
            ),
            FormField(
                name="role",
                label="Initial role",
                id="invite_role",
                required=True,
                error=role_error or None,
                control=Select(
                    "role",
                    [(role.name, role.name.title()) for role in roles],
                    id="invite_role",
                    required=True,
                    value=(
                        "user"
                        if any(role.name == "user" for role in roles)
                        else roles[0].name
                        if roles
                        else None
                    ),
                ),
            ),
            Button("Send invitation", width="full", type="submit"),
            action=form_action(request, "admin/invitations"),
            method="post",
            **hx_attrs(
                request,
                path="admin/invitations",
                target="#invitation-panel",
                sync="#invitation-panel:drop",
                indicator=INDICATOR,
            ),
        ),
        ResourceList(
            *pending_rows,
            label="Invitation history",
            density="comfortable",
        )
        if pending_rows
        else StateView(
            "No invitations yet.",
            kind="empty",
            description="Sent invitations and their current status will appear here.",
        ),
        id="invitation-panel",
    )
