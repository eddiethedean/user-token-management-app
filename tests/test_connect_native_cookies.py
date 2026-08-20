"""Native application-cookie coverage for Posit Connect."""

from __future__ import annotations

from app.security.cookies import ACCESS_COOKIE, PREAUTH_CSRF_COOKIE, REFRESH_COOKIE
from tests.helpers import ADMIN_EMAIL, ADMIN_PASSWORD, login_csrf_from


def test_native_connect_login_round_trip(client, monkeypatch) -> None:
    """Connect forwards app cookies and adds the external content path itself."""
    monkeypatch.setenv("POSIT_PRODUCT", "CONNECT")
    connect_headers = {
        "RStudio-Connect-App-Base-URL": ("https://connect.example.gov/content/access-registry/")
    }

    login_page = client.get("/login", headers=connect_headers)
    preauth = login_csrf_from(login_page.text)
    assert any(
        header.startswith(f"{PREAUTH_CSRF_COOKIE}=") and "Path=/;" in header
        for header in login_page.headers.get_list("set-cookie")
    )

    signed_in = client.post(
        "/login",
        headers=connect_headers,
        data={
            "email": ADMIN_EMAIL,
            "password": ADMIN_PASSWORD,
            "next": "/profile",
            "preauth_csrf_token": preauth,
        },
    )
    assert signed_in.status_code == 303
    # TestClient does not emulate Connect's ASGI root_path; the native Connect
    # proxy adds its content mount to this app-local redirect in deployment.
    assert signed_in.headers["location"] == "/profile"
    set_cookie_headers = signed_in.headers.get_list("set-cookie")
    for cookie_name in (ACCESS_COOKIE, REFRESH_COOKIE):
        assert any(
            header.startswith(f"{cookie_name}=") and "Path=/;" in header
            for header in set_cookie_headers
        )

    profile = client.get("/profile", headers=connect_headers)
    assert profile.status_code == 200
    assert "Account settings" in profile.text
