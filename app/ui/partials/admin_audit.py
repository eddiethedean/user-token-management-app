"""Admin audit log fragments."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlencode

from hedron import (
    ComponentRef,
    ErrorState,
    Form,
    FormField,
    Lazy,
    Loading,
    Section,
    Table,
    TextInput,
    html,
)
from hedron_core import Component, HtmlAttrValue, NodeLike

from app.models import AuditEvent
from app.ui.forms import submit_button
from app.ui.layout import INDICATOR
from app.ui.partials.shared import _filter_base_path, hedron_pagination
from app.ui.urls import form_action, hx_attrs, page_href


def audit_match_count(total: int, *, oob: bool = False) -> NodeLike:
    attrs: dict[str, HtmlAttrValue] = {"id": "audit-match-count", "class_": "verification-badge"}
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
) -> Component[Any]:
    return Section(
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


def _audit_filter_form(event_type_filter: str, outcome_filter: str) -> Form:
    return Form(
        FormField(
            name="event_type",
            label="Event type",
            id="event-type-filter",
            control=TextInput(
                "event_type",
                id="event-type-filter",
                value=event_type_filter,
                placeholder="auth.login",
                autocomplete="off",
            ),
        ),
        FormField(
            name="outcome",
            label="Outcome",
            id="outcome-filter",
            control=TextInput(
                "outcome",
                id="outcome-filter",
                value=outcome_filter,
                placeholder="success",
                autocomplete="off",
            ),
        ),
        submit_button("Apply filters", variant="secondary", small=True),
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
) -> Component[Any]:
    _ = page_count
    headers = ["When", "Event", "Outcome", "Source", "Detail"]
    if events:
        rows: list[list[NodeLike]] = [
            [
                event.occurred_at.strftime("%Y-%m-%d %H:%M"),
                event.event_type,
                event.outcome,
                event.source_ip or "",
                (event.detail or "")[:120],
            ]
            for event in events
        ]
    else:
        rows = [[f"No events ({total_events} total).", "", "", "", ""]]
    base = _filter_base_path(
        "/admin/audit",
        event_type=event_type_filter,
        outcome=outcome_filter,
    )
    return Section(
        html.div(Table(headers, rows), class_="table-wrap"),
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
) -> NodeLike:
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


def audit_results_lazy(*, event_type: str = "", outcome: str = "", page: int = 1) -> Lazy:
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
