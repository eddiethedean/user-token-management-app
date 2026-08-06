"""Tests that exercise Hedron's built-in testing helpers."""

from __future__ import annotations

from types import SimpleNamespace

from hedron.testing import (
    AdapterResponse,
    assert_fragment_body,
    assert_html_contains,
    assert_page_document,
    assert_renders,
    fastapi_fixture,
    fragment_client,
    render_html,
)
from hedron_core import RenderMode

from app.ui import partials as ui
from app.ui.layout import alert_box, page_heading


def _preauth_token(html: str) -> str:
    assert 'name="preauth_csrf_token"' in html
    start = html.index('name="preauth_csrf_token"')
    snippet = html[start : start + 240]
    return snippet.split('value="')[1].split('"')[0]


def _session_csrf(html: str) -> str:
    assert 'name="csrf_token"' in html
    start = html.index('name="csrf_token"')
    snippet = html[start : start + 240]
    return snippet.split('value="')[1].split('"')[0]


def test_login_page_document(access_app) -> None:
    fixture = fastapi_fixture(access_app)
    response = fixture.get("/login")
    assert_page_document(response)
    assert_html_contains(response, "Sign in")
    assert_html_contains(response, 'name="preauth_csrf_token"')
    assert_html_contains(response, 'name="htmx-config"')


def test_register_page_document(access_app) -> None:
    fixture = fastapi_fixture(access_app)
    response = fixture.get("/register")
    assert_page_document(response)
    assert_html_contains(response, "Request access")


def test_login_then_profile_via_fastapi_fixture(access_app) -> None:
    fixture = fastapi_fixture(access_app)
    login_page = fixture.get("/login")
    assert_page_document(login_page)
    token = _preauth_token(login_page.body)

    # TestClient inside fastapi_fixture follows redirects and keeps cookies.
    profile = fixture.post(
        "/login",
        data={
            "email": "admin@example.gov",
            "password": "Tr0pic-Maple!River92",
            "preauth_csrf_token": token,
            "next": "/profile",
        },
    )
    assert_page_document(profile)
    assert_html_contains(profile, "Your profile")
    assert_html_contains(profile, "admin@example.gov")


def test_htmx_profile_update_returns_fragment(access_app) -> None:
    client = fragment_client(access_app)
    login_page = client.get("/login")
    token = _preauth_token(login_page.text)
    signed_in = client.post(
        "/login",
        data={
            "email": "admin@example.gov",
            "password": "Tr0pic-Maple!River92",
            "preauth_csrf_token": token,
            "next": "/profile",
        },
        follow_redirects=True,
    )
    assert signed_in.status_code == 200
    csrf = _session_csrf(signed_in.text)

    response = client.post(
        "/profile",
        data={
            "csrf_token": csrf,
            "full_name": "Hedron Admin",
            "organization": "Access Registry",
            "job_title": "Tester",
            "phone": "",
        },
    )
    adapter = AdapterResponse(response.status_code, response.text, dict(response.headers))
    assert_fragment_body(adapter, contains="profile-form-region")
    assert_html_contains(adapter, "Hedron Admin")
    assert_html_contains(adapter, "Your profile has been updated")
    assert_html_contains(adapter, "hedron-toast")


def test_htmx_admin_users_requires_auth(access_app) -> None:
    client = fragment_client(access_app)
    response = client.get(
        "/admin/users",
        headers={"HX-Target": "#user-directory", "Accept": "text/html"},
        follow_redirects=False,
    )
    assert response.status_code in {303, 401, 302}
    if response.status_code in {302, 303}:
        assert "/login" in response.headers.get("location", "")


def test_alert_and_heading_render_helpers() -> None:
    assert_renders(alert_box("Saved.", kind="success"), contains="Saved.")
    assert_renders(alert_box("Broken."), contains="Broken.")
    html = assert_renders(
        page_heading("Workspace", "Your profile", "Update account details."),
        contains="Your profile",
        mode=RenderMode.FRAGMENT,
    )
    assert "Workspace" in html
    assert "Update account details." in html


def test_profile_form_render_html() -> None:
    auth = SimpleNamespace(
        user=SimpleNamespace(
            full_name="Ada Admin",
            email_original="ada@example.gov",
            organization="DoD",
            job_title="Engineer",
            phone="",
            status="active",
            role_names=["administrator"],
        )
    )
    html = render_html(ui.profile_form(auth, csrf_token="test-csrf"))
    assert 'id="profile-form-region"' in html
    assert 'name="csrf_token"' in html
    assert 'value="Ada Admin"' in html
    assert 'hx-post="/profile"' in html


def test_password_form_render_html() -> None:
    html = render_html(ui.password_form(csrf_token="pw-csrf"))
    assert 'id="password-form-region"' in html
    assert 'hx-post="/security/password"' in html
    assert 'data-password-toggle="new_password"' in html


def test_password_form_success_swaps_to_sign_in() -> None:
    html = render_html(ui.password_form(csrf_token="pw-csrf", success="Password changed."))
    assert "Password changed." in html
    assert "Return to sign in" in html
    assert 'hx-post="/security/password"' not in html


def test_user_directory_fragment_render() -> None:
    html = render_html(
        ui.user_directory(
            [],
            csrf_token="admin-csrf",
            query="",
            status_filter="",
            page=1,
            page_count=1,
        )
    )
    assert 'id="user-directory"' in html
    assert 'id="user-directory-body"' in html
    assert "No users found." in html


def test_session_list_and_secret_slot_render_html() -> None:
    from datetime import datetime

    from app.services.secrets import SECRET_PROVIDERS

    auth = SimpleNamespace(session=SimpleNamespace(id="current-session"))
    sessions = [
        SimpleNamespace(
            id="current-session",
            user_agent="TestClient",
            last_seen_at=datetime(2026, 1, 1, 12, 0, 0),
            source_ip="127.0.0.1",
        ),
        SimpleNamespace(
            id="other-session",
            user_agent="Other Browser",
            last_seen_at=datetime(2026, 1, 1, 11, 0, 0),
            source_ip="10.0.0.2",
        ),
    ]
    session_html = render_html(ui.session_list(sessions, auth=auth, csrf_token="sess-csrf"))
    assert 'id="session-list"' in session_html
    assert "Current" in session_html
    assert "Revoke" in session_html
    assert "security/sessions/other-session/revoke" in session_html
    assert "data-hedron-dialog-open" in session_html

    slot = render_html(ui.secret_slot(SECRET_PROVIDERS[0], None, csrf_token="sec-csrf"))
    assert 'id="secret-slot-advana"' in slot
    assert "ADVANA_API_TOKEN" in slot
    assert "security/secrets/advana" in slot


def test_audit_results_and_invitation_panel_render_html() -> None:
    from datetime import datetime

    events = [
        SimpleNamespace(
            occurred_at=datetime(2026, 1, 1, 12, 0, 0),
            event_type="auth.login",
            outcome="success",
            source_ip="127.0.0.1",
            detail="{}",
        )
    ]
    audit = render_html(
        ui.audit_results(
            events,
            event_type_filter="auth.login",
            outcome_filter="",
            current_page=1,
            page_count=1,
            total_events=1,
        )
    )
    assert 'id="audit-results-region"' in audit
    assert "auth.login" in audit

    panel = render_html(
        ui.invitation_panel(
            [],
            [SimpleNamespace(name="user"), SimpleNamespace(name="administrator")],
            csrf_token="inv-csrf",
        )
    )
    assert 'id="invitation-panel"' in panel
    assert "admin/invitations" in panel


def test_login_via_page_fixture_uses_hedron_asserts(page) -> None:
    from tests.helpers import fixture_login

    profile = fixture_login(page)
    assert_page_document(profile)
    assert_html_contains(profile, "Your profile")
    assert_html_contains(profile, "admin@example.gov")


def test_authenticated_shell_has_main_panel_and_toast_host(page) -> None:
    from tests.helpers import fixture_login

    profile = fixture_login(page)
    assert_html_contains(profile, 'id="main-panel"')
    assert_html_contains(profile, 'id="toast-host"')
    assert_html_contains(profile, 'id="side-nav"')
    assert_html_contains(profile, 'hx-target="#main-panel"')
    assert_html_contains(profile, 'hx-select="#main-panel"')
    assert_html_contains(profile, 'hx-select-oob="#side-nav"')
    assert_html_contains(profile, "historyCacheSize")


def test_htmx_nav_swaps_main_panel_without_shell_chrome(access_app) -> None:
    client = fragment_client(access_app)
    login_page = client.get("/login")
    token = _preauth_token(login_page.text)
    signed_in = client.post(
        "/login",
        data={
            "email": "admin@example.gov",
            "password": "Tr0pic-Maple!River92",
            "preauth_csrf_token": token,
            "next": "/profile",
        },
        follow_redirects=True,
    )
    assert signed_in.status_code == 200

    security = client.get(
        "/security",
        headers={"HX-Target": "#main-panel", "Accept": "text/html"},
    )
    adapter = AdapterResponse(security.status_code, security.text, dict(security.headers))
    assert security.status_code == 200
    assert_fragment_body(adapter, contains="main-panel")
    assert_html_contains(adapter, "security-tabs")
    assert_html_contains(adapter, "hx-swap-oob")
    assert "<!doctype" not in security.text.lower()
    assert 'class="site-header"' not in security.text
    assert security.headers.get("HX-Push-Url")


def test_htmx_profile_update_emits_toast_oob(access_app) -> None:
    client = fragment_client(access_app)
    login_page = client.get("/login")
    token = _preauth_token(login_page.text)
    signed_in = client.post(
        "/login",
        data={
            "email": "admin@example.gov",
            "password": "Tr0pic-Maple!River92",
            "preauth_csrf_token": token,
            "next": "/profile",
        },
        follow_redirects=True,
    )
    csrf = _session_csrf(signed_in.text)
    response = client.post(
        "/profile",
        data={
            "csrf_token": csrf,
            "full_name": "Toast Admin",
            "organization": "Access Registry",
            "job_title": "Tester",
            "phone": "",
        },
        headers={"HX-Target": "#profile-form-region", "Accept": "text/html"},
    )
    adapter = AdapterResponse(response.status_code, response.text, dict(response.headers))
    assert_fragment_body(adapter, contains="profile-form-region")
    assert_html_contains(adapter, "Your profile has been updated")
    assert_html_contains(adapter, "hedron-toast")
    assert 'hx-swap-oob="beforeend"' in response.text
    assert 'id="toast-host"' in response.text
    assert "toast-item" in response.text


def test_session_list_uses_dialog_confirm(access_app) -> None:
    from datetime import datetime

    auth = SimpleNamespace(session=SimpleNamespace(id="current-session"))
    sessions = [
        SimpleNamespace(
            id="current-session",
            user_agent="TestClient",
            last_seen_at=datetime(2026, 1, 1, 12, 0, 0),
            source_ip="127.0.0.1",
        ),
        SimpleNamespace(
            id="other-session",
            user_agent="Other Browser",
            last_seen_at=datetime(2026, 1, 1, 11, 0, 0),
            source_ip="10.0.0.2",
        ),
    ]
    session_html = render_html(ui.session_list(sessions, auth=auth, csrf_token="sess-csrf"))
    assert 'data-hedron-dialog-open="#revoke-session-other-session"' in session_html
    assert 'id="revoke-session-other-session"' in session_html
    assert "hedron-dialog" in session_html


def test_admin_pagination_uses_hedron_markup() -> None:
    html = render_html(
        ui.hedron_pagination(
            page=2,
            page_size=10,
            total=45,
            base_path="/admin/users",
            target="#user-directory-body",
        )
    )
    assert "hedron-pagination" in html
    assert 'hx-target="#user-directory-body"' in html
    assert 'hx-swap="innerHTML"' in html
    assert "page=2" in html


def test_security_activity_lazy_placeholder() -> None:
    html = render_html(ui.security_activity_lazy())
    assert 'id="security-activity"' in html
    assert "hedron-loading" in html
    assert 'hx-get="/security/activity"' in html
    assert 'hx-swap="outerHTML"' in html


def test_password_form_field_errors() -> None:
    html = render_html(
        ui.password_form(
            csrf_token="csrf",
            field_errors={"new_password_confirm": "New passwords do not match."},
        )
    )
    assert 'id="new_password_confirm-error"' in html
    assert "hedron-field-error" in html
    assert "do not match" in html
    assert 'aria-invalid="true"' in html


def test_audit_results_lazy_uses_hedron_lazy() -> None:
    html = render_html(ui.audit_results_lazy())
    assert 'id="audit-results-region"' in html
    assert "hedron-loading" in html
    assert 'hx-get="/admin/audit"' in html
    assert "Loading audit activity" in html
