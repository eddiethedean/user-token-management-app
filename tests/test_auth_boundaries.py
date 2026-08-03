import re
from datetime import timedelta

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.config import get_settings
from app.database import SessionLocal
from app.dependencies import REFRESH_COOKIE
from app.main import app
from app.models import (
    AuditEvent,
    EmailOutbox,
    Invitation,
    PasswordReset,
    RefreshSession,
    User,
    utcnow,
)
from app.security.tokens import decode_access_token

ADMIN_EMAIL = "admin@example.gov"
ADMIN_PASSWORD = "River-Lantern-94!Blue"
NEW_PASSWORD = "Silver-Canyon-82!Light"


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def api_login(client, email: str, password: str) -> str:
    response = client.post("/api/v1/auth/token", json={"email": email, "password": password})
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


def test_login_lockout_is_generic_audited_and_blocks_correct_password(client) -> None:
    for _ in range(5):
        rejected = client.post(
            "/api/v1/auth/token",
            json={"email": ADMIN_EMAIL, "password": "definitely-wrong"},
        )
        assert rejected.status_code == 401
        assert rejected.json()["detail"] == "Unable to sign in with those credentials."

    locked = client.post(
        "/api/v1/auth/token",
        json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD},
    )
    assert locked.status_code == 401
    assert locked.json()["detail"] == "Unable to sign in with those credentials."

    unknown = client.post(
        "/api/v1/auth/token",
        json={"email": "unknown@example.gov", "password": "definitely-wrong"},
    )
    assert unknown.status_code == 401
    assert unknown.json()["detail"] == locked.json()["detail"]

    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.email == ADMIN_EMAIL))
        assert user is not None
        assert user.failed_login_attempts == 5
        assert user.locked_until is not None and user.locked_until > utcnow()
        outcomes = db.scalars(
            select(AuditEvent.outcome)
            .where(AuditEvent.event_type == "auth.login")
            .order_by(AuditEvent.occurred_at)
        ).all()
        assert outcomes == ["failure"] * 5 + ["locked"]


def test_refresh_rotation_rejects_replay_and_invalid_cookie_is_deleted(client) -> None:
    api_login(client, ADMIN_EMAIL, ADMIN_PASSWORD)
    old_refresh = client.cookies.get(REFRESH_COOKIE)
    assert old_refresh

    rotated = client.post("/api/v1/auth/refresh")
    assert rotated.status_code == 200
    new_refresh = client.cookies.get(REFRESH_COOKIE)
    assert new_refresh and new_refresh != old_refresh

    with TestClient(app, follow_redirects=False) as replay_client:
        replay_client.cookies.set(REFRESH_COOKIE, old_refresh)
        replay = replay_client.post("/api/v1/auth/refresh")
        assert replay.status_code == 401
        assert "invalid or expired" in replay.json()["detail"].lower()
        deleted = replay.headers.get_list("set-cookie")
        assert len(deleted) == 2
        assert all("Max-Age=0" in cookie for cookie in deleted)

    family_revoked = client.post("/api/v1/auth/refresh")
    assert family_revoked.status_code == 401
    with SessionLocal() as db:
        event = db.scalar(
            select(AuditEvent).where(AuditEvent.event_type == "auth.session.refresh_reuse")
        )
        assert event is not None and event.outcome == "denied"


def test_trusted_header_authentication_requires_allowlisted_proxy(client) -> None:
    settings = get_settings()
    original_mode = settings.authentication_mode
    original_proxies = settings.trusted_proxy_ips
    try:
        settings.authentication_mode = "trusted_header"
        settings.trusted_proxy_ips = "127.0.0.1"
        with TestClient(
            app,
            client=("127.0.0.1", 50000),
            follow_redirects=False,
        ) as federated_client:
            login_page = federated_client.get("/login")
            assert "Continue with federated sign-in" in login_page.text
            assert 'name="password"' not in login_page.text
            missing = federated_client.post("/api/v1/auth/federated")
            assert missing.status_code == 401
            signed_in = federated_client.post(
                "/api/v1/auth/federated",
                headers={settings.trusted_identity_header: ADMIN_EMAIL},
            )
            assert signed_in.status_code == 200
            assert decode_access_token(signed_in.json()["access_token"], settings)["sub"]
            password_login = federated_client.post(
                "/api/v1/auth/token",
                json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD},
            )
            assert password_login.status_code == 403

        with TestClient(app, follow_redirects=False) as untrusted_client:
            spoofed = untrusted_client.post(
                "/api/v1/auth/federated",
                headers={settings.trusted_identity_header: ADMIN_EMAIL},
            )
            assert spoofed.status_code == 401
    finally:
        settings.authentication_mode = original_mode
        settings.trusted_proxy_ips = original_proxies


def test_cookie_requests_require_csrf_but_bearer_requests_do_not(client) -> None:
    access_token = api_login(client, ADMIN_EMAIL, ADMIN_PASSWORD)
    rejected = client.patch(
        "/api/v1/me",
        json={"full_name": "No CSRF", "organization": "", "job_title": "", "phone": ""},
    )
    assert rejected.status_code == 403
    assert rejected.json()["detail"] == "Invalid CSRF token"

    accepted = client.patch(
        "/api/v1/me",
        json={
            "full_name": "Bearer Administrator",
            "organization": "Security",
            "job_title": "Administrator",
            "phone": "",
        },
        headers=bearer(access_token),
    )
    assert accepted.status_code == 200

    profile = client.get("/profile")
    csrf = re.search(r'name="csrf_token" value="([^"]+)"', profile.text)
    assert csrf is not None
    cookie_accepted = client.patch(
        "/api/v1/me",
        json={
            "full_name": "Cookie Administrator",
            "organization": "Security",
            "job_title": "Administrator",
            "phone": "",
        },
        headers={"X-CSRF-Token": csrf.group(1)},
    )
    assert cookie_accepted.status_code == 200


def test_password_change_checks_current_policy_and_revokes_old_token(client) -> None:
    old_token = api_login(client, ADMIN_EMAIL, ADMIN_PASSWORD)
    wrong = client.post(
        "/api/v1/me/password",
        json={"current_password": "wrong", "new_password": NEW_PASSWORD},
        headers=bearer(old_token),
    )
    assert wrong.status_code == 400
    assert wrong.json()["detail"] == "Current password is incorrect."

    weak = client.post(
        "/api/v1/me/password",
        json={"current_password": ADMIN_PASSWORD, "new_password": "too-short"},
        headers=bearer(old_token),
    )
    assert weak.status_code == 400
    assert "at least 15" in weak.json()["detail"]

    changed = client.post(
        "/api/v1/me/password",
        json={"current_password": ADMIN_PASSWORD, "new_password": NEW_PASSWORD},
        headers=bearer(old_token),
    )
    assert changed.status_code == 204
    assert client.get("/api/v1/me", headers=bearer(old_token)).status_code == 401
    old_login = client.post(
        "/api/v1/auth/token", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD}
    )
    assert old_login.status_code == 401
    assert api_login(client, ADMIN_EMAIL, NEW_PASSWORD)


def test_session_ownership_revocation_and_logout_all(client, make_user) -> None:
    user = make_user("operator@example.gov")
    first_token = api_login(client, user.email, "Aspen-Compass-64!River")
    second_token = api_login(client, user.email, "Aspen-Compass-64!River")

    sessions = client.get("/api/v1/me/sessions", headers=bearer(second_token))
    assert sessions.status_code == 200
    assert len(sessions.json()) == 2
    other_session = next(item for item in sessions.json() if not item["current"])
    revoked = client.delete(
        f"/api/v1/me/sessions/{other_session['id']}", headers=bearer(second_token)
    )
    assert revoked.status_code == 204
    assert client.get("/api/v1/me", headers=bearer(first_token)).status_code == 401

    missing = client.delete(
        "/api/v1/me/sessions/00000000-0000-0000-0000-000000000000",
        headers=bearer(second_token),
    )
    assert missing.status_code == 404

    third_token = api_login(client, user.email, "Aspen-Compass-64!River")
    logout_all = client.post("/api/v1/auth/logout-all", headers=bearer(second_token))
    assert logout_all.status_code == 204
    assert client.get("/api/v1/me", headers=bearer(second_token)).status_code == 401
    assert client.get("/api/v1/me", headers=bearer(third_token)).status_code == 401


def test_non_admin_is_forbidden_and_admin_self_protections_hold(client, make_user) -> None:
    normal = make_user("viewer@example.gov")
    normal_token = api_login(client, normal.email, "Aspen-Compass-64!River")
    assert client.get("/api/v1/admin/users", headers=bearer(normal_token)).status_code == 403
    assert client.get("/api/v1/admin/audit", headers=bearer(normal_token)).status_code == 403

    admin_token = api_login(client, ADMIN_EMAIL, ADMIN_PASSWORD)
    admin = client.get("/api/v1/me", headers=bearer(admin_token)).json()
    disable_self = client.patch(
        f"/api/v1/admin/users/{admin['id']}",
        json={"status": "disabled"},
        headers=bearer(admin_token),
    )
    assert disable_self.status_code == 400
    remove_admin = client.patch(
        f"/api/v1/admin/users/{admin['id']}",
        json={"roles": ["user"]},
        headers=bearer(admin_token),
    )
    assert remove_admin.status_code == 400
    invalid_status = client.patch(
        f"/api/v1/admin/users/{normal.id}",
        json={"status": "deleted"},
        headers=bearer(admin_token),
    )
    assert invalid_status.status_code == 400
    invalid_role = client.patch(
        f"/api/v1/admin/users/{normal.id}",
        json={"roles": ["nonexistent"]},
        headers=bearer(admin_token),
    )
    assert invalid_role.status_code == 400
    missing = client.patch(
        "/api/v1/admin/users/00000000-0000-0000-0000-000000000000",
        json={"status": "active"},
        headers=bearer(admin_token),
    )
    assert missing.status_code == 404


def test_invitation_policy_duplicates_reissue_and_hashed_storage(client, make_user) -> None:
    existing = make_user("existing@example.gov")
    admin_token = api_login(client, ADMIN_EMAIL, ADMIN_PASSWORD)
    bad_domain = client.post(
        "/api/v1/admin/invitations",
        json={"email": "person@outside.com", "role": "user"},
        headers=bearer(admin_token),
    )
    assert bad_domain.status_code == 400
    bad_role = client.post(
        "/api/v1/admin/invitations",
        json={"email": "person@example.gov", "role": "superuser"},
        headers=bearer(admin_token),
    )
    assert bad_role.status_code == 400
    duplicate_user = client.post(
        "/api/v1/admin/invitations",
        json={"email": existing.email, "role": "user"},
        headers=bearer(admin_token),
    )
    assert duplicate_user.status_code == 400

    first = client.post(
        "/api/v1/admin/invitations",
        json={"email": "new.user@example.gov", "role": "user"},
        headers=bearer(admin_token),
    )
    second = client.post(
        "/api/v1/admin/invitations",
        json={"email": "new.user@example.gov", "role": "user"},
        headers=bearer(admin_token),
    )
    assert first.status_code == second.status_code == 201
    with SessionLocal() as db:
        invitations = db.scalars(
            select(Invitation)
            .where(Invitation.email == "new.user@example.gov")
            .order_by(Invitation.created_at)
        ).all()
        assert len(invitations) == 2
        assert invitations[0].revoked_at is not None
        assert invitations[1].revoked_at is None
        message = db.scalar(
            select(EmailOutbox)
            .where(EmailOutbox.recipient == "new.user@example.gov")
            .order_by(EmailOutbox.created_at.desc())
        )
        assert message is not None
        raw_token = re.search(r"token=([^\s]+)", message.body_text)
        assert raw_token is not None
        assert raw_token.group(1) not in invitations[1].token_hash
        assert len(invitations[1].token_hash) == 64


def test_password_reset_is_non_enumerating_supersedes_prior_and_rejects_expiry(
    client, make_user
) -> None:
    user = make_user("reset.user@example.gov")
    before = client.post("/api/v1/auth/forgot-password", json={"email": "missing@example.gov"})
    outside = client.post("/api/v1/auth/forgot-password", json={"email": "missing@outside.com"})
    assert before.status_code == outside.status_code == 202
    assert before.json() == outside.json()
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(PasswordReset)) == 0

    for _ in range(2):
        response = client.post("/api/v1/auth/forgot-password", json={"email": user.email})
        assert response.status_code == 202
    with SessionLocal() as db:
        resets = db.scalars(
            select(PasswordReset)
            .where(PasswordReset.user_id == user.id)
            .order_by(PasswordReset.created_at)
        ).all()
        assert len(resets) == 2
        assert resets[0].used_at is not None
        assert resets[1].used_at is None
        resets[1].expires_at = utcnow() - timedelta(seconds=1)
        db.commit()
        message = db.scalar(
            select(EmailOutbox)
            .where(EmailOutbox.recipient == user.email)
            .order_by(EmailOutbox.created_at.desc())
        )
        assert message is not None
        token_match = re.search(r"token=([^\s]+)", message.body_text)
        assert token_match is not None
        expired_token = token_match.group(1)

    expired = client.post(
        "/api/v1/auth/reset-password",
        json={"token": expired_token, "new_password": NEW_PASSWORD},
    )
    assert expired.status_code == 400
    assert "invalid or expired" in expired.json()["detail"].lower()


def test_bearer_logout_revokes_session_without_csrf(client) -> None:
    access_token = api_login(client, ADMIN_EMAIL, ADMIN_PASSWORD)
    session_id = decode_access_token(access_token, get_settings())["sid"]
    response = client.post("/api/v1/auth/logout", headers=bearer(access_token))
    assert response.status_code == 204
    assert client.get("/api/v1/me", headers=bearer(access_token)).status_code == 401
    with SessionLocal() as db:
        event_count = db.scalar(
            select(func.count())
            .select_from(AuditEvent)
            .where(AuditEvent.event_type == "auth.session.revoked")
        )
        assert event_count == 1
        session = db.get(RefreshSession, session_id)
        assert session is not None and session.revoked_at is not None
