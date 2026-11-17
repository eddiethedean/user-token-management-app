from __future__ import annotations

import json
from datetime import timedelta
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import APP_VERSION
from app.database import SessionLocal
from app.models import LiveDiagnosticRun, PipelineCatalogCache, User, UserSecret, utcnow
from app.schema import head_revision
from app.services.secrets import store_user_credentials
from tests.helpers import ADMIN_EMAIL, ADMIN_PASSWORD, csrf_from, login_csrf_from, web_login


def test_slow_provider_check_keeps_diagnostic_lease_alive(
    access_app,
    demo_connections,
    monkeypatch,
) -> None:
    import threading

    from app.config import get_settings
    from app.services import live_diagnostics as diagnostics

    settings = get_settings()
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.email == ADMIN_EMAIL))
        assert user is not None
        user_id = user.id
        run_id = diagnostics.create_diagnostic_run(db, user=user, settings=settings).id

    now = utcnow()
    elapsed = [0]
    renewed = threading.Event()
    original_heartbeat = diagnostics._heartbeat_diagnostic_lease
    original_check = diagnostics._test_diagnostic_connection
    monkeypatch.setattr(diagnostics, "utcnow", lambda: now + timedelta(seconds=elapsed[0]))
    monkeypatch.setattr(diagnostics, "RUN_HEARTBEAT_SECONDS", 0.01)

    def heartbeat(sessions, run_id, worker_token):
        before = elapsed[0]
        result = original_heartbeat(sessions, run_id, worker_token)
        if result and before == 299:
            renewed.set()
        return result

    def slow_check(*args, **kwargs):
        result = original_check(*args, **kwargs)
        if elapsed[0] == 0:
            elapsed[0] = 299
            assert renewed.wait(5), "The diagnostic lease was not renewed during provider I/O"
            elapsed[0] = 301
            with SessionLocal() as db:
                diagnostics.reconcile_stale_diagnostic_runs(db, user_id=user_id)
                run = db.get(LiveDiagnosticRun, run_id)
                assert run is not None and run.status == "running"
        return result

    monkeypatch.setattr(diagnostics, "_heartbeat_diagnostic_lease", heartbeat)
    monkeypatch.setattr(diagnostics, "_test_diagnostic_connection", slow_check)
    diagnostics._execute_diagnostic_checks(run_id, settings)

    with SessionLocal() as db:
        run = db.get(LiveDiagnosticRun, run_id)
        assert run is not None
        assert run.status == "completed_with_gaps"  # No spool directory in the test environment.
        assert all(
            item["status"] not in {"pending", "running"} for item in json.loads(run.checks_json)
        )
    assert not any(t.name == f"diagnostic-lease-{run_id[:8]}" for t in threading.enumerate())


@pytest.mark.parametrize("lost", ["expired", "replaced", "finished"])
def test_diagnostic_heartbeat_cannot_revive_lost_ownership(access_app, lost) -> None:
    from app.config import get_settings
    from app.services import live_diagnostics as diagnostics

    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.email == ADMIN_EMAIL))
        assert user is not None
        run_id = diagnostics.create_diagnostic_run(db, user=user, settings=get_settings()).id
    token = diagnostics._claim_diagnostic_run(run_id)
    assert token is not None
    with SessionLocal() as db:
        run = db.get(LiveDiagnosticRun, run_id)
        assert run is not None
        if lost == "expired":
            run.lease_expires_at = utcnow() - timedelta(seconds=1)
        elif lost == "replaced":
            run.worker_token = "another-worker"
        else:
            run.status = "succeeded"
        db.commit()
        deadline = run.lease_expires_at
    assert not diagnostics._heartbeat_diagnostic_lease(SessionLocal, run_id, token)
    with SessionLocal() as db:
        run = db.get(LiveDiagnosticRun, run_id)
        assert run is not None and run.lease_expires_at == deadline


def test_admin_can_run_and_reopen_owner_scoped_diagnostics(client, make_user) -> None:
    web_login(client, ADMIN_EMAIL, ADMIN_PASSWORD)
    page = client.get("/admin/diagnostics")
    assert page.status_code == 200
    assert "Live connection diagnostics" in page.text
    assert "Run connection checks" in page.text
    assert 'data-diagnostic-run-form="true"' in page.text
    assert 'class="data-mover-visually-hidden"' in page.text
    assert "Manage connections" in page.text

    started = client.post(
        "/admin/diagnostics/run",
        data={"csrf_token": csrf_from(page.text)},
        follow_redirects=False,
    )
    assert started.status_code == 303
    location = urlparse(started.headers["location"])
    run_id = parse_qs(location.query)["run_id"][0]

    reopened = client.get(started.headers["location"])
    assert reopened.status_code == 200
    assert run_id in reopened.text
    assert "Application database" in reopened.text
    assert "PostgreSQL connection" in reopened.text
    assert "No credentials are saved for this provider." in reopened.text
    assert "Copy report" in reopened.text
    assert 'data-diagnostic-copy="true"' in reopened.text
    assert 'id="diagnostic-report-fallback-container"' in reopened.text
    assert "Download report" in reopened.text
    report = client.get(f"/admin/diagnostics/runs/{run_id}/report")
    assert report.status_code == 200
    assert report.headers["content-type"].startswith("text/plain")
    assert report.headers["cache-control"] == "no-store"
    assert "Data Mover deployment readiness report" in report.text
    assert "Run status: completed_with_gaps" in report.text
    assert "Results:" in report.text
    assert "Database revision:" in report.text

    with SessionLocal() as db:
        run = db.scalar(select(LiveDiagnosticRun).where(LiveDiagnosticRun.id == run_id))
        assert run is not None
        assert run.status == "completed_with_gaps"
        assert run.app_version == APP_VERSION
        checks = json.loads(run.checks_json)
        assert (
            next(check for check in checks if check["key"] == "deployment.database")["status"]
            == "passed"
        )
        assert (
            next(check for check in checks if check["key"] == "postgres.connection")["status"]
            == "missing"
        )
        schema_check = next(check for check in checks if check["key"] == "deployment.schema")
        assert schema_check["details"]["expected_revision"] == head_revision()
        assert schema_check["details"]["current_revision"] == head_revision()
    assert reopened.text.index("Recent diagnostic runs") < reopened.text.index("Deployment")

    make_user(
        "diagnostics-other-admin@example.gov",
        password=ADMIN_PASSWORD,
        roles=("administrator",),
    )
    client.post(
        "/logout",
        data={"csrf_token": csrf_from(page.text)},
        follow_redirects=False,
    )
    web_login(client, "diagnostics-other-admin@example.gov", ADMIN_PASSWORD)
    assert client.get(started.headers["location"]).status_code == 404
    assert client.get(f"/admin/diagnostics/runs/{run_id}").status_code == 404
    assert client.get(f"/admin/diagnostics/runs/{run_id}/report").status_code == 404


def test_legacy_gap_status_is_displayed_separately_from_failures() -> None:
    from app.services.live_diagnostics import (
        diagnostic_check_counts,
        diagnostic_checks_for_display,
        diagnostic_run_status,
        format_readiness_report,
    )

    gap_run = LiveDiagnosticRun(
        id="diagnostic-gap-run",
        user_id="diagnostic-admin",
        app_version=APP_VERSION,
        status="completed_with_failures",
        mode="live",
        checks_json=json.dumps(
            [
                {"key": "postgres.connection", "status": "missing"},
                {"key": "postgres.catalog", "status": "blocked"},
            ]
        ),
    )
    failed_run = LiveDiagnosticRun(
        id="diagnostic-failed-run",
        user_id="diagnostic-admin",
        app_version=APP_VERSION,
        status="completed_with_failures",
        mode="live",
        checks_json=json.dumps(
            [{"key": "mss.connection", "status": "failed", "code": "permission_denied"}]
        ),
    )

    assert diagnostic_run_status(gap_run) == "completed_with_gaps"
    assert "Run status: completed_with_gaps" in format_readiness_report(gap_run)
    assert diagnostic_run_status(failed_run) == "completed_with_failures"

    legacy_missing_foundry = LiveDiagnosticRun(
        id="diagnostic-foundry-gap-run",
        user_id="diagnostic-admin",
        app_version=APP_VERSION,
        status="completed_with_failures",
        mode="live",
        checks_json=json.dumps(
            [
                {
                    "key": "mss.connection",
                    "status": "failed",
                    "code": "default_dataset_not_configured",
                },
                {
                    "key": "mss.catalog",
                    "status": "failed",
                    "code": "default_dataset_not_configured",
                },
            ]
        ),
    )
    displayed_checks = diagnostic_checks_for_display(legacy_missing_foundry)

    assert diagnostic_run_status(legacy_missing_foundry) == "completed_with_gaps"
    assert (
        diagnostic_check_counts(
            legacy_missing_foundry.checks_json,
            run_status=legacy_missing_foundry.status,
        )["missing"]
        == 1
    )
    assert displayed_checks == [
        {"key": "mss.connection", "status": "missing", "code": "default_dataset_not_configured"},
        {"key": "mss.catalog", "status": "blocked", "code": "default_dataset_not_configured"},
    ]
    assert "Run status: completed_with_gaps" in format_readiness_report(legacy_missing_foundry)


def test_readiness_report_excludes_saved_connection_values(client) -> None:
    from app.config import get_settings

    web_login(client, ADMIN_EMAIL, ADMIN_PASSWORD)
    with SessionLocal() as db:
        admin = db.scalar(select(User).where(User.email == ADMIN_EMAIL))
        assert admin is not None
        store_user_credentials(
            db,
            get_settings(),
            user=admin,
            provider="postgres",
            credentials={
                "host": "warehouse-secret.example",
                "port": "5432",
                "database": "diagnostic-report",
                "username": "report-user",
                "password": "diagnostic-report-secret-canary",
                "sslmode": "verify-full",
            },
        )
        postgres_secret = db.scalar(
            select(UserSecret).where(
                UserSecret.user_id == admin.id,
                UserSecret.provider == "postgres",
            )
        )
        assert postgres_secret is not None
        postgres_secret.validation_status = "connected"
        postgres_secret.validation_code = "previous_manual_check"
        postgres_secret.validation_reference = "previous-reference"
        postgres_secret.validation_message = "Previous connection health result."
        postgres_secret.validation_check_id = "previous-check-id"
        db.commit()

    page = client.get("/admin/diagnostics")
    started = client.post(
        "/admin/diagnostics/run",
        data={"csrf_token": csrf_from(page.text)},
        follow_redirects=False,
    )
    run_id = parse_qs(urlparse(started.headers["location"]).query)["run_id"][0]
    report = client.get(f"/admin/diagnostics/runs/{run_id}/report")

    assert report.status_code == 200
    assert "PostgreSQL catalog access" in report.text
    assert "diagnostic-report-secret-canary" not in report.text
    assert "warehouse-secret.example" not in report.text
    assert "report-user" not in report.text
    with SessionLocal() as db:
        admin = db.scalar(select(User).where(User.email == ADMIN_EMAIL))
        assert admin is not None
        postgres_secret = db.scalar(
            select(UserSecret).where(
                UserSecret.user_id == admin.id,
                UserSecret.provider == "postgres",
            )
        )
        assert postgres_secret is not None
        assert postgres_secret.validation_status == "connected"
        assert postgres_secret.validation_code == "previous_manual_check"
        assert postgres_secret.validation_reference == "previous-reference"
        assert postgres_secret.validation_message == "Previous connection health result."
        assert postgres_secret.validation_check_id == "previous-check-id"


def test_non_admin_cannot_access_live_diagnostics(client, make_user) -> None:
    from tests.helpers import USER_PASSWORD

    make_user("diagnostics-user@example.gov")
    web_login(client, "diagnostics-user@example.gov", USER_PASSWORD)
    response = client.get("/admin/diagnostics")
    assert response.status_code == 403


def test_diagnostics_navigation_and_run_links_keep_connect_mount(access_app, monkeypatch) -> None:
    async def keep_queued(_runtime, _run_id):
        return None

    monkeypatch.setattr("app.ui.routes.diagnostics.execute_diagnostic_run", keep_queued)
    mounted_client = TestClient(
        access_app,
        root_path="/content/data-mover",
        follow_redirects=False,
        client=("127.0.0.1", 50000),
    )
    mount = "/content/data-mover"
    login_page = mounted_client.get(f"{mount}/login")
    login = mounted_client.post(
        f"{mount}/login",
        data={
            "email": ADMIN_EMAIL,
            "password": ADMIN_PASSWORD,
            "next": "/profile",
            "preauth_csrf_token": login_csrf_from(login_page.text),
        },
    )
    assert login.status_code == 303

    page = mounted_client.get(f"{mount}/admin/diagnostics")

    assert page.status_code == 200
    assert f'href="{mount}/security"' in page.text
    assert f'action="{mount}/admin/diagnostics/run"' in page.text

    started = mounted_client.post(
        f"{mount}/admin/diagnostics/run",
        data={"csrf_token": csrf_from(page.text)},
        follow_redirects=False,
    )
    assert started.status_code == 303
    assert started.headers["location"].startswith(f"{mount}/admin/diagnostics?run_id=")
    run_id = parse_qs(urlparse(started.headers["location"]).query)["run_id"][0]
    reopened = mounted_client.get(started.headers["location"])
    assert reopened.status_code == 200
    assert f'hx-get="{mount}/admin/diagnostics/runs/{run_id}"' in reopened.text

    with SessionLocal() as db:
        run = db.get(LiveDiagnosticRun, run_id)
        assert run is not None
        run.status = "succeeded"
        run.finished_at = utcnow()
        db.commit()

    finished = mounted_client.get(
        f"{mount}/admin/diagnostics/runs/{run_id}",
        headers={"HX-Request": "true", "HX-Target": "#diagnostic-run"},
    )
    assert finished.status_code == 200
    assert "hx-swap-oob" in finished.text
    assert f'href="{mount}/admin/diagnostics/runs/{run_id}/report"' in finished.text
    report = mounted_client.get(f"{mount}/admin/diagnostics/runs/{run_id}/report")
    assert report.status_code == 200

    history_reopen = mounted_client.get(f"{mount}/admin/diagnostics?run_id={run_id}")
    assert history_reopen.status_code == 200
    assert f"Run {run_id}" in history_reopen.text


def test_queued_diagnostic_fragment_renders_polling_root(client, monkeypatch) -> None:
    async def keep_queued(_runtime, _run_id):
        return None

    monkeypatch.setattr("app.ui.routes.diagnostics.execute_diagnostic_run", keep_queued)
    web_login(client, ADMIN_EMAIL, ADMIN_PASSWORD)
    page = client.get("/admin/diagnostics")
    started = client.post(
        "/admin/diagnostics/run",
        data={"csrf_token": csrf_from(page.text)},
        follow_redirects=False,
    )
    run_id = parse_qs(urlparse(started.headers["location"]).query)["run_id"][0]

    fragment = client.get(
        f"/admin/diagnostics/runs/{run_id}",
        headers={"HX-Request": "true", "HX-Target": "#diagnostic-run"},
    )

    assert fragment.status_code == 200
    assert 'id="diagnostic-run"' in fragment.text
    assert "queued" in fragment.text
    assert "every 2s" in fragment.text

    with SessionLocal() as db:
        run = db.get(LiveDiagnosticRun, run_id)
        assert run is not None
        run.status = "succeeded"
        run.finished_at = utcnow()
        db.commit()

    terminal = client.get(
        f"/admin/diagnostics/runs/{run_id}",
        headers={"HX-Request": "true", "HX-Target": "#diagnostic-run"},
    )
    assert terminal.status_code == 200
    assert "hx-swap-oob" in terminal.text
    assert "diagnostic-announcement" in terminal.text
    assert "succeeded" in terminal.text


def test_stale_diagnostic_run_is_interrupted_with_partial_checks_preserved(
    client, monkeypatch
) -> None:
    async def keep_queued(_runtime, _run_id):
        return None

    from app.services.live_diagnostics import DiagnosticLeaseLost, reconcile_stale_diagnostic_runs

    monkeypatch.setattr("app.ui.routes.diagnostics.execute_diagnostic_run", keep_queued)
    web_login(client, ADMIN_EMAIL, ADMIN_PASSWORD)
    page = client.get("/admin/diagnostics")
    started = client.post(
        "/admin/diagnostics/run",
        data={"csrf_token": csrf_from(page.text)},
        follow_redirects=False,
    )
    run_id = parse_qs(urlparse(started.headers["location"]).query)["run_id"][0]

    with SessionLocal() as db:
        run = db.get(LiveDiagnosticRun, run_id)
        assert run is not None
        checks = json.loads(run.checks_json)
        checks[0].update(status="passed", code="database_accessible")
        checks[1].update(status="running", code="spool_timeout")
        checks[2].update(status="pending")
        run.checks_json = json.dumps(checks)
        run.status = "running"
        run.worker_token = "stale-worker-token"
        run.lease_expires_at = utcnow() - timedelta(seconds=1)
        db.commit()

    with SessionLocal() as db:
        admin = db.scalar(select(User).where(User.email == ADMIN_EMAIL))
        assert admin is not None
        reconcile_stale_diagnostic_runs(db, user_id=admin.id)
        run = db.get(LiveDiagnosticRun, run_id)
        assert run is not None
        assert run.status == "interrupted"
        recovered_checks = json.loads(run.checks_json)
        assert recovered_checks[0]["status"] == "passed"
        assert recovered_checks[1]["status"] == "skipped"
        assert recovered_checks[1]["code"] == "diagnostic_interrupted"
        assert recovered_checks[2]["status"] == "skipped"
        assert run.worker_token is None
        with pytest.raises(DiagnosticLeaseLost):
            from app.services.live_diagnostics import _record_check

            _record_check(db, run, "deployment.database", "failed", "database_unavailable")

    with SessionLocal() as db:
        run = db.get(LiveDiagnosticRun, run_id)
        assert run is not None
        assert run.status == "interrupted"
        assert json.loads(run.checks_json)[0]["status"] == "passed"


def test_duplicate_start_reuses_active_run_and_history_keeps_active_control(
    client, monkeypatch
) -> None:
    async def keep_queued(_runtime, _run_id):
        return None

    monkeypatch.setattr("app.ui.routes.diagnostics.execute_diagnostic_run", keep_queued)
    web_login(client, ADMIN_EMAIL, ADMIN_PASSWORD)
    page = client.get("/admin/diagnostics")
    first = client.post(
        "/admin/diagnostics/run",
        data={"csrf_token": csrf_from(page.text)},
        follow_redirects=False,
    )
    first_run_id = parse_qs(urlparse(first.headers["location"]).query)["run_id"][0]
    repeated = client.post(
        "/admin/diagnostics/run",
        data={"csrf_token": csrf_from(page.text)},
        follow_redirects=False,
    )
    repeated_run_id = parse_qs(urlparse(repeated.headers["location"]).query)["run_id"][0]

    assert repeated_run_id == first_run_id
    with SessionLocal() as db:
        run = db.get(LiveDiagnosticRun, first_run_id)
        assert run is not None
        run.status = "succeeded"
        run.finished_at = utcnow()
        db.commit()

    second = client.post(
        "/admin/diagnostics/run",
        data={"csrf_token": csrf_from(page.text)},
        follow_redirects=False,
    )
    second_run_id = parse_qs(urlparse(second.headers["location"]).query)["run_id"][0]
    historical = client.get(f"/admin/diagnostics?run_id={first_run_id}")

    assert second_run_id != first_run_id
    assert "disabled" in historical.text
    assert "View active run" in historical.text
    assert f"run_id={second_run_id}" in historical.text

    active_fragment = client.get(
        "/admin/diagnostics/active",
        headers={"HX-Request": "true", "HX-Target": "#diagnostic-run-control"},
    )
    assert active_fragment.status_code == 200, active_fragment.text
    assert 'id="diagnostic-history"' in active_fragment.text
    assert 'id="diagnostic-run-control"' in active_fragment.text
    assert (
        f"run_id={second_run_id}" in client.get(f"/admin/diagnostics?run_id={second_run_id}").text
    )


def test_diagnostic_active_poll_returns_no_content_when_visible_state_is_unchanged(client) -> None:
    from app.services.live_diagnostics import active_diagnostic_run, list_diagnostic_runs
    from app.ui.routes.diagnostics import _control_version, _history_version

    web_login(client, ADMIN_EMAIL, ADMIN_PASSWORD)
    client.get("/admin/diagnostics")
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.email == ADMIN_EMAIL))
        assert user is not None
        active = active_diagnostic_run(db, user_id=user.id)
        runs = list_diagnostic_runs(db, user_id=user.id)
        control_version = _control_version(active, None)
        history_version = _history_version(runs, None)

    response = client.get(
        "/admin/diagnostics/active",
        params={
            "control_version": control_version,
            "history_version": history_version,
        },
        headers={"HX-Request": "true", "HX-Target": "#diagnostic-run-control"},
    )

    assert response.status_code == 204, response.text
    assert response.content == b""


def test_active_poll_refreshes_interrupted_history_while_viewing_an_older_run(
    client, monkeypatch
) -> None:
    from app.services.live_diagnostics import active_diagnostic_run, list_diagnostic_runs
    from app.ui.routes.diagnostics import _control_version, _history_version

    async def keep_queued(_runtime, _run_id):
        return None

    monkeypatch.setattr("app.ui.routes.diagnostics.execute_diagnostic_run", keep_queued)
    web_login(client, ADMIN_EMAIL, ADMIN_PASSWORD)
    page = client.get("/admin/diagnostics")
    first = client.post(
        "/admin/diagnostics/run",
        data={"csrf_token": csrf_from(page.text)},
        follow_redirects=False,
    )
    first_run_id = parse_qs(urlparse(first.headers["location"]).query)["run_id"][0]
    with SessionLocal() as db:
        old_run = db.get(LiveDiagnosticRun, first_run_id)
        assert old_run is not None
        old_run.status = "succeeded"
        old_run.finished_at = utcnow()
        db.commit()

    second = client.post(
        "/admin/diagnostics/run",
        data={"csrf_token": csrf_from(page.text)},
        follow_redirects=False,
    )
    active_run_id = parse_qs(urlparse(second.headers["location"]).query)["run_id"][0]
    client.get(f"/admin/diagnostics?run_id={first_run_id}")
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.email == ADMIN_EMAIL))
        active_run = db.get(LiveDiagnosticRun, active_run_id)
        assert user is not None and active_run is not None
        active_run.status = "running"
        active_run.worker_token = "expired-worker"
        active_run.lease_expires_at = utcnow() - timedelta(seconds=1)
        db.commit()
        active = active_diagnostic_run(db, user_id=user.id)
        runs = list_diagnostic_runs(db, user_id=user.id)
        control_version = _control_version(active, first_run_id)
        history_version = _history_version(runs, first_run_id)

    response = client.get(
        "/admin/diagnostics/active",
        params={
            "control_version": control_version,
            "history_version": history_version,
            "selected_run_id": first_run_id,
        },
        headers={"HX-Request": "true", "HX-Target": "#diagnostic-run-control"},
    )

    assert response.status_code == 200, response.text
    assert "interrupted" in response.text
    assert 'id="diagnostic-history"' in response.text
    assert 'id="diagnostic-run-control"' in response.text
    assert f"run_id={first_run_id}" in response.text
    assert f"run_id={active_run_id}" in response.text
    assert f'href="/admin/diagnostics?run_id={first_run_id}" aria-current="page"' in response.text


def test_unexpected_diagnostic_worker_error_finishes_run(client, monkeypatch) -> None:
    from app.services import live_diagnostics

    def fail_before_checks(_run_id, _settings):
        raise RuntimeError("provider detail should not be displayed")

    monkeypatch.setattr(live_diagnostics, "_run_diagnostic_checks", fail_before_checks)
    web_login(client, ADMIN_EMAIL, ADMIN_PASSWORD)
    page = client.get("/admin/diagnostics")
    started = client.post(
        "/admin/diagnostics/run",
        data={"csrf_token": csrf_from(page.text)},
        follow_redirects=False,
    )
    run_id = parse_qs(urlparse(started.headers["location"]).query)["run_id"][0]

    with SessionLocal() as db:
        run = db.scalar(select(LiveDiagnosticRun).where(LiveDiagnosticRun.id == run_id))
        assert run is not None
        assert run.status == "completed_with_failures"
        checks = json.loads(run.checks_json)
        assert all(check["status"] == "skipped" for check in checks)
        assert all(check["code"] == "diagnostic_not_run" for check in checks)


def test_database_probes_report_a_bounded_timeout(client, monkeypatch) -> None:
    import subprocess

    from app.services import live_diagnostics

    timeouts = []

    def hang_probe(*_args, **kwargs):
        timeouts.append(kwargs["timeout"])
        raise subprocess.TimeoutExpired("database-probe", kwargs["timeout"])

    monkeypatch.setattr(live_diagnostics.subprocess, "run", hang_probe)
    web_login(client, ADMIN_EMAIL, ADMIN_PASSWORD)
    page = client.get("/admin/diagnostics")
    started = client.post(
        "/admin/diagnostics/run",
        data={"csrf_token": csrf_from(page.text)},
        follow_redirects=False,
    )
    run_id = parse_qs(urlparse(started.headers["location"]).query)["run_id"][0]

    with SessionLocal() as db:
        run = db.get(LiveDiagnosticRun, run_id)
        assert run is not None
        checks = json.loads(run.checks_json)
        database_check = next(check for check in checks if check["key"] == "deployment.database")
        schema_check = next(check for check in checks if check["key"] == "deployment.schema")

    assert timeouts == [5, 5]
    assert database_check["code"] == "database_timeout"
    assert schema_check["code"] == "schema_revision_timeout"


def test_spool_probe_isolated_write_read_and_cleanup(tmp_path) -> None:
    from app.services.live_diagnostics import _run_spool_process

    result = _run_spool_process(
        str(tmp_path),
        "diagnostic-test.tmp",
        operation="probe",
        timeout=5,
    )

    assert result["ok"] is True
    assert result["cleanup_ok"] is True
    assert list(tmp_path.iterdir()) == []


def test_timed_out_spool_probe_cleans_its_exact_file(tmp_path, monkeypatch) -> None:
    import subprocess

    from app.services import live_diagnostics

    name = "data-mover-diagnostic-test.tmp"
    test_file = tmp_path / name
    test_file.write_text("partial diagnostic data")
    original_run = subprocess.run

    def timeout_probe(command, *, input, **kwargs):
        request = json.loads(input)
        if request["operation"] == "probe":
            raise subprocess.TimeoutExpired(command, kwargs["timeout"])
        return original_run(command, input=input, **kwargs)

    monkeypatch.setattr(live_diagnostics.subprocess, "run", timeout_probe)

    with pytest.raises(subprocess.TimeoutExpired):
        live_diagnostics._run_spool_process(
            str(tmp_path),
            name,
            operation="probe",
            timeout=5,
        )

    assert live_diagnostics._cleanup_spool_probe(str(tmp_path), name) is True
    assert not test_file.exists()


def test_diagnostic_catalog_credentials_disable_databricks_startup(client) -> None:
    from app.config import get_settings
    from app.services.catalogs import UserCatalog

    settings = get_settings()
    with SessionLocal() as db:
        admin = db.scalar(select(User).where(User.email == ADMIN_EMAIL))
        assert admin is not None
        store_user_credentials(
            db,
            settings,
            user=admin,
            provider="databricks",
            credentials={
                "workspace_url": "https://databricks.demo.invalid",
                "token": "synthetic-diagnostics-token",
                "compute_kind": "warehouse",
                "compute_id": "diagnostics-warehouse",
                "http_path": "sql/1.0/warehouses/diagnostics-warehouse",
                "startup_policy": "allow_start",
            },
        )
        catalog = UserCatalog(
            db,
            settings,
            admin,
            cache_enabled=False,
            allow_compute_start=False,
        )

        credentials = catalog._credentials_for("databricks")

    assert credentials["startup_policy"] == "manual"
    assert "_startup_request" not in credentials


def test_diagnostics_block_provider_result_if_credentials_change_during_connection(
    client, monkeypatch
) -> None:
    from datetime import timedelta

    from app.config import get_settings
    from app.services import live_diagnostics

    web_login(client, ADMIN_EMAIL, ADMIN_PASSWORD)
    with SessionLocal() as db:
        admin = db.scalar(select(User).where(User.email == ADMIN_EMAIL))
        assert admin is not None
        store_user_credentials(
            db,
            get_settings(),
            user=admin,
            provider="postgres",
            credentials={
                "host": "warehouse.internal.example",
                "port": "5432",
                "database": "diagnostics",
                "username": "diagnostics_user",
                "password": "synthetic-diagnostics-password",
                "sslmode": "verify-full",
            },
        )

    original_resolver = live_diagnostics._diagnostic_connection_tester_for

    class RotateCredentialsDuringProbe:
        def __init__(self, connector):
            self.connector = connector

        def test_connection(self, credentials):
            result = self.connector.test_connection(credentials)
            with SessionLocal() as second_session:
                secret = second_session.scalar(
                    select(UserSecret).where(
                        UserSecret.user_id
                        == second_session.scalar(select(User.id).where(User.email == ADMIN_EMAIL)),
                        UserSecret.provider == "postgres",
                    )
                )
                assert secret is not None
                secret.updated_at = secret.updated_at + timedelta(seconds=1)
                second_session.commit()
            return result

    monkeypatch.setattr(
        live_diagnostics,
        "_diagnostic_connection_tester_for",
        lambda provider: RotateCredentialsDuringProbe(original_resolver(provider)),
    )
    page = client.get("/admin/diagnostics")
    started = client.post(
        "/admin/diagnostics/run",
        data={"csrf_token": csrf_from(page.text)},
        follow_redirects=False,
    )
    run_id = parse_qs(urlparse(started.headers["location"]).query)["run_id"][0]

    with SessionLocal() as db:
        run = db.get(LiveDiagnosticRun, run_id)
        assert run is not None
        checks = json.loads(run.checks_json)
        connection = next(check for check in checks if check["key"] == "postgres.connection")
        assert connection["status"] == "blocked"
        assert connection["code"] == "credentials_stale"
        catalog_check = next(check for check in checks if check["key"] == "postgres.catalog")
        assert catalog_check["status"] == "blocked"
        assert catalog_check["code"] == "credentials_stale"


def test_diagnostics_block_remaining_checks_when_admin_is_revoked(client, monkeypatch) -> None:
    from app.config import get_settings
    from app.services import live_diagnostics

    web_login(client, ADMIN_EMAIL, ADMIN_PASSWORD)
    with SessionLocal() as db:
        admin = db.scalar(select(User).where(User.email == ADMIN_EMAIL))
        assert admin is not None
        store_user_credentials(
            db,
            get_settings(),
            user=admin,
            provider="postgres",
            credentials={
                "host": "warehouse.internal.example",
                "port": "5432",
                "database": "diagnostics",
                "username": "diagnostics_user",
                "password": "synthetic-diagnostics-password",
                "sslmode": "verify-full",
            },
        )

    original_resolver = live_diagnostics._diagnostic_connection_tester_for

    class RevokeAdminDuringProbe:
        def __init__(self, connector):
            self.connector = connector

        def test_connection(self, credentials):
            result = self.connector.test_connection(credentials)
            with SessionLocal() as second_session:
                user = second_session.scalar(select(User).where(User.email == ADMIN_EMAIL))
                assert user is not None
                user.status = "inactive"
                second_session.commit()
            return result

    monkeypatch.setattr(
        live_diagnostics,
        "_diagnostic_connection_tester_for",
        lambda provider: RevokeAdminDuringProbe(original_resolver(provider)),
    )
    page = client.get("/admin/diagnostics")
    started = client.post(
        "/admin/diagnostics/run",
        data={"csrf_token": csrf_from(page.text)},
        follow_redirects=False,
    )
    run_id = parse_qs(urlparse(started.headers["location"]).query)["run_id"][0]

    with SessionLocal() as db:
        run = db.get(LiveDiagnosticRun, run_id)
        assert run is not None
        checks = json.loads(run.checks_json)
        postgres_connection = next(
            check for check in checks if check["key"] == "postgres.connection"
        )
        assert postgres_connection["status"] == "blocked"
        assert postgres_connection["code"] == "administrator_unavailable"
        postgres_catalog = next(check for check in checks if check["key"] == "postgres.catalog")
        assert postgres_catalog["status"] == "blocked"
        assert postgres_catalog["code"] == "administrator_unavailable"
        assert all(check["status"] != "pending" for check in checks)


@pytest.mark.parametrize(
    ("change", "expected_code"),
    [("rotate", "credentials_stale"), ("revoke", "administrator_unavailable")],
)
def test_postgres_catalog_guard_runs_before_provider_call(
    client, monkeypatch, change, expected_code
) -> None:
    from app.config import get_settings
    from app.services.catalogs import UserCatalog

    web_login(client, ADMIN_EMAIL, ADMIN_PASSWORD)
    with SessionLocal() as db:
        admin = db.scalar(select(User).where(User.email == ADMIN_EMAIL))
        assert admin is not None
        store_user_credentials(
            db,
            get_settings(),
            user=admin,
            provider="postgres",
            credentials={
                "host": "warehouse.internal.example",
                "port": "5432",
                "database": "diagnostics",
                "username": "diagnostics_user",
                "password": "synthetic-diagnostics-password",
                "sslmode": "verify-full",
            },
        )

    original_guard = UserCatalog._check_diagnostic_authorization

    def change_before_guard(credentials):
        if "host" in credentials and "database" in credentials:
            with SessionLocal() as second_session:
                if change == "revoke":
                    user = second_session.scalar(select(User).where(User.email == ADMIN_EMAIL))
                    assert user is not None
                    user.status = "inactive"
                else:
                    user = second_session.scalar(select(User).where(User.email == ADMIN_EMAIL))
                    assert user is not None
                    secret = second_session.scalar(
                        select(UserSecret).where(
                            UserSecret.user_id == user.id,
                            UserSecret.provider == "postgres",
                        )
                    )
                    assert secret is not None
                    secret.updated_at += timedelta(seconds=1)
                second_session.commit()
        original_guard(credentials)

    monkeypatch.setattr(
        UserCatalog,
        "_check_diagnostic_authorization",
        staticmethod(change_before_guard),
    )
    page = client.get("/admin/diagnostics")
    started = client.post(
        "/admin/diagnostics/run",
        data={"csrf_token": csrf_from(page.text)},
        follow_redirects=False,
    )
    run_id = parse_qs(urlparse(started.headers["location"]).query)["run_id"][0]

    with SessionLocal() as db:
        run = db.get(LiveDiagnosticRun, run_id)
        assert run is not None
        checks = json.loads(run.checks_json)
        catalog = next(check for check in checks if check["key"] == "postgres.catalog")
        assert catalog["status"] == "blocked"
        assert catalog["code"] == expected_code
        if change == "revoke":
            assert all(check["status"] != "pending" for check in checks)


def test_databricks_compute_race_is_reported_as_blocked(client, monkeypatch) -> None:
    from app.config import get_settings
    from app.connectors.errors import ConnectorError, TransferErrorCode
    from app.services.catalogs import UserCatalog

    web_login(client, ADMIN_EMAIL, ADMIN_PASSWORD)
    with SessionLocal() as db:
        admin = db.scalar(select(User).where(User.email == ADMIN_EMAIL))
        assert admin is not None
        store_user_credentials(
            db,
            get_settings(),
            user=admin,
            provider="databricks",
            credentials={
                "workspace_url": "https://databricks.demo.invalid",
                "token": "synthetic-diagnostics-token",
                "compute_kind": "warehouse",
                "compute_id": "diagnostics-warehouse",
                "http_path": "sql/1.0/warehouses/diagnostics-warehouse",
                "startup_policy": "manual",
            },
        )

    original_list_namespaces = UserCatalog.list_namespaces

    def warehouse_stops(self, provider):
        if provider == "databricks":
            raise ConnectorError(TransferErrorCode.COMPUTE_NOT_READY, "synthetic race")
        return original_list_namespaces(self, provider)

    monkeypatch.setattr(UserCatalog, "list_namespaces", warehouse_stops)
    page = client.get("/admin/diagnostics")
    started = client.post(
        "/admin/diagnostics/run",
        data={"csrf_token": csrf_from(page.text)},
        follow_redirects=False,
    )
    run_id = parse_qs(urlparse(started.headers["location"]).query)["run_id"][0]

    with SessionLocal() as db:
        run = db.get(LiveDiagnosticRun, run_id)
        assert run is not None
        checks = json.loads(run.checks_json)
        sql_check = next(check for check in checks if check["key"] == "databricks.catalog")
        assert sql_check["status"] == "blocked"
        assert sql_check["code"] == "compute_not_ready"


def test_catalog_failure_logs_the_visible_diagnostic_reference(client, monkeypatch, caplog) -> None:
    from app.config import get_settings
    from app.connectors.errors import ConnectorError, TransferErrorCode
    from app.services.catalogs import UserCatalog

    web_login(client, ADMIN_EMAIL, ADMIN_PASSWORD)
    with SessionLocal() as db:
        admin = db.scalar(select(User).where(User.email == ADMIN_EMAIL))
        assert admin is not None
        store_user_credentials(
            db,
            get_settings(),
            user=admin,
            provider="postgres",
            credentials={
                "host": "warehouse.internal.example",
                "port": "5432",
                "database": "diagnostics",
                "username": "diagnostics_user",
                "password": "synthetic-diagnostics-password",
                "sslmode": "verify-full",
            },
        )

    original_list_namespaces = UserCatalog.list_namespaces

    def provider_rejects_probe(self, provider):
        if provider == "postgres":
            raise ConnectorError(TransferErrorCode.TLS_FAILED, "synthetic provider detail")
        return original_list_namespaces(self, provider)

    monkeypatch.setattr(UserCatalog, "list_namespaces", provider_rejects_probe)
    page = client.get("/admin/diagnostics")
    started = client.post(
        "/admin/diagnostics/run",
        data={"csrf_token": csrf_from(page.text)},
        follow_redirects=False,
    )
    run_id = parse_qs(urlparse(started.headers["location"]).query)["run_id"][0]

    failure = next(
        record
        for record in caplog.records
        if getattr(record, "event", "") == "diagnostic.check.failed"
        and getattr(record, "run_id", "") == run_id
    )
    assert failure.reference_id == run_id
    assert failure.provider == "postgres"
    assert failure.error_code == "tls_failed"
    assert "synthetic provider detail" not in failure.getMessage()


def test_diagnostics_checks_saved_demo_connections_and_catalogs(client) -> None:
    from app.config import get_settings

    web_login(client, ADMIN_EMAIL, ADMIN_PASSWORD)
    with SessionLocal() as db:
        admin = db.scalar(select(User).where(User.email == ADMIN_EMAIL))
        assert admin is not None
        store_user_credentials(
            db,
            get_settings(),
            user=admin,
            provider="postgres",
            credentials={
                "host": "warehouse.internal.example",
                "port": "5432",
                "database": "diagnostics",
                "username": "diagnostics_user",
                "password": "synthetic-diagnostics-password",
                "sslmode": "verify-full",
            },
        )
        stale_cache = PipelineCatalogCache(
            user_id=admin.id,
            provider="postgres",
            namespace="",
            payload_json='{"items":[]}',
            expires_at=utcnow() + timedelta(hours=1),
        )
        db.add(stale_cache)
        db.commit()
        store_user_credentials(
            db,
            get_settings(),
            user=admin,
            provider="databricks",
            credentials={
                "workspace_url": "https://databricks.demo.invalid",
                "token": "synthetic-diagnostics-token",
                "compute_kind": "warehouse",
                "compute_id": "diagnostics-warehouse",
                "http_path": "sql/1.0/warehouses/diagnostics-warehouse",
                "startup_policy": "manual",
            },
        )

    page = client.get("/admin/diagnostics")
    started = client.post(
        "/admin/diagnostics/run",
        data={"csrf_token": csrf_from(page.text)},
        follow_redirects=False,
    )
    run_id = parse_qs(urlparse(started.headers["location"]).query)["run_id"][0]
    with SessionLocal() as db:
        run = db.scalar(select(LiveDiagnosticRun).where(LiveDiagnosticRun.id == run_id))
        assert run is not None
        checks = json.loads(run.checks_json)
        assert (
            next(check for check in checks if check["key"] == "postgres.connection")["status"]
            == "passed"
        )
        assert (
            next(check for check in checks if check["key"] == "postgres.catalog")["status"]
            == "passed"
        )
        databricks_management = next(
            check for check in checks if check["key"] == "databricks.connection"
        )
        databricks_compute = next(check for check in checks if check["key"] == "databricks.compute")
        databricks_catalog = next(check for check in checks if check["key"] == "databricks.catalog")
        assert databricks_management["status"] == "passed"
        assert databricks_management["code"] == "management_api_available"
        assert databricks_compute["status"] == "passed"
        assert databricks_compute["code"] == "compute_ready"
        assert databricks_catalog["status"] == "passed"
        assert databricks_catalog["code"] == "databricks_sql_catalog_available"
        postgres_cache = db.scalar(
            select(PipelineCatalogCache).where(
                PipelineCatalogCache.user_id == admin.id,
                PipelineCatalogCache.provider == "postgres",
                PipelineCatalogCache.namespace == "",
            )
        )
        assert postgres_cache is not None
        assert postgres_cache.payload_json == '{"items":[]}'


@pytest.mark.parametrize(
    ("dataset_rid", "expected_code", "expected_connection_status", "expected_catalog_status"),
    [
        (None, "default_dataset_not_configured", "missing", "blocked"),
        ("ri.foundry.main.dataset.missing", "source_not_found", "failed", "failed"),
    ],
)
def test_foundry_diagnostic_reports_default_dataset_failure_from_one_probe(
    client,
    monkeypatch,
    dataset_rid,
    expected_code,
    expected_connection_status,
    expected_catalog_status,
) -> None:
    from app.config import get_settings
    from app.connectors.fake import FakeFoundryConnector
    from app.services.demo import DEMO_CONNECTION_CREDENTIALS

    web_login(client, ADMIN_EMAIL, ADMIN_PASSWORD)
    credentials = dict(DEMO_CONNECTION_CREDENTIALS["mss"])
    if dataset_rid is None:
        credentials.pop("dataset_rid")
    else:
        credentials["dataset_rid"] = dataset_rid
    with SessionLocal() as db:
        admin = db.scalar(select(User).where(User.email == ADMIN_EMAIL))
        assert admin is not None
        store_user_credentials(
            db,
            get_settings(),
            user=admin,
            provider="mss",
            credentials=credentials,
        )

    calls = 0
    original_probe = FakeFoundryConnector.test_diagnostic_connection

    def count_probe(self, saved_credentials):
        nonlocal calls
        calls += 1
        return original_probe(self, saved_credentials)

    monkeypatch.setattr(FakeFoundryConnector, "test_diagnostic_connection", count_probe)
    page = client.get("/admin/diagnostics")
    started = client.post(
        "/admin/diagnostics/run",
        data={"csrf_token": csrf_from(page.text)},
        follow_redirects=False,
    )
    run_id = parse_qs(urlparse(started.headers["location"]).query)["run_id"][0]

    with SessionLocal() as db:
        run = db.get(LiveDiagnosticRun, run_id)
        assert run is not None
        checks = json.loads(run.checks_json)
        connection = next(check for check in checks if check["key"] == "mss.connection")
        catalog = next(check for check in checks if check["key"] == "mss.catalog")

    assert calls == 1
    assert connection["status"] == expected_connection_status
    assert connection["code"] == expected_code
    assert catalog["status"] == expected_catalog_status
    assert catalog["code"] == (
        "connection_check_required" if expected_catalog_status == "blocked" else expected_code
    )
    assert dataset_rid is None or dataset_rid not in run.checks_json


@pytest.mark.parametrize(
    ("change", "expected_status", "expected_code"),
    [
        ("rotate", "blocked", "credentials_stale"),
        ("revoke", "blocked", "administrator_unavailable"),
        ("provider_denial", "failed", "permission_denied"),
    ],
)
def test_databricks_diagnostic_guard_classifies_provider_and_run_changes(
    client, monkeypatch, change, expected_status, expected_code
) -> None:
    from app.config import get_settings
    from app.connectors.errors import ConnectorError, TransferErrorCode
    from app.services.catalogs import UserCatalog

    web_login(client, ADMIN_EMAIL, ADMIN_PASSWORD)
    with SessionLocal() as db:
        admin = db.scalar(select(User).where(User.email == ADMIN_EMAIL))
        assert admin is not None
        store_user_credentials(
            db,
            get_settings(),
            user=admin,
            provider="databricks",
            credentials={
                "workspace_url": "https://databricks.demo.invalid",
                "token": "synthetic-diagnostics-token",
                "compute_kind": "warehouse",
                "compute_id": "diagnostics-warehouse",
                "http_path": "sql/1.0/warehouses/diagnostics-warehouse",
                "startup_policy": "manual",
            },
        )

    original_list_namespaces = UserCatalog.list_namespaces

    def change_after_credentials_resolve(self, provider):
        if provider == "databricks":
            if change == "provider_denial":
                raise ConnectorError(
                    TransferErrorCode.PERMISSION_DENIED,
                    "synthetic provider denial",
                    retryable=False,
                )
            credentials = self._credentials_for(provider)
            with SessionLocal() as second_session:
                if change == "revoke":
                    admin = second_session.scalar(select(User).where(User.email == ADMIN_EMAIL))
                    assert admin is not None
                    admin.status = "inactive"
                else:
                    secret = second_session.scalar(
                        select(UserSecret).where(
                            UserSecret.user_id == self.user.id,
                            UserSecret.provider == provider,
                        )
                    )
                    assert secret is not None
                    secret.updated_at += timedelta(seconds=1)
                second_session.commit()
            credentials["_authorization_check"]()
        return original_list_namespaces(self, provider)

    monkeypatch.setattr(UserCatalog, "list_namespaces", change_after_credentials_resolve)
    page = client.get("/admin/diagnostics")
    started = client.post(
        "/admin/diagnostics/run",
        data={"csrf_token": csrf_from(page.text)},
        follow_redirects=False,
    )
    run_id = parse_qs(urlparse(started.headers["location"]).query)["run_id"][0]

    with SessionLocal() as db:
        run = db.get(LiveDiagnosticRun, run_id)
        assert run is not None
        checks = json.loads(run.checks_json)
        catalog = next(check for check in checks if check["key"] == "databricks.catalog")
        assert catalog["status"] == expected_status
        assert catalog["code"] == expected_code
        if change == "revoke":
            assert all(check["status"] != "pending" for check in checks)
