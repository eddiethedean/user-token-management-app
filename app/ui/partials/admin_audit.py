"""Admin audit log fragments."""

from __future__ import annotations

from urllib.parse import urlencode

from hedron import ComponentRef, ErrorState, Lazy, Loading, html

from app.models import AuditEvent
from app.ui.layout import INDICATOR
from app.ui.partials.shared import _filter_base_path, hedron_pagination
from app.ui.urls import form_action, hx_attrs, page_href


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
