"""Smoke tests for the Hedron Access Registry port."""

from __future__ import annotations

from fastapi.testclient import TestClient


def test_health(client: TestClient) -> None:
    assert client.get("/health").json() == {"status": "ok"}


def test_login_page(client: TestClient) -> None:
    response = client.get("/login")
    assert response.status_code == 200
    assert "Sign in" in response.text
    assert "access_registry_login_csrf" in response.cookies


def test_admin_requires_auth(client: TestClient) -> None:
    response = client.get("/admin/users", follow_redirects=False)
    assert response.status_code in {303, 401, 302}
    location = response.headers.get("location", "")
    assert "/login" in location or response.status_code == 401


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
    assert "/profile" in response.headers["location"]
    profile = client.get("/profile")
    assert profile.status_code == 200
    assert "Your profile" in profile.text


def test_htmx_admin_directory_requires_auth(client: TestClient) -> None:
    response = client.get(
        "/admin/users",
        headers={"HX-Request": "true", "HX-Target": "#user-directory"},
        follow_redirects=False,
    )
    assert response.status_code in {303, 401, 302}
