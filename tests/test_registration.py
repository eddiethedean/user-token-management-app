"""Self-registration and invitation flows for the Hedron UI."""

from __future__ import annotations

from sqlalchemy import select

from app.config import get_settings
from app.database import SessionLocal
from app.models import AuditEvent, Invitation, RegistrationVerification, User, UserStatus
from app.services.auth import create_invitation
from tests.helpers import (
    ADMIN_EMAIL,
    USER_PASSWORD,
    csrf_from,
    latest_email_token,
    login_csrf_from,
    preauth_post,
    web_login,
)

REGISTRATION_EMAIL = "self.registered@example.gov"
REGISTRATION_PASSWORD = "Verdant-Harbor-83!Signal"


def test_registration_page_states_both_gates(page) -> None:
    from hedron.testing import assert_html_contains, assert_page_document

    response = page.get("/register")
    assert_page_document(response)
    assert_html_contains(response, "Request access")
    assert 'name="email"' in response.body
    assert 'name="full_name"' in response.body
    assert 'name="password"' not in response.body

    login = page.get("/login")
    assert_page_document(login)
    assert "approval" in login.body.lower()
    assert "Request access" in login.body


def test_registration_rejects_unapproved_domain(client) -> None:
    from sqlalchemy import func

    from app.models import RegistrationVerification

    response = preauth_post(
        client, "/register", {"email": "outsider@example.com", "full_name": "Outsider"}
    )
    assert response.status_code == 400
    assert "domain" in response.text.lower()
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(User)) == 1  # admin only
        assert db.scalar(select(func.count()).select_from(RegistrationVerification)) == 0


def test_registration_verify_approve_and_sign_in(client) -> None:
    submitted = preauth_post(
        client, "/register", {"email": REGISTRATION_EMAIL, "full_name": "Self Registered"}
    )
    assert submitted.status_code == 202
    assert "verification link" in submitted.text.lower()

    token = latest_email_token(REGISTRATION_EMAIL, subject_like="Verify your%registration")
    mismatch = client.post(
        "/registration/verify",
        data={
            "token": token,
            "password": REGISTRATION_PASSWORD,
            "password_confirm": "different-password",
        },
    )
    assert mismatch.status_code == 400
    assert 'name="password"' in mismatch.text
    assert "Passwords do not match" in mismatch.text

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
    assert "Unable to sign in with those credentials." in blocked.text
    assert "awaiting administrator approval" not in blocked.text.lower()
    with SessionLocal() as db:
        pending_audit = db.scalar(
            select(AuditEvent).where(
                AuditEvent.event_type == "auth.login",
                AuditEvent.outcome == "pending_approval",
            )
        )
        assert pending_audit is not None

    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.email == REGISTRATION_EMAIL))
        assert user is not None
        assert user.status == UserStatus.PENDING.value
        assert user.preferred_color_mode == "dark"
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
        preauth_post(client, "/register", {"email": email, "full_name": "Denied User"}).status_code
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
        preauth_post(client, "/register", {"email": email, "full_name": "Unverified"}).status_code
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
    mismatch = client.post(
        "/invitations/accept",
        data={
            "token": raw_token,
            "full_name": "Invitee User",
            "password": USER_PASSWORD,
            "password_confirm": "different-password",
        },
    )
    assert mismatch.status_code == 400
    assert 'name="password"' in mismatch.text
    assert "Passwords do not match" in mismatch.text

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


def test_expired_and_forged_registration_links_do_not_set_credentials(client) -> None:
    from datetime import timedelta

    from app.models import utcnow

    email = "expired.reg@example.gov"
    assert (
        preauth_post(client, "/register", {"email": email, "full_name": "Expired Reg"}).status_code
        == 202
    )
    token = latest_email_token(email, subject_like="Verify your%registration")
    with SessionLocal() as db:
        verification = db.scalar(select(RegistrationVerification))
        assert verification is not None
        assert len(verification.token_hash) == 64
        assert token not in verification.token_hash
        verification.expires_at = utcnow() - timedelta(seconds=1)
        db.commit()

    expired = client.post(
        "/registration/verify",
        data={
            "token": token,
            "password": REGISTRATION_PASSWORD,
            "password_confirm": REGISTRATION_PASSWORD,
        },
    )
    forged = client.post(
        "/registration/verify",
        data={
            "token": "forged-registration-token",
            "password": REGISTRATION_PASSWORD,
            "password_confirm": REGISTRATION_PASSWORD,
        },
    )
    assert expired.status_code == forged.status_code == 400
    assert "invalid or expired" in expired.text.lower()
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.email == email))
        assert user is not None
        assert user.email_verified_at is None
        assert user.password_hash is None
        assert user.status == UserStatus.PENDING.value


def test_self_registration_revokes_prior_invitation(client) -> None:
    settings = get_settings()
    email = "invite.then.register@example.gov"
    with SessionLocal() as db:
        admin = db.scalar(select(User).where(User.email == ADMIN_EMAIL))
        invitation, _ = create_invitation(
            db, settings, email=email, role_name="user", inviter=admin
        )
        invitation_id = invitation.id

    assert (
        preauth_post(
            client, "/register", {"email": email, "full_name": "Self After Invite"}
        ).status_code
        == 202
    )
    with SessionLocal() as db:
        invitation = db.get(Invitation, invitation_id)
        assert invitation is not None and invitation.revoked_at is not None


def test_registration_coalesces_and_does_not_enumerate_existing(client) -> None:
    from sqlalchemy import func

    from app.models import EmailOutbox

    email = "coalesce.reg@example.gov"
    first = preauth_post(client, "/register", {"email": email, "full_name": "First Name"})
    second = preauth_post(client, "/register", {"email": email, "full_name": "Changed Name"})
    existing = preauth_post(
        client, "/register", {"email": ADMIN_EMAIL, "full_name": "Claimed Administrator"}
    )
    assert first.status_code == second.status_code == existing.status_code == 202
    assert "verification" in first.text.lower()
    with SessionLocal() as db:
        pending = db.scalar(select(User).where(User.email == email))
        admin = db.scalar(select(User).where(User.email == ADMIN_EMAIL))
        assert pending is not None and pending.full_name == "First Name"
        assert admin is not None and "Claimed" not in (admin.full_name or "")
        assert (
            db.scalar(
                select(func.count())
                .select_from(EmailOutbox)
                .where(
                    EmailOutbox.recipient == email,
                    EmailOutbox.subject.like("Verify your%registration"),
                )
            )
            == 1
        )


def test_invitation_policy_rejects_bad_role_and_reissues(client) -> None:
    web_login(client, next_path="/admin/users")
    csrf = csrf_from(client.get("/admin/users").text)
    bad_role = client.post(
        "/admin/invitations",
        data={"csrf_token": csrf, "email": "policy@example.gov", "role": "invalid"},
    )
    assert bad_role.status_code == 400

    first = client.post(
        "/admin/invitations",
        data={
            "csrf_token": csrf_from(client.get("/admin/users").text),
            "email": "policy@example.gov",
            "role": "user",
        },
    )
    assert first.status_code == 303
    second = client.post(
        "/admin/invitations",
        data={
            "csrf_token": csrf_from(client.get("/admin/users").text),
            "email": "policy@example.gov",
            "role": "user",
        },
    )
    assert second.status_code == 303
    with SessionLocal() as db:
        invitations = list(
            db.scalars(
                select(Invitation)
                .where(Invitation.email == "policy@example.gov")
                .order_by(Invitation.created_at)
            ).all()
        )
        assert len(invitations) == 2
        assert invitations[0].revoked_at is not None
        assert invitations[1].revoked_at is None
        assert len(invitations[1].token_hash) == 64
