"""Smoke tests for Data Mover."""

from __future__ import annotations

from fastapi.testclient import TestClient

from tests.helpers import assert_redirect_path


def test_health(client: TestClient) -> None:
    assert client.get("/health").json() == {"status": "ok"}


def test_login_page(client: TestClient) -> None:
    response = client.get("/login")
    assert response.status_code == 200
    assert "Sign in" in response.text
    assert "access_registry_login_csrf" in response.cookies
    assert "SameSite=lax" in response.headers["set-cookie"]


def test_admin_requires_auth(client: TestClient) -> None:
    browser_request = client.get(
        "/admin/users",
        headers={"Accept": "text/html"},
        follow_redirects=False,
    )
    assert browser_request.status_code == 303
    assert_redirect_path(
        browser_request,
        "/login",
        query={"next": ["/admin/users"]},
    )

    api_request = client.get(
        "/admin/users",
        headers={"Accept": "application/json"},
        follow_redirects=False,
    )
    assert api_request.status_code == 401
    assert api_request.headers["www-authenticate"] == "Bearer"


def test_login_and_profile(client: TestClient) -> None:
    login_page = client.get("/login")
    csrf = login_page.cookies.get("access_registry_login_csrf")
    assert csrf
    assert 'name="preauth_csrf_token"' in login_page.text
    start = login_page.text.index('name="preauth_csrf_token"')
    snippet = login_page.text[start : start + 200]
    token = snippet.split('value="')[1].split('"')[0]
    response = client.post(
        "/login",
        data={
            "email": "admin@example.gov",
            "password": "Tr0pic-Maple!River92",
            "preauth_csrf_token": token,
            "next": "/profile",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert_redirect_path(response, "/profile")
    profile = client.get("/profile")
    assert profile.status_code == 200
    assert "Account settings" in profile.text


def test_htmx_admin_directory_requires_auth(client: TestClient) -> None:
    response = client.get(
        "/admin/users",
        headers={
            "HX-Request": "true",
            "HX-Target": "#user-directory",
            "Accept": "text/html",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert_redirect_path(response, "/login", query={"next": ["/admin/users"]})
    assert response.headers["hx-redirect"] == response.headers["location"]
