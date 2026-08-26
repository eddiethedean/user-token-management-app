"""Tests that exercise Hedron's built-in testing helpers."""

from __future__ import annotations

import re
from collections import Counter
from types import SimpleNamespace

import pytest
from hedron.testing import (
    AdapterResponse,
    assert_fragment_body,
    assert_html_contains,
    assert_page_document,
    assert_renders,
    assert_ui_targets_subset_of_regions,
    fastapi_fixture,
    fragment_client,
    render_html,
)
from hedron_core import RenderMode
from starlette.requests import Request

from app.ui import partials as ui
from app.ui.design_system import (
    APP_SHELL_NAV_STYLE_CLASS,
    DATA_MOVER_DESIGN,
    DATA_MOVER_MOTION_RECIPES,
    DATA_MOVER_PRESENTATION,
    DATA_MOVER_SCOPED_STYLES,
    DATA_MOVER_THEME_EXPORT,
    PROCESS_FLOW_STEP_STYLE_CLASS,
    DataMoverPageHeader,
)
from app.ui.forms import submit_button
from app.ui.hedron_styles import desktop_default_styles
from app.ui.interactions import APP_REGIONS
from app.ui.layout import alert_box, document_head, page_heading
from app.ui.urls import hx_attrs
from tests.helpers import assert_redirect_path


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
    assert_html_contains(response, 'href="/app-assets/hedron-desktop.css?v=2"')
    assert_html_contains(response, 'href="/assets/theme.css"')
    assert_html_contains(response, 'href="/app-assets/data-mover-components.css?v=6"')
    assert_html_contains(
        response,
        'type="image/png" href="/assets/brand/data-mover-mark.png" rel="icon"',
    )
    assert_html_contains(response, 'src="/assets/brand/data-mover-mark-light.png"')
    assert_html_contains(response, 'srcset="/assets/brand/data-mover-mark-dark.png"')
    assert_html_contains(response, 'src="/assets/brand/cdao-mark.png"')
    assert_html_contains(response, "Chief Digital and Artificial Intelligence Office")
    assert_html_contains(response, 'data-hedron-max-width="lg"')
    assert_html_contains(response, 'data-hedron-resource-list="true"')
    assert_html_contains(response, 'aria-label="Workspace protections"')
    assert_html_contains(response, "Continue to workspace")

    dark = fixture.get("/login", cookies={"data_mover_color_mode": "dark"})
    assert_html_contains(dark, 'src="/assets/brand/data-mover-mark-dark.png"')


def test_hedron_component_bundles_are_served(access_app) -> None:
    fixture = fastapi_fixture(access_app)
    response = fixture.get("/app-assets/data-mover-components.css")
    assert response.status_code == 200
    assert ".hedron-card--glass" in response.body
    assert "--hedron-color-bg: #f6f6fb" in response.body
    assert "--hedron-color-bg: #080d16" in response.body
    assert "--hedron-type-display-size" in response.body
    assert "--hedron-geometry-control-height" in response.body
    assert "--hedron-glass-opacity: 78%" in response.body
    assert "--hedron-glass-blur: 18px" in response.body
    assert "--hedron-surface-glass-opacity: 78%" in response.body
    assert "--hedron-data-table-header-background" in response.body
    assert "--hedron-motion-elevate: 180ms" in response.body
    assert "--hedron-type-measure-narrow: 18ch" in response.body
    assert "--hedron-type-measure-narrow: 12ch" in response.body
    assert "--hedron-elevation-focus: 0 12px 32px rgb(2 8 23 / 14%)" in response.body
    assert "--hedron-color-surface" in response.body
    login_page = fixture.get("/login")
    assert 'data-hedron-environment-banner="true"' in login_page.body
    assert 'data-hedron-max-width="xl"' in login_page.body
    assert 'data-hedron-ambient-pattern="radial"' in login_page.body
    assert "hedron-surface--glass" in login_page.body
    theme = fixture.get("/assets/theme.css")
    assert theme.status_code == 200
    assert "radial-gradient" not in theme.body
    assert ":has(" not in theme.body
    assert ".hedron-text-input" not in theme.body
    assert ".hedron-app-shell-nav" not in theme.body
    assert ".hedron-card::before" not in theme.body
    desktop_styles = fixture.get("/app-assets/hedron-desktop.css")
    assert desktop_styles.status_code == 200
    assert desktop_styles.headers["content-type"].startswith("text/css")
    assert desktop_styles.headers["cache-control"] == "public, max-age=3600"
    assert not re.search(r"@media\s*\([^)]*max-width\s*:", desktop_styles.body, re.IGNORECASE)
    assert not re.search(r"@media\s*\([^)]*hover\s*:\s*none", desktop_styles.body, re.IGNORECASE)
    assert re.search(r"@media\s*\([^)]*min-width\s*:", desktop_styles.body, re.IGNORECASE)
    assert "@media (prefers-reduced-motion: reduce)" in desktop_styles.body
    assert 'href="/hedron-static/hedron-default.css"' not in fixture.get("/login").body


def test_document_head_can_disable_custom_theme() -> None:
    rendered = render_html(
        document_head(
            request=_request(),
            page_title="Theme experiment",
            app_name="Data Mover",
            custom_theme_enabled=False,
        )
    )
    assert "/assets/theme.css" not in rendered


def test_hedron_063_design_system_and_action_recipe() -> None:
    plan = DATA_MOVER_DESIGN.explain()
    assert plan.schema == "hedron.design-system-plan/1"
    assert plan.logical_id == "design:data-mover"
    assert {recipe["name"] for recipe in plan.recipes} == {
        "data-mover-primary-action",
        "data-mover-secondary-action",
        "data-mover-danger-action",
        "data-mover-panel",
        "data-mover-auth-panel",
        "data-mover-inset",
        "data-mover-compact-data",
        "data-mover-operational-status",
        "data-mover-supporting-copy",
        "data-mover-page-title",
        "data-mover-page-copy",
        "data-mover-auth-title",
        "data-mover-auth-copy",
    }
    rendered = render_html(submit_button("Run transfer"))
    assert 'data-hedron-appearance="solid"' in rendered
    assert 'data-hedron-emphasis="primary"' in rendered


def test_hedron_065_scoped_motion_and_application_style_contract(access_app) -> None:
    from hedron_core.registry import get_registry

    assert set(DATA_MOVER_MOTION_RECIPES) == {
        "instant",
        "standard",
        "emphasized",
        "reveal",
        "elevate",
        "crossfade",
    }
    assert PROCESS_FLOW_STEP_STYLE_CLASS in DATA_MOVER_SCOPED_STYLES.css
    assert APP_SHELL_NAV_STYLE_CLASS in DATA_MOVER_SCOPED_STYLES.css
    assert 'data-hedron-state~="current"' in DATA_MOVER_SCOPED_STYLES.css
    assert "@media (min-width: 56rem) and (max-width: 90rem)" in DATA_MOVER_SCOPED_STYLES.css
    assert "prefers-reduced-motion" not in DATA_MOVER_SCOPED_STYLES.css
    styles = get_registry().application_styles()
    assert [style.name for style in styles] == ["data-mover-art-direction"]
    assert styles[0].layer == "application"
    assert styles[0].global_ is True


def test_hedron_066_typography_and_context_contract(access_app) -> None:
    rendered = render_html(page_heading("Workspace", "Pipeline", "Move approved data safely."))
    assert 'data-hedron-type-measure="narrow"' in rendered
    assert 'data-hedron-type-effect="display"' in rendered
    assert 'data-hedron-type-measure="default"' in rendered
    assert 'data-hedron-type-effect="subtle"' in rendered

    direct = render_html(
        DataMoverPageHeader("Native defaults", description="No route-level CSS required.")
    )
    assert 'data-hedron-type-measure="narrow"' in direct
    assert 'data-hedron-type-effect="display"' in direct

    fixture = fastapi_fixture(access_app)
    login = fixture.get("/login")
    assert 'data-hedron-presentation="PageHeader.description=data-mover-auth-copy;' in login.body
    assert "data-mover-auth-title" in login.body
    assert 'data-hedron-type-effect="display"' in login.body


def test_hedron_064_theme_export_is_conformant() -> None:
    exported = DATA_MOVER_THEME_EXPORT.to_dict()
    assert exported["design_tokens"]
    assert exported["conformance"]["ok"] is True


def test_hedron_064_presentation_contract_is_available() -> None:
    contract = DATA_MOVER_PRESENTATION.to_dict()

    assert contract["schema"] == "hedron.presentation-contract/1"
    assert contract["container_sizes"]
    assert "checkbox" in contract["native_controls"]
    assert "table" in contract["data_chrome"]


def test_hedron_native_stylesheet_is_desktop_only() -> None:
    stylesheet = desktop_default_styles()

    assert "@media (max-width" not in stylesheet
    assert "@media (min-width" in stylesheet
    assert "@media (hover: none)" not in stylesheet
    assert "@media (prefers-reduced-motion: reduce)" in stylesheet


def test_hedron_media_filter_handles_nested_css_and_preserves_non_mobile_rules() -> None:
    from app.ui.hedron_styles import _without_viewport_media

    stylesheet = """
    .base { content: "literal { brace }"; }
    @media (max-width: 40rem) {
        .mobile { content: "removed"; }
        @supports (display: grid) { .nested { display: grid; } }
    }
    @media (hover: none) { .touch { content: "removed"; } }
    @media (min-width: 40rem) { .desktop { display: grid; } }
    @media print { .print { color: black; } }
    /* a comment with a closing brace: } */
    """

    filtered = _without_viewport_media(stylesheet)

    assert 'content: "literal { brace }"' in filtered
    assert 'content: "removed"' not in filtered
    assert ".nested" not in filtered
    assert ".touch" not in filtered
    assert ".desktop" in filtered
    assert ".print" in filtered


def test_hedron_media_filter_rejects_unbalanced_css() -> None:
    from app.ui.hedron_styles import _without_viewport_media

    with pytest.raises(ValueError, match="Unbalanced CSS block"):
        _without_viewport_media("@media (max-width: 40rem) { .mobile { display: none; }")


def test_register_page_document(access_app) -> None:
    fixture = fastapi_fixture(access_app)
    response = fixture.get("/register")
    assert_page_document(response)
    assert_html_contains(response, "Request access")
    assert_html_contains(response, 'name="preauth_csrf_token"')


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
    assert_html_contains(profile, "Account settings")
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
            "organization": "Data Mover",
            "job_title": "Tester",
            "phone": "",
        },
        headers={"HX-Target": "#profile-form-region", "Accept": "text/html"},
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
    assert response.status_code == 303
    assert_redirect_path(response, "/login", query={"next": ["/admin/users"]})
    assert response.headers["hx-redirect"] == response.headers["location"]


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
    html = render_html(ui.profile_form(_request(), auth, csrf_token="test-csrf"))
    assert 'id="profile-form-region"' in html
    assert 'name="csrf_token"' in html
    assert 'value="Ada Admin"' in html
    assert 'hx-post="/profile"' in html


def test_password_form_render_html() -> None:
    html = render_html(ui.password_form(_request(), csrf_token="pw-csrf"))
    assert 'id="password-form-region"' in html
    assert 'hx-post="/profile/password"' in html
    assert 'data-hedron-password-toggle="new_password"' in html


def test_password_form_success_swaps_to_sign_in() -> None:
    html = render_html(
        ui.password_form(_request(), csrf_token="pw-csrf", success="Password changed.")
    )
    assert "Password changed." in html
    assert "Return to sign in" in html
    assert 'hx-post="/profile/password"' not in html


def test_user_directory_fragment_render() -> None:
    html = render_html(
        ui.user_directory(
            _request(),
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
    assert "Account" in html
    assert "No users found." in html


def test_user_directory_groups_account_identity_in_one_column() -> None:
    user = SimpleNamespace(
        id="user-1",
        email_original="ada@example.gov",
        full_name="Ada Lovelace",
        status="disabled",
        role_names=["user"],
    )
    html = render_html(
        ui.user_directory(
            _request(),
            [user],
            csrf_token="admin-csrf",
            query="",
            status_filter="",
            page=1,
            page_count=1,
        )
    )

    assert ">Account</th>" in html
    assert ">Full name</th>" not in html
    assert html.index(">ada@example.gov<") < html.index(">Ada Lovelace<")
    assert 'data-hedron-density="compact"' in html
    assert 'data-hedron-sticky-header="true"' in html
    assert 'data-hedron-zebra="true"' in html


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
    session_html = render_html(
        ui.session_list(_request(), sessions, auth=auth, csrf_token="sess-csrf")
    )
    assert 'id="session-list"' in session_html
    assert "Current" in session_html
    assert "Revoke" in session_html
    assert "profile/sessions/other-session/revoke" in session_html
    assert "data-hedron-dialog-open" in session_html

    slot = render_html(ui.secret_slot(_request(), SECRET_PROVIDERS[0], None, csrf_token="sec-csrf"))
    assert 'id="secret-slot-mss"' in slot
    assert 'class="hedron-avatar"' in slot
    assert "MSS_API_TOKEN" in slot
    assert 'id="mss-endpoint"' in slot
    assert 'id="mss-token"' in slot
    assert slot.count('id="mss-token"') == 1
    assert 'id="mss-token-visibility"' in slot
    assert "security/secrets/mss" in slot
    assert 'class="hedron-action-group"' in slot

    configured_slot = render_html(
        ui.secret_slot(
            _request(),
            SECRET_PROVIDERS[0],
            SimpleNamespace(
                updated_at=datetime(2026, 1, 1, 12, 0, 0),
                validation_message="Connection ready",
            ),
            csrf_token="sec-csrf",
        )
    )
    assert "Replace credentials" in configured_slot
    assert "Delete connection" in configured_slot
    assert configured_slot.index("Replace credentials") < configured_slot.index("Delete connection")

    postgres_provider = next(
        provider for provider in SECRET_PROVIDERS if provider.name == "postgres"
    )
    postgres = render_html(
        ui.secret_slot(_request(), postgres_provider, None, csrf_token="sec-csrf")
    )
    assert 'id="secret-slot-postgres"' in postgres
    assert "DATABASE_URL" in postgres
    assert all(
        f'id="postgres-{field}"' in postgres
        for field in ("host", "port", "database", "username", "password", "sslmode")
    )

    mcscop_provider = next(provider for provider in SECRET_PROVIDERS if provider.name == "mcscop")
    mcscop = render_html(ui.secret_slot(_request(), mcscop_provider, None, csrf_token="sec-csrf"))
    assert 'id="secret-slot-mcscop"' in mcscop
    assert "MCSCOP_API_TOKEN" in mcscop
    assert all(
        f'id="mcscop-{field}"' in mcscop
        for field in ("endpoint", "token", "dataset_rid", "branch", "ca_profile")
    )


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
            _request(),
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
            _request(),
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
    assert_html_contains(profile, "Account settings")
    assert_html_contains(profile, "admin@example.gov")


def test_authenticated_shell_has_main_panel_and_toast_host(page) -> None:
    from tests.helpers import fixture_login

    profile = fixture_login(page)
    assert_html_contains(profile, 'id="main-panel"')
    assert_html_contains(profile, 'id="hedron-toast"')
    assert_html_contains(profile, 'id="side-nav"')
    assert_html_contains(profile, 'hx-target="#main-panel"')
    assert_html_contains(profile, 'hx-select="#main-panel"')
    assert_html_contains(profile, 'hx-push-url="true"')
    assert 'data-hx-push-url="true"' not in profile.body
    assert 'hx-select-oob="#side-nav"' not in profile.body
    assert_html_contains(profile, "historyCacheSize")


def test_hedron_emits_htmx_core_before_extensions(page) -> None:
    response = page.get("/login")
    core = response.body.index("hedron-static/htmx.min.js")
    head_support = response.body.index("hedron-static/ext/head-support.js")
    sse = response.body.index("hedron-static/ext/sse.js")
    assert core < head_support
    assert core < sse


def test_complete_browser_surface_is_registered_with_hedron(access_app) -> None:
    from hedron_core.registry import get_registry

    _ = access_app
    routes = [
        route for route in get_registry().routes() if route.module.startswith("app.ui.routes")
    ]
    assert Counter(route.kind for route in routes) == {
        "page": 13,
        "action": 26,
        "component": 2,
    }
    assert all(route.operation_id.startswith(f"hedron_{route.kind}_") for route in routes)
    assert all("csrf" in route.htmx_inference for route in routes if route.kind == "action")


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
            "organization": "Data Mover",
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
    assert 'id="hedron-toast"' in response.text
    assert "hedron-toast" in response.text


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
    session_html = render_html(
        ui.session_list(_request(), sessions, auth=auth, csrf_token="sess-csrf")
    )
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
    html = render_html(ui.security_activity_lazy(_request()))
    assert 'id="security-activity"' in html
    assert "hedron-loading" in html
    assert 'hx-get="/profile/activity"' in html
    assert 'hx-swap="innerHTML"' in html
    assert 'hx-target="#security-activity-body"' in html


def test_numeric_polling_intervals_are_seconds() -> None:
    assert hx_attrs(_request(), path="/status", polling=1.5)["hx-trigger"] == "every 1.5s"
    assert hx_attrs(_request(), path="/status", polling=45)["hx-trigger"] == "every 45s"
    assert hx_attrs(_request(), path="/status", polling="750ms")["hx-trigger"] == "every 750ms"


def test_region_busy_attributes_use_hedron_061_action_lifecycle() -> None:
    attrs = hx_attrs(_request(), path="/status", method="post", busy="region", indicator="#busy")
    assert attrs["data-hedron-busy"] == "region"
    assert attrs["data-hedron-action-phase"] == "idle"
    assert attrs["data-hedron-busy-indicator"] == "#busy"


def test_password_form_field_errors() -> None:
    html = render_html(
        ui.password_form(
            _request(),
            csrf_token="csrf",
            field_errors={"new_password_confirm": "New passwords do not match."},
        )
    )
    assert 'id="new_password_confirm-error"' in html
    assert "hedron-field-error" in html
    assert "do not match" in html
    assert 'aria-invalid="true"' in html


def test_audit_results_lazy_uses_hedron_lazy() -> None:
    html = render_html(ui.audit_results_lazy(_request()))
    assert 'id="audit-results-region"' in html
    assert "hedron-loading" in html
    assert 'hx-get="/admin/audit/results"' in html
    assert "Loading audit activity" in html


def test_audit_panel_includes_refresh_button() -> None:
    html = render_html(
        ui.audit_panel(
            _request(),
            [],
            event_type_filter="",
            outcome_filter="",
            current_page=1,
            page_count=1,
            total_events=0,
            lazy=True,
        )
    )
    assert 'data-hedron-align="end"' in html
    assert 'hx-get="/admin/audit/results"' in html
    assert 'hx-target="#audit-results-region"' in html
    assert 'hx-target="#audit-results-region-body"' in html
    assert_ui_targets_subset_of_regions(html, APP_REGIONS)
