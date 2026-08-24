"""Robust Hedron interaction tests — shell swaps, toasts, dialogs, regions, redirects.

Uses hedron.testing helpers (AppScenario, fastapi_fixture, fragment_client, assert_*) throughout.
"""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

from hedron.testing import (
    AppScenario,
    assert_budget,
    assert_fragment_body,
    assert_html_contains,
    assert_oob_present,
    assert_page_document,
    assert_renders,
    assert_shell_dual_path,
    assert_toast_markup,
    assert_ui_targets_subset_of_regions,
    fastapi_fixture,
    fragment_client,
    render_html,
)
from hedron_core import RenderMode
from sqlalchemy import select
from starlette.requests import Request

from app.config import get_settings
from app.database import SessionLocal
from app.models import RefreshSession
from app.security.tokens import decode_access_token
from app.ui import partials as ui
from app.ui.interactions import APP_REGIONS
from app.ui.layout import alert_box, main_panel, page_heading
from tests.helpers import (
    ADMIN_PASSWORD,
    NEW_PASSWORD,
    USER_PASSWORD,
    as_adapter,
    assert_hx_push_url,
    assert_hx_redirect,
    assert_no_document_shell,
    csrf_from,
    fixture_login,
    htmx_login,
)


def _request(root_path: str = "") -> Request:
    return Request(
        {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": "/",
            "raw_path": b"/",
            "query_string": b"",
            "headers": [],
            "client": ("127.0.0.1", 0),
            "server": ("test", 80),
            "root_path": root_path,
        }
    )


def test_auth_and_shell_pages_are_documents(page) -> None:
    login = page.get("/login")
    assert_page_document(login)
    assert_html_contains(login, "Sign in")
    assert_html_contains(login, 'name="htmx-config"')

    changed = page.get("/login?password=changed")
    assert_page_document(changed)
    assert_html_contains(changed, "Password changed")

    profile = fixture_login(page)
    assert_page_document(profile)
    assert_html_contains(profile, 'id="main-panel"')
    assert_html_contains(profile, 'id="hedron-toast"')
    assert_html_contains(profile, 'id="dialog-host"')
    assert_html_contains(profile, 'id="global-request-indicator"')
    assert_html_contains(profile, 'data-hedron-navigation-tabs="true"')
    assert_html_contains(profile, 'data-navigation-tab-label="Profile"')
    assert_html_contains(profile, 'hx-select="#main-panel"')
    assert 'hx-select-oob="#side-nav"' not in profile.body
    assert_budget(profile.body, max_bytes=250_000)


def test_main_panel_nav_swaps_all_authenticated_routes(htmx) -> None:
    htmx_login(htmx)
    routes = (
        ("/pipeline", "pipeline-builder", "Pipeline workspace"),
        ("/profile", "account-tabs", "Profile details"),
        ("/security", "security-tabs", "Workspace settings"),
        ("/admin/users", "user-directory", "Users and invitations"),
        ("/admin/audit", "audit-results-region", "Audit activity"),
    )
    for path, marker, heading in routes:
        response = htmx.get(
            path,
            headers={"HX-Target": "#main-panel", "Accept": "text/html"},
        )
        adapter = as_adapter(response)
        assert_fragment_body(adapter, contains=marker)
        assert_html_contains(adapter, heading)
        assert_no_document_shell(adapter)
        assert_hx_push_url(adapter)
        assert "hx-swap-oob" in response.text  # side-nav active state
        assert_budget(response.text, max_bytes=200_000)


def test_history_restore_returns_full_document(htmx) -> None:
    htmx_login(htmx)
    response = htmx.get(
        "/admin/users",
        params={"q": "restore"},
        headers={
            "HX-Target": "#main-panel",
            "HX-History-Restore-Request": "true",
            "Accept": "text/html",
        },
    )
    assert response.status_code == 200
    assert 'id="main-panel"' in response.text
    assert 'id="side-nav"' in response.text
    assert "hedron-app-shell-header" in response.text
    assert 'class="hedron-app-shell-nav"' in response.text


def test_toast_oob_appends_for_queueing() -> None:
    from hedron import Fragment, Toast, html
    from hedron.testing import render_html
    from hedron_core.interaction import InteractionResult, OobUpdate, materialize_interaction_nodes

    from app.ui.interactions import APP_POLICY

    result = InteractionResult(
        content=html.div("ok"),
        oob=(
            OobUpdate(
                content=Fragment(
                    Toast("First", tone="success"),
                    Toast("Second", tone="danger"),
                ),
                element_id="hedron-toast",
                swap="beforeend",
            ),
        ),
        policy=APP_POLICY,
    )
    markup = render_html(materialize_interaction_nodes(result))
    assert markup.count('hx-swap-oob="beforeend"') == 1
    assert "toast-item" not in markup
    assert "hedron-toast-success" in markup
    assert "hedron-toast-danger" in markup
    assert "First" in markup and "Second" in markup


def test_undeclared_hx_target_is_rejected(htmx) -> None:
    signed_in = htmx_login(htmx)
    csrf = csrf_from(signed_in.body)
    rejected = htmx.post(
        "/profile",
        data={
            "csrf_token": csrf,
            "full_name": "Nope",
            "organization": "",
            "job_title": "",
            "phone": "",
        },
        headers={"HX-Target": "#not-a-declared-region", "Accept": "text/html"},
    )
    # AR's HTTPException handler opaques FragmentRegionError details for clients;
    # status 403 is the fail-closed contract exercised here.
    assert rejected.status_code == 403


def test_shell_dual_path_security(client) -> None:
    from tests.helpers import web_login

    web_login(client)
    fragment = as_adapter(
        client.get(
            "/security",
            headers={"HX-Request": "true", "HX-Target": "#main-panel", "Accept": "text/html"},
        )
    )
    document = as_adapter(client.get("/security"))
    assert_shell_dual_path(fragment, document, fragment_contains="main-panel")
    assert_html_contains(fragment, "security-tabs")
    assert_hx_push_url(fragment)


def test_profile_mutation_toast_and_identity_oob(htmx) -> None:
    signed_in = htmx_login(htmx)
    csrf = csrf_from(signed_in.body)
    response = htmx.post(
        "/profile",
        data={
            "csrf_token": csrf,
            "full_name": "Interaction Admin",
            "organization": "Hedron QA",
            "job_title": "Tester",
            "phone": "555-0100",
        },
        headers={"HX-Target": "#profile-form-region", "Accept": "text/html"},
    )
    adapter = as_adapter(response)
    assert_fragment_body(adapter, contains="profile-form-region")
    assert_html_contains(adapter, "Interaction Admin")
    assert_toast_markup(adapter, contains="Your profile has been updated")
    assert_oob_present(adapter, contains="hedron-toast")
    assert "Vary" in response.headers


def test_password_htmx_error_fragment_and_success_redirect(htmx) -> None:
    signed_in = htmx_login(htmx)
    _ = signed_in
    csrf = csrf_from(htmx.get("/profile").text)

    mismatch = htmx.post(
        "/profile/password",
        data={
            "csrf_token": csrf,
            "current_password": ADMIN_PASSWORD,
            "new_password": NEW_PASSWORD,
            "new_password_confirm": "not-the-same-password",
        },
        headers={"HX-Target": "#password-form-region", "Accept": "text/html"},
    )
    adapter = as_adapter(mismatch)
    assert mismatch.status_code == 400
    assert_html_contains(adapter, "password-form-region")
    assert_html_contains(adapter, "do not match")
    assert_html_contains(adapter, "new_password_confirm-error")
    assert_no_document_shell(adapter)

    success = htmx.post(
        "/profile/password",
        data={
            "csrf_token": csrf_from(htmx.get("/profile").text),
            "current_password": ADMIN_PASSWORD,
            "new_password": NEW_PASSWORD,
            "new_password_confirm": NEW_PASSWORD,
        },
        headers={"HX-Target": "#password-form-region", "Accept": "text/html"},
        follow_redirects=False,
    )
    assert_hx_redirect(as_adapter(success), "password=changed")


def test_session_revoke_with_second_session(access_app, make_user) -> None:
    user = make_user("revoke.pair@example.gov")
    primary = fragment_client(access_app)
    secondary = fragment_client(access_app)
    htmx_login(primary, user.email, USER_PASSWORD)
    htmx_login(secondary, user.email, USER_PASSWORD)

    access = primary.cookies.get("access_registry_access")
    assert access
    current_sid = decode_access_token(access, get_settings())["sid"]

    with SessionLocal() as db:
        sessions = list(
            db.scalars(
                select(RefreshSession).where(
                    RefreshSession.user_id == user.id,
                    RefreshSession.revoked_at.is_(None),
                )
            )
        )
        assert len(sessions) >= 2
        remote = next(session for session in sessions if session.id != current_sid)
        remote_id = remote.id

    account = primary.get("/profile")
    csrf = csrf_from(account.text)
    assert 'data-hedron-dialog-open="#revoke-session-' in account.text

    revoked = primary.post(
        f"/profile/sessions/{remote_id}/revoke",
        data={"csrf_token": csrf},
        headers={"HX-Target": "#session-list", "Accept": "text/html"},
    )
    adapter = as_adapter(revoked)
    assert_fragment_body(adapter, contains="session-list")
    assert_html_contains(adapter, "hedron-toast")
    assert_html_contains(adapter, "browser session was revoked")
    assert_no_document_shell(adapter)

    with SessionLocal() as db:
        session = db.get(RefreshSession, remote_id)
        assert session is not None and session.revoked_at is not None


def test_security_activity_lazy_fragment(htmx) -> None:
    htmx_login(htmx)
    response = htmx.get(
        "/profile/activity",
        headers={"HX-Target": "#security-activity", "Accept": "text/html"},
    )
    adapter = as_adapter(response)
    assert_fragment_body(adapter, contains="security-activity")
    assert_no_document_shell(adapter)
    assert "hedron-loading" not in response.text
    assert "hedron-timeline" in response.text
    assert "hedron-badge-success" in response.text


def test_security_activity_undeclared_target_rejected(htmx) -> None:
    htmx_login(htmx)
    rejected = htmx.get(
        "/profile/activity",
        headers={"HX-Target": "#not-a-declared-region", "Accept": "text/html"},
    )
    # Route allowlist is SECURITY_ACTIVITY only; AR opaques the Hedron diagnostic body.
    assert rejected.status_code == 403


def test_app_scenario_document_asserts(page) -> None:
    """AppScenario assert helpers over fastapi_fixture document responses."""
    scenario = AppScenario.from_fixture(page)
    profile = fixture_login(page)
    scenario.assert_page_document(profile)
    scenario.assert_html_contains('id="main-panel"', response=profile)
    security = page.get("/security")
    scenario.assert_page_document(security)
    scenario.assert_html_contains("security-tabs", response=security)
    assert "lazy-refresh" not in security.body
    scenario.assert_html_contains("account-tabs", response=profile)
    scenario.assert_html_contains('data-hedron-align="end"', response=profile)
    assert_ui_targets_subset_of_regions(security.body, APP_REGIONS)


def test_admin_invite_and_toggle_toasts(htmx, make_user) -> None:
    target = make_user("toggle.target@example.gov")
    htmx_login(htmx, next_path="/admin/users")
    csrf = csrf_from(htmx.get("/admin/users").text)

    invited = htmx.post(
        "/admin/invitations",
        data={"csrf_token": csrf, "email": "invite.htmx@example.gov", "role": "user"},
        headers={"HX-Target": "#invitation-panel", "Accept": "text/html"},
    )
    invite_adapter = as_adapter(invited)
    assert_fragment_body(invite_adapter, contains="invitation-panel")
    assert_html_contains(invite_adapter, "hedron-toast")
    assert_html_contains(invite_adapter, "Invitation queued")
    assert_html_contains(invite_adapter, "data-hedron-dialog-open")

    toggled = htmx.post(
        f"/admin/users/{target.id}/toggle",
        data={
            "csrf_token": csrf_from(htmx.get("/admin/users").text),
            "q": "",
            "status": "",
            "page": "1",
        },
        headers={"HX-Target": "#user-directory-body", "Accept": "text/html"},
    )
    toggle_adapter = as_adapter(toggled)
    assert_fragment_body(toggle_adapter, contains="user-directory-body")
    assert_html_contains(toggle_adapter, "hedron-toast")
    assert_html_contains(toggle_adapter, "account status was updated")
    assert_html_contains(toggle_adapter, "hedron-dialog")


def test_admin_directory_and_audit_filter_fragments(htmx, make_user) -> None:
    make_user("filter.hedron@example.gov")
    htmx_login(htmx)

    directory = htmx.get(
        "/admin/users",
        params={"q": "filter.hedron"},
        headers={"HX-Target": "#user-directory", "Accept": "text/html"},
    )
    dir_adapter = as_adapter(directory)
    assert_fragment_body(dir_adapter, contains="user-directory")
    assert_html_contains(dir_adapter, "filter.hedron@example.gov")
    assert_html_contains(dir_adapter, "user-directory-body")
    assert_no_document_shell(dir_adapter)
    assert_budget(directory.text, max_bytes=150_000)

    body = htmx.get(
        "/admin/users",
        params={"q": "filter.hedron", "page": "1"},
        headers={"HX-Target": "#user-directory-body", "Accept": "text/html"},
    )
    body_adapter = as_adapter(body)
    assert_fragment_body(body_adapter, contains="user-directory-body")
    assert "HX-Reswap" in body.headers
    assert body.headers["HX-Reswap"] == "outerHTML"

    audit = htmx.get(
        "/admin/audit",
        params={"event_type": "auth.login"},
        headers={"HX-Target": "#audit-results-region", "Accept": "text/html"},
    )
    audit_adapter = as_adapter(audit)
    assert_fragment_body(audit_adapter, contains="audit-results-region")
    assert_no_document_shell(audit_adapter)

    lazy = htmx.get(
        "/admin/audit/results",
        headers={"HX-Target": "#audit-results-region", "Accept": "text/html"},
    )
    lazy_adapter = as_adapter(lazy)
    assert_fragment_body(lazy_adapter, contains="audit-results-region")
    assert_oob_present(lazy_adapter, contains="audit-match-count")
    assert_no_document_shell(lazy_adapter)


def test_audit_full_page_lazy_placeholder(page) -> None:
    fixture_login(page, next_path="/admin/audit")
    audit = page.get("/admin/audit")
    assert_page_document(audit)
    assert_html_contains(audit, "Loading audit activity")
    assert_html_contains(audit, 'hx-get="/admin/audit/results"')
    assert_html_contains(audit, 'id="audit-results-region"')
    assert_html_contains(audit, 'data-hedron-align="end"')
    assert_html_contains(audit, 'hx-target="#audit-results-region"')
    assert_ui_targets_subset_of_regions(audit.body, APP_REGIONS)


def test_non_admin_cannot_nav_swap_admin_panels(htmx, make_user) -> None:
    user = make_user("standard.nav@example.gov")
    htmx_login(htmx, user.email, USER_PASSWORD)
    for path in ("/admin/users", "/admin/audit"):
        response = htmx.get(
            path,
            headers={"HX-Target": "#main-panel", "Accept": "text/html"},
            follow_redirects=False,
        )
        assert response.status_code == 403


def test_component_renders_via_hedron_assert_renders() -> None:
    assert_renders(
        alert_box("Saved via toast path.", kind="success"), contains="Saved via toast path."
    )
    assert_renders(
        page_heading("Workspace", "Security", "Manage tokens and sessions."),
        contains="Security",
        mode=RenderMode.FRAGMENT,
    )
    assert_renders(main_panel(page_heading("A", "B", "C")), contains="main-panel")

    auth = SimpleNamespace(session=SimpleNamespace(id="current"))
    sessions = [
        SimpleNamespace(
            id="current",
            user_agent="Primary",
            last_seen_at=datetime(2026, 1, 1, 12, 0, 0),
            source_ip="127.0.0.1",
        ),
        SimpleNamespace(
            id="remote",
            user_agent="Remote",
            last_seen_at=datetime(2026, 1, 1, 11, 0, 0),
            source_ip="10.0.0.2",
        ),
    ]
    html = assert_renders(
        ui.session_list(_request(), sessions, auth=auth, csrf_token="csrf"),
        contains="hedron-dialog",
    )
    assert "data-hedron-dialog-open" in html

    pagination = assert_renders(
        ui.hedron_pagination(
            page=1,
            page_size=10,
            total=35,
            base_path="/admin/audit",
            target="#audit-results-body",
        ),
        contains="hedron-pagination",
    )
    assert 'hx-swap="innerHTML"' in pagination
    assert 'hx-target="#audit-results-body"' in pagination
    assert "page=3" in pagination


def test_connection_and_account_tabs_keep_controls_in_the_right_section() -> None:
    auth = SimpleNamespace(
        session=SimpleNamespace(id="s1"),
        user=SimpleNamespace(role_names=["user"]),
    )
    connection_html = render_html(
        ui.security_tabs(
            _request(),
            csrf_token="csrf",
            secret_slots=[],
        )
    )
    assert 'id="security-tabs"' in connection_html
    assert "Credentials" in connection_html
    assert "Status" in connection_html
    assert "Password" not in connection_html
    assert "Sessions" not in connection_html
    assert "Activity" not in connection_html

    account_html = render_html(
        ui.account_tabs(
            _request(),
            csrf_token="csrf",
            local_password=True,
            sessions=[],
            auth=auth,
            profile_content="Profile details",
        )
    )
    assert 'id="account-tabs"' in account_html
    assert "Profile" in account_html
    assert "Password" in account_html
    assert "Sessions" in account_html
    assert "Activity" in account_html
    assert "Credentials" not in account_html
    assert "Status" not in account_html
    assert 'hx-get="/profile/activity"' in account_html
    assert "hedron-loading" in account_html
    assert 'data-hedron-align="end"' in account_html
    assert 'hx-target="#security-activity-body"' in account_html
    assert_ui_targets_subset_of_regions(account_html, APP_REGIONS)


def test_fastapi_fixture_admin_round_trip(access_app, make_user) -> None:
    make_user("fixture.roundtrip@example.gov")
    fixture = fastapi_fixture(access_app)
    profile = fixture_login(fixture)
    assert_page_document(profile)

    users = fixture.get("/admin/users")
    assert_page_document(users)
    assert_html_contains(users, "fixture.roundtrip@example.gov")
    assert_html_contains(users, "hedron-dialog")

    security = fixture.get("/security")
    assert_page_document(security)
    assert_html_contains(security, "security-tabs")
