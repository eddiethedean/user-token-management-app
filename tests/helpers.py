"""Shared helpers for Hedron web-flow tests."""

from __future__ import annotations

import re
from urllib.parse import parse_qs, urlparse

from hedron.testing import AdapterResponse, AppScenario
from sqlalchemy import select

from app.database import SessionLocal
from app.models import EmailOutbox

ADMIN_EMAIL = "admin@example.gov"
ADMIN_PASSWORD = "Tr0pic-Maple!River92"
USER_PASSWORD = "Aspen-Compass-64!River"
NEW_PASSWORD = "Quartz-Beacon-62!Harbor"
ADVANA_TOKEN = "advana-secret-token-value-123456"

HX_FRAGMENT = {"HX-Request": "true", "Accept": "text/html"}


def csrf_from(html: str) -> str:
    match = re.search(r'name="csrf_token"\s+value="([^"]+)"', html)
    if match is None:
        match = re.search(r'name="csrf_token" value="([^"]+)"', html)
    assert match is not None, "csrf_token missing from HTML"
    return match.group(1)


def login_csrf_from(html: str) -> str:
    match = re.search(r'name="preauth_csrf_token"\s+value="([^"]+)"', html)
    if match is None:
        match = re.search(r'name="preauth_csrf_token" value="([^"]+)"', html)
    assert match is not None, "preauth_csrf_token missing from HTML"
    return match.group(1)


def as_adapter(response) -> AdapterResponse:
    """Wrap a Starlette/TestClient response for Hedron assert helpers."""
    return AdapterResponse(
        response.status_code,
        response.text,
        dict(response.headers),
        {str(k): str(v) for k, v in response.cookies.items()},
    )


def copy_cookies(source, target) -> None:
    """Copy auth cookies from one TestClient jar onto another."""
    for key, value in source.cookies.items():
        target.cookies.set(key, value)


def web_login(
    client,
    email: str = ADMIN_EMAIL,
    password: str = ADMIN_PASSWORD,
    *,
    next_path: str = "/profile",
) -> None:
    preauth = login_csrf_from(client.get("/login").text)
    response = client.post(
        "/login",
        data={
            "email": email,
            "password": password,
            "next": next_path,
            "preauth_csrf_token": preauth,
        },
    )
    assert response.status_code == 303, response.text
    assert next_path in response.headers["location"]


def htmx_login(
    htmx,
    email: str = ADMIN_EMAIL,
    password: str = ADMIN_PASSWORD,
    *,
    next_path: str = "/profile",
) -> AdapterResponse:
    """Sign in on a fragment_client and return the followed landing response."""
    login_page = htmx.get("/login")
    token = login_csrf_from(login_page.text)
    signed_in = htmx.post(
        "/login",
        data={
            "email": email,
            "password": password,
            "next": next_path,
            "preauth_csrf_token": token,
        },
        follow_redirects=True,
    )
    assert signed_in.status_code == 200, signed_in.text
    return as_adapter(signed_in)


def fixture_login(
    fixture,
    email: str = ADMIN_EMAIL,
    password: str = ADMIN_PASSWORD,
    *,
    next_path: str = "/profile",
) -> AdapterResponse:
    """Sign in through Hedron's fastapi_fixture (follows redirects, keeps cookies)."""
    login_page = fixture.get("/login")
    token = login_csrf_from(login_page.body)
    return fixture.post(
        "/login",
        data={
            "email": email,
            "password": password,
            "next": next_path,
            "preauth_csrf_token": token,
        },
        cookies=login_page.cookies,
    )


def scenario_login(
    scenario: AppScenario,
    email: str = ADMIN_EMAIL,
    password: str = ADMIN_PASSWORD,
    *,
    next_path: str = "/profile",
) -> AdapterResponse:
    """Sign in through AppScenario and return the landing document."""
    login_page = scenario.get("/login")
    token = login_csrf_from(login_page.body)
    return scenario.post(
        "/login",
        data={
            "email": email,
            "password": password,
            "next": next_path,
            "preauth_csrf_token": token,
        },
        cookies=login_page.cookies,
    )


def assert_no_document_shell(response: AdapterResponse) -> None:
    """Fragment responses must not re-emit authenticated shell chrome."""
    lower = response.body.lower()
    assert "<html" not in lower
    assert 'class="site-header"' not in response.body
    assert 'class="official-banner"' not in response.body


def assert_hx_redirect(response: AdapterResponse, contains: str) -> None:
    location = response.headers.get("HX-Redirect") or response.headers.get("hx-redirect") or ""
    assert contains in location, f"expected HX-Redirect containing {contains!r}, got {location!r}"


def assert_hx_push_url(response: AdapterResponse) -> None:
    push = response.headers.get("HX-Push-Url") or response.headers.get("hx-push-url")
    assert push, f"expected HX-Push-Url, got headers={dict(response.headers)}"


def latest_email_token(recipient: str, *, subject_like: str | None = None) -> str:
    with SessionLocal() as db:
        statement = select(EmailOutbox).where(EmailOutbox.recipient == recipient)
        if subject_like:
            statement = statement.where(EmailOutbox.subject.like(subject_like))
        message = db.scalar(statement.order_by(EmailOutbox.created_at.desc()))
        assert message is not None, f"No email for {recipient}"
        match = re.search(r"https?://[^\s]+", message.body_text)
        assert match is not None, "No link in email body"
        return parse_qs(urlparse(match.group(0)).query)["token"][0]
