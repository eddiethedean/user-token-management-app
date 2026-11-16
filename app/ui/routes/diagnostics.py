"""Administrator live connection diagnostics page and actions."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC
from typing import Literal

from fastapi import BackgroundTasks, HTTPException, Request, status
from fastapi.responses import PlainTextResponse, RedirectResponse
from hedron import Badge, Button, Form, Hedron, HedronRouter, Inline, OobUpdate, Stack, Text, html
from hedron.htmx import is_htmx_request
from hedron_core import NodeLike
from starlette.responses import Response

from app.dependencies import AdminAuth, DbSession, RequireCsrf, SettingsDep
from app.models import LiveDiagnosticRun
from app.services.live_diagnostics import (
    active_diagnostic_run,
    create_diagnostic_run,
    diagnostic_check_counts,
    diagnostic_checks_for_display,
    diagnostic_run_status,
    execute_diagnostic_run,
    format_readiness_report,
    list_diagnostic_runs,
    owned_diagnostic_run,
    reconcile_stale_diagnostic_runs,
)
from app.ui.design_system import surface_card
from app.ui.forms import csrf_hidden
from app.ui.http import render_authenticated_view
from app.ui.layout import INDICATOR, page_heading
from app.ui.regions import (
    DIAGNOSTIC_ANNOUNCEMENT,
    DIAGNOSTIC_CHECKS,
    DIAGNOSTIC_CONTROL,
    DIAGNOSTIC_HISTORY,
    DIAGNOSTIC_RUN,
    MAIN_PANEL,
    REQUEST_FEEDBACK,
    SIDE_NAV,
    TOAST_HOST,
)
from app.ui.urls import form_action, hx_attrs, page_href, redirect_path

_STATUS_TONES: dict[str, Literal["neutral", "info", "success", "warning", "danger"]] = {
    "passed": "success",
    "succeeded": "success",
    "running": "info",
    "queued": "info",
    "missing": "warning",
    "disabled": "warning",
    "blocked": "warning",
    "failed": "danger",
    "completed_with_failures": "danger",
    "completed_with_gaps": "warning",
    "interrupted": "warning",
    "skipped": "warning",
}
_RESULT_MESSAGES = {
    "database_accessible": "The application database responded.",
    "database_unavailable": "The application database could not be checked.",
    "database_timeout": "The application database did not respond within the diagnostic time limit.",
    "schema_revision_timeout": "The schema revision did not respond within the diagnostic time limit.",
    "spool_available": "The private transfer storage passed a write and read check.",
    "spool_unavailable": "The private transfer storage could not complete a write and read check.",
    "spool_timeout": "The private transfer storage did not respond within the diagnostic time limit.",
    "spool_cleanup_unconfirmed": "The diagnostic could not confirm that its temporary storage check file was removed.",
    "spool_not_configured": "Private transfer storage is not configured in this environment.",
    "schema_current": "The application database schema is current.",
    "schema_not_current": "The application database schema needs operator attention.",
    "schema_revision_unavailable": "The current application database schema revision could not be read.",
    "credentials_not_configured": "No credentials are saved for this provider.",
    "connection_check_required": "Catalog access was not attempted because the connection was not checked.",
    "connection_check_failed": "Catalog access was not attempted because the connection check failed.",
    "connection_available": "The provider connection test succeeded.",
    "management_api_available": "Databricks management API authentication and compute visibility succeeded.",
    "catalog_available": "The provider catalog responded.",
    "databricks_sql_catalog_available": "The Databricks SQL endpoint and catalog responded.",
    "compute_ready": "Databricks compute is ready for SQL access.",
    "administrator_unavailable": "The administrator account is no longer active for this run.",
    "provider_unavailable": "The provider check failed. Use the support reference to find the safe application diagnostics.",
    "authentication_failed": "The provider rejected authentication.",
    "connection_authentication_failed": "The provider rejected authentication.",
    "connection_permission_denied": "The connection authenticated, but the provider denied the requested access.",
    "connection_endpoint_blocked": "The configured provider endpoint is not allowed by this deployment.",
    "connection_tls_failed": "The secure connection to the provider could not be established.",
    "connection_provider_unavailable": "The provider could not be reached for this check.",
    "connection_invalid": "The saved connection could not be validated.",
    "connection_unsupported": "This saved connection setup is not supported.",
    "connection_test_incomplete": "The provider connection responded, but readiness is incomplete.",
    "connection_test_succeeded": "The provider connection test succeeded.",
    "permission_denied": "The connection authenticated, but the provider denied the requested access.",
    "source_not_found": "The configured default dataset or branch could not be found.",
    "default_dataset_not_configured": "Save a default Foundry dataset RID before running this check.",
    "connection_timeout": "The provider did not respond before the connection timed out.",
    "tls_failed": "The secure connection to the provider could not be established.",
    "endpoint_blocked": "The configured provider endpoint is not allowed by this deployment.",
    "compute_not_ready": "The provider connection is saved, but its compute is not ready.",
    "sql_operations_disabled": "Databricks SQL checks are disabled by this deployment's startup verification setting.",
    "rate_limited": "The provider temporarily rate limited the connection check.",
    "diagnostic_worker_failed": "The diagnostic worker stopped during this check. Use the run reference when contacting support.",
    "diagnostic_interrupted": "This check did not finish because its diagnostic run was interrupted.",
    "diagnostic_not_run": "This check was skipped because the diagnostic worker stopped earlier.",
    "provider_disabled": "This provider is disabled by the deployment configuration.",
    "credentials_stale": "The saved credentials changed during this check. Run diagnostics again.",
    "check_superseded": "A newer connection check replaced this result. Run diagnostics again.",
}


def register_diagnostics_routes(app: Hedron, fragment_router: HedronRouter) -> None:
    @app.page(
        "/admin/diagnostics",
        fragment_regions=(
            MAIN_PANEL,
            SIDE_NAV,
            DIAGNOSTIC_RUN,
            DIAGNOSTIC_HISTORY,
            DIAGNOSTIC_CHECKS,
            DIAGNOSTIC_CONTROL,
            DIAGNOSTIC_ANNOUNCEMENT,
            REQUEST_FEEDBACK,
        ),
        include_in_schema=False,
    )
    async def diagnostics_page(
        request: Request,
        auth: AdminAuth,
        db: DbSession,
        settings: SettingsDep,
        run_id: str = "",
    ) -> Response:
        request.state.hedron_authenticated = True
        reconcile_stale_diagnostic_runs(db, user_id=auth.user.id)
        active = active_diagnostic_run(db, user_id=auth.user.id)
        runs = list_diagnostic_runs(db, user_id=auth.user.id)
        selected = None
        if run_id:
            try:
                selected = owned_diagnostic_run(db, run_id=run_id, user_id=auth.user.id)
            except LookupError as exc:
                raise HTTPException(status_code=404, detail="Diagnostic run not found") from exc
        elif runs:
            selected = runs[0]
        body = [
            page_heading(
                "Administration",
                "Live connection diagnostics",
                "Check how this Posit Connect deployment reaches your saved data connections.",
            ),
            surface_card(
                Stack(
                    Text(
                        "These checks run from the application deployment. They verify application readiness, provider authentication, and catalog access. They do not write data or start compute."
                    ),
                    html.a("Manage connections", href=page_href(request, "/security")),
                    Form(
                        csrf_hidden(auth.session.csrf_token),
                        _run_control(
                            request,
                            active,
                            runs,
                            selected_run_id=selected.id if selected else None,
                        ),
                        action=form_action(request, "/admin/diagnostics/run"),
                        method="post",
                        data={"diagnostic-run-form": "true"},
                    ),
                    gap="md",
                )
            ),
            _run_overview(request, selected),
            surface_card(_diagnostic_history(request, runs, selected.id if selected else None)),
            _run_checks(selected),
            html.div(
                _run_announcement(selected),
                id="diagnostic-announcement",
                role="status",
                aria={"live": "polite", "atomic": "true"},
                class_="data-mover-visually-hidden",
            ),
        ]
        return await render_authenticated_view(
            request,
            body=body,
            auth=auth,
            settings=settings,
            page_title="Live connection diagnostics",
            csrf_token=auth.session.csrf_token,
            push_path="/admin/diagnostics",
            headers={"Cache-Control": "no-store"},
        )

    @app.page(
        "/admin/diagnostics/runs/{run_id}/report",
        fragment_regions=(),
        include_in_schema=False,
    )
    async def diagnostic_report(
        run_id: str,
        auth: AdminAuth,
        db: DbSession,
    ) -> Response:
        try:
            run = owned_diagnostic_run(db, run_id=run_id, user_id=auth.user.id)
        except LookupError as exc:
            raise HTTPException(status_code=404, detail="Diagnostic run not found") from exc
        return PlainTextResponse(
            format_readiness_report(run),
            headers={
                "Cache-Control": "no-store",
                "Content-Disposition": 'attachment; filename="data-mover-readiness-report.txt"',
            },
        )

    @app.action(
        "/admin/diagnostics/run",
        fragment_regions=(MAIN_PANEL, DIAGNOSTIC_RUN, TOAST_HOST, REQUEST_FEEDBACK),
        include_in_schema=False,
    )
    async def diagnostics_run_submit(
        request: Request,
        background_tasks: BackgroundTasks,
        auth: AdminAuth,
        db: DbSession,
        settings: SettingsDep,
        _csrf: RequireCsrf,
    ) -> Response:
        runtime = getattr(request.state, "execution", None)
        if runtime is None or not runtime.is_accepting():
            raise HTTPException(status_code=503, detail="The application runtime is unavailable")
        run = create_diagnostic_run(db, user=auth.user, settings=settings)
        background_tasks.add_task(execute_diagnostic_run, runtime, run.id)
        destination = redirect_path(request, f"/admin/diagnostics?run_id={run.id}")
        if is_htmx_request(request):
            from app.ui.interactions import htmx_redirect, interaction_response

            return await interaction_response(request, htmx_redirect(destination))
        return RedirectResponse(destination, status_code=status.HTTP_303_SEE_OTHER)

    @fragment_router.view(
        "/admin/diagnostics/active",
        fragment_regions=(DIAGNOSTIC_CONTROL, DIAGNOSTIC_HISTORY),
        include_in_schema=False,
    )
    async def diagnostic_active_fragment(
        request: Request,
        auth: AdminAuth,
        db: DbSession,
        control_version: str = "",
        history_version: str = "",
        selected_run_id: str = "",
    ):
        request.state.hedron_authenticated = True
        reconcile_stale_diagnostic_runs(db, user_id=auth.user.id)
        active = active_diagnostic_run(db, user_id=auth.user.id)
        runs = list_diagnostic_runs(db, user_id=auth.user.id)
        selected_id = selected_run_id or None
        next_control_version = _control_version(active, selected_id)
        next_history_version = _history_version(runs, selected_id)
        oob = []
        if next_history_version != history_version:
            oob.append(
                OobUpdate(
                    _diagnostic_history(request, runs, selected_id),
                    element_id=DIAGNOSTIC_HISTORY.id,
                    swap="outerHTML",
                )
            )
        if next_control_version != control_version:
            oob.append(
                OobUpdate(
                    _run_control(
                        request,
                        active,
                        runs,
                        selected_run_id=selected_id,
                    ),
                    element_id=DIAGNOSTIC_CONTROL.id,
                    swap="outerHTML",
                )
            )
        if not oob:
            return Response(status_code=204, headers={"Cache-Control": "no-store"})
        if next_history_version != history_version and next_control_version == control_version:
            oob.append(
                OobUpdate(
                    _run_control(
                        request,
                        active,
                        runs,
                        selected_run_id=selected_id,
                    ),
                    element_id=DIAGNOSTIC_CONTROL.id,
                    swap="outerHTML",
                )
            )
        from app.ui.interactions import ok_fragment

        return ok_fragment(
            None,
            oob=tuple(oob),
            headers={"Cache-Control": "no-store"},
        )

    @fragment_router.view(
        "/admin/diagnostics/runs/{run_id}",
        fragment_regions=(
            DIAGNOSTIC_RUN,
            DIAGNOSTIC_CHECKS,
            DIAGNOSTIC_ANNOUNCEMENT,
        ),
        include_in_schema=False,
    )
    async def diagnostic_run_fragment(
        request: Request,
        run_id: str,
        auth: AdminAuth,
        db: DbSession,
    ):
        request.state.hedron_authenticated = True
        try:
            run = owned_diagnostic_run(db, run_id=run_id, user_id=auth.user.id)
        except LookupError as exc:
            raise HTTPException(status_code=404, detail="Diagnostic run not found") from exc
        reconcile_stale_diagnostic_runs(db, user_id=auth.user.id)
        run = owned_diagnostic_run(db, run_id=run_id, user_id=auth.user.id)
        from app.ui.interactions import ok_fragment

        oob = [
            OobUpdate(
                _run_checks(run),
                element_id=DIAGNOSTIC_CHECKS.id,
                swap="outerHTML",
            ),
            OobUpdate(
                Text(_run_announcement(run)),
                element_id=DIAGNOSTIC_ANNOUNCEMENT.id,
                swap="innerHTML",
            ),
        ]
        return ok_fragment(_run_overview(request, run), oob=tuple(oob))


def _run_control(
    request: Request,
    run: LiveDiagnosticRun | None,
    runs: list[LiveDiagnosticRun],
    *,
    selected_run_id: str | None = None,
):
    active = run is not None
    button = Button(
        "Running connection checks…" if active else "Run connection checks",
        type="submit",
        disabled=active,
        attrs={"data-diagnostic-run-submit": "true"},
    )
    content: list[NodeLike] = [button]
    if active and run.id != selected_run_id:
        content.append(
            html.a(
                "View active run",
                href=page_href(request, f"/admin/diagnostics?run_id={run.id}"),
            )
        )
    return html.div(
        Inline(*content, gap="md"),
        id=DIAGNOSTIC_CONTROL.id,
        data={"diagnostic-selected-run": selected_run_id or ""},
        **hx_attrs(
            request,
            method="get",
            path=(
                "admin/diagnostics/active?"
                f"control_version={_control_version(run, selected_run_id)}"
                f"&history_version={_history_version(runs, selected_run_id)}"
                f"&selected_run_id={selected_run_id or ''}"
            ),
            target=f"#{DIAGNOSTIC_CONTROL.id}",
            polling=3,
            indicator=INDICATOR,
        ),
    )


def _control_version(run: LiveDiagnosticRun | None, selected_run_id: str | None) -> str:
    state = (run.id, selected_run_id == run.id) if run is not None else (None, False)
    return hashlib.sha256(json.dumps(state, separators=(",", ":")).encode()).hexdigest()[:16]


def _history_version(runs: list[LiveDiagnosticRun], selected_run_id: str | None) -> str:
    state = [
        (run.id, diagnostic_run_status(run), run.id == selected_run_id)
        for run in runs
    ]
    return hashlib.sha256(json.dumps(state, separators=(",", ":")).encode()).hexdigest()[:16]


def _diagnostic_history(
    request: Request,
    runs: list[LiveDiagnosticRun],
    selected_run_id: str | None,
):
    items = []
    for run in runs:
        created = run.created_at
        created = created.replace(tzinfo=UTC) if created.tzinfo is None else created.astimezone(UTC)
        label = created.strftime("%Y-%m-%d %H:%M:%S UTC")
        result_status = diagnostic_run_status(run)
        link = html.a(
            label,
            href=page_href(request, f"/admin/diagnostics?run_id={run.id}"),
            aria={"current": "page"} if run.id == selected_run_id else {},
        )
        run_link_items: list[NodeLike] = [
            link,
            Badge(
                result_status.replace("_", " "),
                tone=_STATUS_TONES.get(result_status, "neutral"),
            ),
        ]
        if run.id == selected_run_id:
            run_link_items.append(Badge("Current", tone="info"))
        items.append(html.div(Inline(*run_link_items, gap="sm")))
    return html.div(
        Stack(
            Text("Recent diagnostic runs"),
            *(items if items else [Text("No diagnostic runs yet.")]),
            gap="sm",
        ),
        id=DIAGNOSTIC_HISTORY.id,
    )


def _run_overview(request: Request, run: LiveDiagnosticRun | None):
    if run is None:
        return html.div(
            surface_card(Text("Run the checks to see connection and deployment readiness.")),
            id=DIAGNOSTIC_RUN.id,
        )
    counts = diagnostic_check_counts(run.checks_json, run_status=run.status)
    result_status = diagnostic_run_status(run)
    count_summary = " · ".join(
        f"{count} {name}" for name, count in counts.items() if count
    ) or "No checks recorded"
    announcement = _run_announcement(run)
    children: list[NodeLike] = [
        Inline(
            Text(f"Run {run.id}"),
            Badge(
                f"{run.mode} · {result_status.replace('_', ' ')}",
                tone=_STATUS_TONES.get(result_status, "neutral"),
            ),
            gap="sm",
        ),
        Text(count_summary),
        Text(f"Application build: {run.app_version or 'unknown'}"),
        Text(announcement),
    ]
    if run.status not in {"queued", "running"}:
        report_text = format_readiness_report(run)
        children.extend(
            [
                Inline(
                    Button(
                        "Copy report",
                        type="button",
                        variant="secondary",
                        attrs={
                            "data-diagnostic-copy": "true",
                            "data-report": report_text,
                        },
                    ),
                    html.a(
                        "Download report",
                        href=page_href(request, f"/admin/diagnostics/runs/{run.id}/report"),
                    ),
                    gap="md",
                ),
                html.div(
                    Stack(
                        html.label("Copy the report text below", for_="diagnostic-report-fallback"),
                        html.textarea(
                            report_text,
                            id="diagnostic-report-fallback",
                            readonly=True,
                            rows=12,
                            aria={"label": "Readiness report for manual copying"},
                        ),
                        gap="sm",
                    ),
                    id="diagnostic-report-fallback-container",
                    hidden=True,
                ),
            ]
        )
    attrs = {
        "id": DIAGNOSTIC_RUN.id,
        "data-status": result_status,
        "data-announcement": announcement,
    }
    if run.status in {"queued", "running"}:
        attrs.update(
            hx_attrs(
                request,
                method="get",
                path=f"admin/diagnostics/runs/{run.id}",
                target=f"#{DIAGNOSTIC_RUN.id}",
                polling=2,
                indicator=INDICATOR,
            )
        )
    return html.div(surface_card(Stack(*children, gap="md")), **attrs)


def _run_announcement(run: LiveDiagnosticRun | None) -> str:
    if run is None:
        return ""
    counts = diagnostic_check_counts(run.checks_json, run_status=run.status)
    if run.status in {"queued", "running"}:
        complete = sum(
            count for name, count in counts.items() if name not in {"pending", "running"}
        )
        total = sum(counts.values())
        phase = "queued" if run.status == "queued" else "running"
        summary = ", ".join(f"{count} {name}" for name, count in counts.items() if count)
        progress = f"{complete} of {total} checks finished."
        return f"Connection checks are {phase}. {progress} {summary}."
    result_status = diagnostic_run_status(run)
    summary = ", ".join(f"{count} {name}" for name, count in counts.items() if count)
    return f"Connection checks {result_status.replace('_', ' ')}. {summary}."


def _run_checks(run: LiveDiagnosticRun | None):
    group_keys = (
        ("deployment", "Deployment"),
        ("mss", "MSS"),
        ("mcscop", "MCS-COP"),
        ("postgres", "PostgreSQL"),
        ("databricks", "Databricks"),
    )
    if run is None:
        return html.div(
            surface_card(Text("Run the checks to see individual connection results.")),
            id=DIAGNOSTIC_CHECKS.id,
        )
    checks = diagnostic_checks_for_display(run)
    groups = []
    for prefix, heading in group_keys:
        group_checks = [
            check
            for check in checks
            if isinstance(check, dict) and check.get("key", "").startswith(prefix + ".")
        ]
        if not group_checks:
            continue
        results = []
        for check in group_checks:
            status_value = check.get("status", "pending")
            code = check.get("code", "")
            message = _RESULT_MESSAGES.get(code)
            if message is None:
                message = (
                    "The check failed. Use the run reference when contacting support."
                    if status_value == "failed"
                    else "This check could not run because a prerequisite was not met."
                    if status_value == "blocked"
                    else "The check has not completed."
                )
            check_children: list[NodeLike] = [
                Inline(
                    Text(check.get("label", "Diagnostic check")),
                    Badge(
                        status_value.replace("_", " "),
                        tone=_STATUS_TONES.get(status_value, "neutral"),
                    ),
                    gap="sm",
                ),
                Text(message),
            ]
            if check.get("key") == "deployment.schema":
                details = check.get("details")
                if isinstance(details, dict):
                    current = details.get("current_revision", "unavailable")
                    expected = details.get("expected_revision", "unavailable")
                    check_children.append(Text(f"Database revision: {current} · expected: {expected}"))
            results.append(
                html.div(
                    Stack(*check_children, gap="xs"),
                    class_="data-mover-diagnostic-check",
                )
            )
        groups.append(html.section(Stack(html.h2(heading), *results, gap="md")))
    return html.div(
        surface_card(Stack(*groups, gap="lg")),
        id=DIAGNOSTIC_CHECKS.id,
    )
