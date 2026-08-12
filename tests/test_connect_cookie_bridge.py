"""Security and end-to-end coverage for the opt-in Connect cookie bridge."""

from __future__ import annotations

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from app.security.cookies import (
    ACCESS_COOKIE,
    CONNECT_COOKIE_BRIDGE_HEADER,
    PREAUTH_CSRF_COOKIE,
    REFRESH_COOKIE,
    ConnectCookieBridgeMiddleware,
)
from tests.helpers import ADMIN_EMAIL, ADMIN_PASSWORD, login_csrf_from

BRIDGE_HEADER = CONNECT_COOKIE_BRIDGE_HEADER.decode("ascii")


def _echo_client(*, enabled: bool = True, connect_runtime: bool = True) -> TestClient:
    inner = FastAPI()

    @inner.get("/")
    async def echo(request: Request) -> dict[str, str | None]:
        return {
            "cookie": request.headers.get("cookie"),
            "bridge": request.headers.get(BRIDGE_HEADER),
        }

    wrapped = ConnectCookieBridgeMiddleware(
        inner,
        enabled=enabled,
        connect_runtime=connect_runtime,
    )
    return TestClient(wrapped)


def test_bridge_filters_connect_and_unrelated_cookies() -> None:
    with _echo_client() as client:
        response = client.get(
            "/",
            headers={
                BRIDGE_HEADER: (
                    "rsconnect=connect-secret; unrelated=value; "
                    f"{ACCESS_COOKIE}=access-value; {REFRESH_COOKIE}=refresh-value"
                )
            },
        )

    assert response.status_code == 200
    assert response.json() == {
        "cookie": f"{ACCESS_COOKIE}=access-value; {REFRESH_COOKIE}=refresh-value",
        "bridge": None,
    }


@pytest.mark.parametrize(("enabled", "connect_runtime"), [(False, True), (True, False)])
def test_bridge_is_inert_without_both_security_gates(enabled: bool, connect_runtime: bool) -> None:
    with _echo_client(enabled=enabled, connect_runtime=connect_runtime) as client:
        response = client.get(
            "/",
            headers={BRIDGE_HEADER: f"{ACCESS_COOKIE}=must-not-become-a-cookie"},
        )

    assert response.status_code == 200
    assert response.json()["cookie"] is None


def test_bridge_rejects_duplicate_or_conflicting_transports() -> None:
    with _echo_client() as client:
        duplicate = client.get(
            "/",
            headers=[
                (BRIDGE_HEADER, f"{ACCESS_COOKIE}=one"),
                (BRIDGE_HEADER, f"{ACCESS_COOKIE}=two"),
            ],
        )
        conflicting = client.get(
            "/",
            headers={
                "Cookie": f"{ACCESS_COOKIE}=native",
                BRIDGE_HEADER: f"{ACCESS_COOKIE}=bridged",
            },
        )

    assert duplicate.status_code == 400
    assert conflicting.status_code == 400


def test_bridge_completes_application_owned_cookie_login(access_app) -> None:
    wrapped = ConnectCookieBridgeMiddleware(
        access_app,
        enabled=True,
        connect_runtime=True,
    )
    with TestClient(
        wrapped,
        follow_redirects=False,
        client=("127.0.0.1", 50000),
    ) as client:
        login_page = client.get("/login")
        preauth = login_csrf_from(login_page.text)
        client.cookies.clear()

        signed_in = client.post(
            "/login",
            data={
                "email": ADMIN_EMAIL,
                "password": ADMIN_PASSWORD,
                "next": "/profile",
                "preauth_csrf_token": preauth,
            },
            headers={BRIDGE_HEADER: f"rsconnect=connect-secret; {PREAUTH_CSRF_COOKIE}={preauth}"},
        )
        access = signed_in.cookies.get(ACCESS_COOKIE)
        refresh = signed_in.cookies.get(REFRESH_COOKIE)
        assert signed_in.status_code == 303
        assert access
        assert refresh

        client.cookies.clear()
        profile = client.get(
            "/profile",
            headers={
                BRIDGE_HEADER: (
                    f"rsconnect=connect-secret; {ACCESS_COOKIE}={access}; "
                    f"{REFRESH_COOKIE}={refresh}"
                )
            },
        )

    assert profile.status_code == 200
    assert "Your profile" in profile.text


def test_main_app_ignores_spoofed_bridge_header_when_disabled(client) -> None:
    page = client.get("/login")
    preauth = login_csrf_from(page.text)
    client.cookies.clear()
    response = client.post(
        "/login",
        data={
            "email": ADMIN_EMAIL,
            "password": ADMIN_PASSWORD,
            "preauth_csrf_token": preauth,
        },
        headers={BRIDGE_HEADER: f"{PREAUTH_CSRF_COOKIE}={preauth}"},
    )

    assert response.status_code == 403
