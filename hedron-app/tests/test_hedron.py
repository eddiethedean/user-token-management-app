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


def test_login_page_document(hedron_app) -> None:
    fixture = fastapi_fixture(hedron_app)
    response = fixture.get("/login")
    assert_page_document(response)
    assert_html_contains(response, "Sign in")
    assert_html_contains(response, 'name="preauth_csrf_token"')
    assert_html_contains(response, 'name="htmx-config"')


def test_register_page_document(hedron_app) -> None:
    fixture = fastapi_fixture(hedron_app)
    response = fixture.get("/register")
    assert_page_document(response)
    assert_html_contains(response, "Request access")


def test_login_then_profile_via_fastapi_fixture(hedron_app) -> None:
    fixture = fastapi_fixture(hedron_app)
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


def test_htmx_profile_update_returns_fragment(hedron_app) -> None:
    client = fragment_client(hedron_app)
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


def test_htmx_admin_users_requires_auth(hedron_app) -> None:
    client = fragment_client(hedron_app)
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
    assert 'id="user-table"' in html
    assert "No users found." in html
