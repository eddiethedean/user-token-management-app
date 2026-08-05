import re
from datetime import timedelta
from urllib.parse import parse_qs, urlparse

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.config import get_settings
from app.database import SessionLocal
from app.main import app
from app.models import (
    AuditEvent,
    EmailOutbox,
    Invitation,
    RefreshSession,
    RegistrationVerification,
    User,
    UserStatus,
    utcnow,
)
from app.services.auth import create_invitation

ADMIN_EMAIL = "admin@example.gov"
ADMIN_PASSWORD = "River-Lantern-94!Blue"
REGISTRATION_EMAIL = "self.registered@example.gov"
REGISTRATION_PASSWORD = "Verdant-Harbor-83!Signal"


def csrf_from(html: str) -> str:
    match = re.search(r'name="csrf_token" value="([^"]+)"', html)
    assert match is not None
    return match.group(1)


def verification_token(email: str) -> str:
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


def request_registration(client, *, email: str = REGISTRATION_EMAIL):
    return client.post(
        "/register",
        data={"email": email, "full_name": "Self Registered User"},
    )


def verify_registration(client, token: str, *, password: str = REGISTRATION_PASSWORD):
    return client.post(
        "/registration/verify",
        data={
            "token": token,
            "password": password,
            "password_confirm": password,
        },
    )


def admin_login(client) -> str:
    login_page = client.get("/login")
    preauth_csrf = re.search(r'name="preauth_csrf_token" value="([^"]+)"', login_page.text)
    assert preauth_csrf is not None
    response = client.post(
        "/login",
        data={
            "email": ADMIN_EMAIL,
            "password": ADMIN_PASSWORD,
            "next": "/admin/users",
            "preauth_csrf_token": preauth_csrf.group(1),
        },
    )
    assert response.status_code == 303
    page = client.get("/admin/users")
    assert page.status_code == 200
    return csrf_from(page.text)


def login_form_token(client) -> str:
    page = client.get("/login")
    match = re.search(r'name="preauth_csrf_token" value="([^"]+)"', page.text)
    assert match is not None
    return match.group(1)


def test_registration_page_makes_both_gates_explicit(client) -> None:
    page = client.get("/register")
    assert page.status_code == 200
    assert "Verify your email" in page.text
    assert "Wait for administrator approval" in page.text
    assert "You cannot sign in until approval is granted" in page.text
    assert 'autocomplete="email"' in page.text
    assert 'name="password"' not in page.text

    login = client.get("/login")
    assert 'href="register"' in login.text
    assert "administrator approval before signing in" in login.text


def test_self_registration_revokes_an_older_invitation_for_the_same_address(client) -> None:
    settings = get_settings()
    with SessionLocal() as db:
        administrator = db.scalar(select(User).where(User.email == ADMIN_EMAIL))
        assert administrator is not None
        invitation, _ = create_invitation(
            db,
            settings,
            email=REGISTRATION_EMAIL,
            role_name="user",
            inviter=administrator,
        )
        invitation_id = invitation.id
    assert request_registration(client).status_code == 202
    with SessionLocal() as db:
        invitation = db.get(Invitation, invitation_id)
        assert invitation is not None and invitation.revoked_at is not None


def test_federated_registration_verification_does_not_create_a_password(client) -> None:
    settings = get_settings()
    original_mode = settings.authentication_mode
    try:
        settings.authentication_mode = "trusted_header"
        assert request_registration(client).status_code == 202
        token = verification_token(REGISTRATION_EMAIL)
        page = client.get(f"/registration/verify?token={token}")
        assert page.status_code == 200
        assert 'name="password"' not in page.text
        verified = client.post("/registration/verify", data={"token": token})
        assert verified.status_code == 200
        with SessionLocal() as db:
            user = db.scalar(select(User).where(User.email == REGISTRATION_EMAIL))
            assert user is not None
            assert user.email_verified_at is not None
            assert user.password_hash is None
    finally:
        settings.authentication_mode = original_mode


def test_registration_rejects_unapproved_domain_without_creating_state(client) -> None:
    response = request_registration(client, email="person@example.com")
    assert response.status_code == 400
    assert "domain is not approved" in response.text
    with SessionLocal() as db:
        assert db.scalar(select(User).where(User.email == "person@example.com")) is None
        assert db.scalar(select(func.count()).select_from(RegistrationVerification)) == 0


def test_registration_verifies_email_but_blocks_login_until_approval(client) -> None:
    submitted = request_registration(client)
    assert submitted.status_code == 202
    assert "administrator must approve" in submitted.text
    assert "before you can sign in" in submitted.text

    token = verification_token(REGISTRATION_EMAIL)
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.email == REGISTRATION_EMAIL))
        assert user is not None
        assert user.status == UserStatus.PENDING.value
        assert user.email_verified_at is None
        assert user.password_hash is None
        stored = db.scalar(
            select(RegistrationVerification).where(RegistrationVerification.user_id == user.id)
        )
        assert stored is not None
        assert stored.token_hash != token
        assert token not in stored.token_hash

    preview = client.get(f"/registration/verify?token={token}")
    assert preview.status_code == 200
    assert "Administrator approval is still required" in preview.text
    with SessionLocal() as db:
        stored = db.scalar(select(RegistrationVerification))
        assert stored is not None and stored.used_at is None

    mismatch = client.post(
        "/registration/verify",
        data={
            "token": token,
            "password": REGISTRATION_PASSWORD,
            "password_confirm": "different-password",
        },
    )
    assert mismatch.status_code == 400
    assert "Passwords do not match" in mismatch.text

    verified = verify_registration(client, token)
    assert verified.status_code == 200
    assert "Your government email is verified" in verified.text
    assert "you cannot sign in until it is approved" in verified.text

    replay = client.get(f"/registration/verify?token={token}")
    assert replay.status_code == 400
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.email == REGISTRATION_EMAIL))
        assert user is not None
        assert user.status == UserStatus.PENDING.value
        assert user.email_verified_at is not None
        assert user.password_hash is not None
        stored = db.scalar(
            select(RegistrationVerification).where(RegistrationVerification.user_id == user.id)
        )
        assert stored is not None and stored.used_at is not None
        assert (
            db.scalar(
                select(func.count())
                .select_from(RefreshSession)
                .where(RefreshSession.user_id == user.id)
            )
            == 0
        )

    rejected_web = client.post(
        "/login",
        data={
            "email": REGISTRATION_EMAIL,
            "password": REGISTRATION_PASSWORD,
            "next": "/profile",
            "preauth_csrf_token": login_form_token(client),
        },
    )
    assert rejected_web.status_code == 400
    assert "awaiting administrator approval" in rejected_web.text

    rejected_api = client.post(
        "/api/v1/auth/token",
        json={"email": REGISTRATION_EMAIL, "password": REGISTRATION_PASSWORD},
    )
    assert rejected_api.status_code == 401
    assert rejected_api.json()["detail"] == (
        "Your registration is awaiting administrator approval. You cannot sign in yet."
    )


def test_admin_approval_activates_registration_and_is_audited(client) -> None:
    assert request_registration(client).status_code == 202
    assert verify_registration(client, verification_token(REGISTRATION_EMAIL)).status_code == 200
    csrf = admin_login(client)

    page = client.get("/admin/users")
    assert REGISTRATION_EMAIL in page.text
    assert "Email verified" in page.text
    assert ">Approve<" in page.text
    approved = client.post(
        f"/admin/users/{_registration_user_id()}/approve",
        data={"csrf_token": csrf},
        headers={"HX-Request": "true"},
    )
    assert approved.status_code == 200
    assert "Access approved" in approved.text

    client.cookies.clear()
    signed_in = client.post(
        "/login",
        data={
            "email": REGISTRATION_EMAIL,
            "password": REGISTRATION_PASSWORD,
            "next": "/profile",
            "preauth_csrf_token": login_form_token(client),
        },
    )
    assert signed_in.status_code == 303
    assert signed_in.headers["location"] == "/profile"

    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.email == REGISTRATION_EMAIL))
        assert user is not None and user.status == UserStatus.ACTIVE.value
        event_names = db.scalars(
            select(AuditEvent.event_type).where(AuditEvent.target_user_id == user.id)
        ).all()
        assert "registration.requested" in event_names
        assert "registration.email_verified" in event_names
        assert "registration.approved" in event_names
        approval_email = db.scalar(
            select(EmailOutbox).where(
                EmailOutbox.recipient == REGISTRATION_EMAIL,
                EmailOutbox.subject.like("%access was approved"),
            )
        )
        assert approval_email is not None


def test_admin_cannot_bypass_email_verification_and_can_deny(client) -> None:
    assert request_registration(client).status_code == 202
    user_id = _registration_user_id()
    csrf = admin_login(client)

    blocked = client.post(
        f"/admin/users/{user_id}/approve",
        data={"csrf_token": csrf},
    )
    assert blocked.status_code == 400
    assert "must verify" in blocked.json()["detail"]
    bypass = client.post(
        f"/admin/users/{user_id}/toggle",
        data={"csrf_token": csrf},
    )
    assert bypass.status_code == 400

    denied = client.post(
        f"/admin/users/{user_id}/deny",
        data={"csrf_token": csrf},
        headers={"HX-Request": "true"},
    )
    assert denied.status_code == 200
    assert "Registration denied" in denied.text
    with SessionLocal() as db:
        user = db.get(User, user_id)
        assert user is not None and user.status == UserStatus.DISABLED.value
        denied_event = db.scalar(
            select(AuditEvent).where(
                AuditEvent.target_user_id == user_id,
                AuditEvent.event_type == "registration.denied",
            )
        )
        assert denied_event is not None


def test_expired_and_forged_registration_links_do_not_set_credentials(client) -> None:
    assert request_registration(client).status_code == 202
    token = verification_token(REGISTRATION_EMAIL)
    with SessionLocal() as db:
        verification = db.scalar(select(RegistrationVerification))
        assert verification is not None
        verification.expires_at = utcnow() - timedelta(seconds=1)
        db.commit()

    expired = verify_registration(client, token)
    forged = verify_registration(client, "forged-registration-token")
    assert expired.status_code == forged.status_code == 400
    assert "invalid or expired" in expired.text
    assert "invalid or expired" in forged.text
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.email == REGISTRATION_EMAIL))
        assert user is not None
        assert user.email_verified_at is None
        assert user.password_hash is None
        assert user.status == UserStatus.PENDING.value


def test_registration_submission_is_generic_and_recent_requests_are_coalesced(client) -> None:
    first = client.post(
        "/api/v1/auth/register",
        json={"email": REGISTRATION_EMAIL, "full_name": "First Name"},
    )
    second = client.post(
        "/api/v1/auth/register",
        json={"email": REGISTRATION_EMAIL, "full_name": "Changed Name"},
    )
    existing = client.post(
        "/api/v1/auth/register",
        json={"email": ADMIN_EMAIL, "full_name": "Claimed Administrator"},
    )
    assert first.status_code == second.status_code == existing.status_code == 202
    assert first.json() == second.json() == existing.json()

    with SessionLocal() as db:
        pending = db.scalar(select(User).where(User.email == REGISTRATION_EMAIL))
        admin = db.scalar(select(User).where(User.email == ADMIN_EMAIL))
        assert pending is not None and pending.full_name == "First Name"
        assert admin is not None and admin.full_name == "Registry Administrator"
        verification_messages = db.scalar(
            select(func.count())
            .select_from(EmailOutbox)
            .where(
                EmailOutbox.recipient == REGISTRATION_EMAIL,
                EmailOutbox.subject.like("Verify your%registration"),
            )
        )
        assert verification_messages == 1


def test_registration_pages_respect_workbench_root_path(client) -> None:
    root_path = "/s/registration/session/p/8000"
    with TestClient(app, root_path=root_path, follow_redirects=False) as workbench_client:
        page = workbench_client.get("/register")
        assert page.status_code == 200
        assert f'<base href="{root_path}/">' in page.text
        assert 'action="register"' in page.text


def _registration_user_id() -> str:
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.email == REGISTRATION_EMAIL))
        assert user is not None
        return user.id
