"""API admin happy-path coverage: list, audit, approve/deny, invitation revoke."""

from __future__ import annotations

import re
from urllib.parse import parse_qs, urlparse

from sqlalchemy import select

from app.config import get_settings
from app.database import SessionLocal
from app.models import AuditEvent, EmailOutbox, Invitation, User, UserStatus
from app.services.auth import create_invitation

ADMIN_EMAIL = "admin@example.gov"
ADMIN_PASSWORD = "River-Lantern-94!Blue"
REGISTRATION_EMAIL = "api.pending@example.gov"
REGISTRATION_PASSWORD = "Verdant-Harbor-83!Signal"


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def api_login(client, email: str = ADMIN_EMAIL, password: str = ADMIN_PASSWORD) -> str:
    response = client.post("/api/v1/auth/token", json={"email": email, "password": password})
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


def _verification_token(email: str) -> str:
    with SessionLocal() as db:
        message = db.scalar(
            select(EmailOutbox)
            .where(
                EmailOutbox.recipient == email,
                EmailOutbox.subject.like("Verify your%registration"),
            )
            .order_by(EmailOutbox.created_at.desc())
        )
        assert message is not None
        match = re.search(r"https://[^\s]+", message.body_text)
        assert match is not None
        return parse_qs(urlparse(match.group(0)).query)["token"][0]


def _verified_pending_user(client, *, email: str, full_name: str) -> str:
    assert (
        client.post(
            "/api/v1/auth/register",
            json={"email": email, "full_name": full_name},
        ).status_code
        == 202
    )
    token = _verification_token(email)
    verified = client.post(
        "/registration/verify",
        data={
            "token": token,
            "password": REGISTRATION_PASSWORD,
            "password_confirm": REGISTRATION_PASSWORD,
        },
    )
    assert verified.status_code == 200, verified.text
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.email == email))
        assert user is not None
        assert user.status == UserStatus.PENDING.value
        return user.id


def test_admin_list_users_and_filters(client, make_user) -> None:
    make_user("filter.alpha@example.gov")
    make_user("filter.beta@example.gov", status=UserStatus.DISABLED.value)
    token = api_login(client)
    headers = bearer(token)

    listed = client.get("/api/v1/admin/users", headers=headers)
    assert listed.status_code == 200
    emails = {row["email"] for row in listed.json()}
    assert ADMIN_EMAIL in emails
    assert "filter.alpha@example.gov" in emails

    filtered = client.get(
        "/api/v1/admin/users",
        params={"q": "filter.alpha", "status": "active"},
        headers=headers,
    )
    assert filtered.status_code == 200
    assert [row["email"] for row in filtered.json()] == ["filter.alpha@example.gov"]

    disabled = client.get(
        "/api/v1/admin/users",
        params={"status": "disabled"},
        headers=headers,
    )
    assert disabled.status_code == 200
    assert any(row["email"] == "filter.beta@example.gov" for row in disabled.json())

    limited = client.get("/api/v1/admin/users", params={"limit": 1, "offset": 0}, headers=headers)
    assert limited.status_code == 200
    assert len(limited.json()) == 1


def test_admin_audit_list_and_filters(client) -> None:
    token = api_login(client)
    headers = bearer(token)

    events = client.get("/api/v1/admin/audit", headers=headers)
    assert events.status_code == 200
    assert events.json()
    assert all("event_type" in row and "outcome" in row for row in events.json())

    filtered = client.get(
        "/api/v1/admin/audit",
        params={"event_type": "auth.login", "outcome": "success"},
        headers=headers,
    )
    assert filtered.status_code == 200
    assert filtered.json()
    assert all(row["event_type"] == "auth.login" for row in filtered.json())
    assert all(row["outcome"] == "success" for row in filtered.json())


def test_api_approve_and_deny_registration(client) -> None:
    user_id = _verified_pending_user(
        client, email=REGISTRATION_EMAIL, full_name="API Pending"
    )
    token = api_login(client)
    headers = bearer(token)

    approved = client.post(f"/api/v1/admin/users/{user_id}/approve", headers=headers)
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == UserStatus.ACTIVE.value
    assert approved.json()["email"] == REGISTRATION_EMAIL
    assert api_login(client, REGISTRATION_EMAIL, REGISTRATION_PASSWORD)

    with SessionLocal() as db:
        event = db.scalar(
            select(AuditEvent).where(
                AuditEvent.target_user_id == user_id,
                AuditEvent.event_type == "registration.approved",
            )
        )
        assert event is not None

    pending_id = _verified_pending_user(
        client, email="api.denied@example.gov", full_name="API Denied"
    )
    denied = client.post(f"/api/v1/admin/users/{pending_id}/deny", headers=headers)
    assert denied.status_code == 200, denied.text
    assert denied.json()["status"] == UserStatus.DISABLED.value

    with SessionLocal() as db:
        user = db.get(User, pending_id)
        assert user is not None and user.status == UserStatus.DISABLED.value


def test_api_approve_requires_verified_email(client) -> None:
    assert (
        client.post(
            "/api/v1/auth/register",
            json={"email": "unverified.api@example.gov", "full_name": "Unverified"},
        ).status_code
        == 202
    )
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.email == "unverified.api@example.gov"))
        assert user is not None
        user_id = user.id

    token = api_login(client)
    blocked = client.post(f"/api/v1/admin/users/{user_id}/approve", headers=bearer(token))
    assert blocked.status_code == 400
    assert "verify" in blocked.json()["detail"].lower()


def test_api_revoke_invitation(client) -> None:
    token = api_login(client)
    headers = bearer(token)
    settings = get_settings()

    with SessionLocal() as db:
        admin = db.scalar(select(User).where(User.email == ADMIN_EMAIL))
        invitation, _ = create_invitation(
            db,
            settings,
            email="invitee.api@example.gov",
            role_name="user",
            inviter=admin,
        )
        invitation_id = invitation.id

    revoked = client.delete(f"/api/v1/admin/invitations/{invitation_id}", headers=headers)
    assert revoked.status_code == 204

    with SessionLocal() as db:
        invitation = db.get(Invitation, invitation_id)
        assert invitation is not None and invitation.revoked_at is not None

    missing = client.delete(
        "/api/v1/admin/invitations/00000000-0000-0000-0000-000000000000",
        headers=headers,
    )
    assert missing.status_code == 404


def test_ready_endpoint_reports_ready(client) -> None:
    response = client.get("/ready")
    assert response.status_code == 200
    assert response.json() == {"status": "ready"}
