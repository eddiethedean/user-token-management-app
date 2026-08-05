"""Self-registration and invitation flows for the Hedron UI."""

from __future__ import annotations

from sqlalchemy import select

from app.database import SessionLocal
from app.models import AuditEvent, Invitation, User, UserStatus
from app.config import get_settings
from app.services.auth import create_invitation
from tests.helpers import (
    ADMIN_EMAIL,
    USER_PASSWORD,
    csrf_from,
    latest_email_token,
    login_csrf_from,
    web_login,
)

REGISTRATION_EMAIL = "self.registered@example.gov"
REGISTRATION_PASSWORD = "Verdant-Harbor-83!Signal"


def test_registration_page_states_both_gates(client) -> None:
    page = client.get("/register")
    assert page.status_code == 200
    assert "Request access" in page.text


def test_registration_rejects_unapproved_domain(client) -> None:
    response = client.post(
        "/register",
        data={"email": "outsider@example.com", "full_name": "Outsider"},
    )
    assert response.status_code == 400
    assert "domain" in response.text.lower()


def test_registration_verify_approve_and_sign_in(client) -> None:
    submitted = client.post(
        "/register",
        data={"email": REGISTRATION_EMAIL, "full_name": "Self Registered"},
    )
    assert submitted.status_code == 202
    assert "verification link" in submitted.text.lower()

    token = latest_email_token(REGISTRATION_EMAIL, subject_like="Verify your%registration")
    verified = client.post(
        "/registration/verify",
        data={
            "token": token,
            "password": REGISTRATION_PASSWORD,
            "password_confirm": REGISTRATION_PASSWORD,
        },
    )
    assert verified.status_code == 200
    assert "awaiting administrator" in verified.text.lower()

    preauth = login_csrf_from(client.get("/login").text)
    blocked = client.post(
        "/login",
        data={
            "email": REGISTRATION_EMAIL,
            "password": REGISTRATION_PASSWORD,
            "next": "/profile",
            "preauth_csrf_token": preauth,
        },
    )
    assert blocked.status_code == 400
    assert "approval" in blocked.text.lower()

    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.email == REGISTRATION_EMAIL))
        assert user is not None
        assert user.status == UserStatus.PENDING.value
        user_id = user.id

    web_login(client, next_path="/admin/users")
    csrf = csrf_from(client.get("/admin/users").text)
    approved = client.post(
        f"/admin/users/{user_id}/approve",
        data={"csrf_token": csrf},
    )
    assert approved.status_code == 303
    assert "registration-approved" in approved.headers["location"]

    client.cookies.clear()
    web_login(client, REGISTRATION_EMAIL, REGISTRATION_PASSWORD)
    profile = client.get("/profile")
    assert profile.status_code == 200
    assert REGISTRATION_EMAIL in profile.text

    with SessionLocal() as db:
        event = db.scalar(
            select(AuditEvent).where(
                AuditEvent.target_user_id == user_id,
                AuditEvent.event_type == "registration.approved",
            )
        )
        assert event is not None


def test_admin_can_deny_pending_registration(client) -> None:
    email = "self.denied@example.gov"
    assert (
        client.post(
            "/register",
            data={"email": email, "full_name": "Denied User"},
        ).status_code
        == 202
    )
    token = latest_email_token(email, subject_like="Verify your%registration")
    assert (
        client.post(
            "/registration/verify",
            data={
                "token": token,
                "password": REGISTRATION_PASSWORD,
                "password_confirm": REGISTRATION_PASSWORD,
            },
        ).status_code
        == 200
    )
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.email == email))
        assert user is not None
        user_id = user.id

    web_login(client, next_path="/admin/users")
    denied = client.post(
        f"/admin/users/{user_id}/deny",
        data={"csrf_token": csrf_from(client.get("/admin/users").text)},
    )
    assert denied.status_code == 303
    with SessionLocal() as db:
        user = db.get(User, user_id)
        assert user is not None and user.status == UserStatus.DISABLED.value


def test_admin_cannot_approve_before_email_verification(client) -> None:
    email = "unverified.pending@example.gov"
    assert (
        client.post(
            "/register",
            data={"email": email, "full_name": "Unverified"},
        ).status_code
        == 202
    )
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.email == email))
        assert user is not None
        user_id = user.id

    web_login(client, next_path="/admin/users")
    blocked = client.post(
        f"/admin/users/{user_id}/approve",
        data={"csrf_token": csrf_from(client.get("/admin/users").text)},
    )
    assert blocked.status_code == 400


def test_invitation_accept_and_revoke(client) -> None:
    settings = get_settings()
    with SessionLocal() as db:
        admin = db.scalar(select(User).where(User.email == ADMIN_EMAIL))
        invitation, raw_token = create_invitation(
            db,
            settings,
            email="invitee@example.gov",
            role_name="user",
            inviter=admin,
        )
        invitation_id = invitation.id

    page = client.get(f"/invitations/accept?token={raw_token}")
    assert page.status_code == 200
    accepted = client.post(
        "/invitations/accept",
        data={
            "token": raw_token,
            "full_name": "Invitee User",
            "password": USER_PASSWORD,
            "password_confirm": USER_PASSWORD,
        },
    )
    assert accepted.status_code in {200, 303}
    web_login(client, "invitee@example.gov", USER_PASSWORD)

    # Fresh invitation for revoke path
    client.cookies.clear()
    with SessionLocal() as db:
        admin = db.scalar(select(User).where(User.email == ADMIN_EMAIL))
        invitation, _ = create_invitation(
            db,
            settings,
            email="revoked.invite@example.gov",
            role_name="user",
            inviter=admin,
        )
        invitation_id = invitation.id

    web_login(client, next_path="/admin/users")
    revoked = client.post(
        f"/admin/invitations/{invitation_id}/revoke",
        data={"csrf_token": csrf_from(client.get("/admin/users").text)},
    )
    assert revoked.status_code == 303
    with SessionLocal() as db:
        invitation = db.get(Invitation, invitation_id)
        assert invitation is not None and invitation.revoked_at is not None
