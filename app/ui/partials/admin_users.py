"""Admin user directory and invitation fragments."""

from __future__ import annotations

from hedron import Dialog, html
from hedron_core import HtmlAttrValue, NodeLike

from app.models import Invitation, Role, User, UserStatus
from app.ui.layout import INDICATOR, alert_box
from app.ui.partials.shared import _filter_base_path, field_error, hedron_pagination
from app.ui.urls import form_action, hx_attrs


def user_match_count(total: int, *, oob: bool = False) -> NodeLike:
    attrs: dict[str, HtmlAttrValue] = {"id": "user-match-count", "class_": "verification-badge"}
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
) -> NodeLike:
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
) -> NodeLike:
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
) -> NodeLike:
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
    email_attrs: dict[str, HtmlAttrValue] = {
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
