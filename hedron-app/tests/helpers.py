"""Shared helpers for Hedron web-flow tests."""

from __future__ import annotations

import re
from urllib.parse import parse_qs, urlparse

from sqlalchemy import select

from app.database import SessionLocal
from app.models import EmailOutbox

ADMIN_EMAIL = "admin@example.gov"
ADMIN_PASSWORD = "Tr0pic-Maple!River92"
USER_PASSWORD = "Aspen-Compass-64!River"
NEW_PASSWORD = "Quartz-Beacon-62!Harbor"


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


def web_login(client, email: str = ADMIN_EMAIL, password: str = ADMIN_PASSWORD, *, next_path: str = "/profile") -> None:
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
