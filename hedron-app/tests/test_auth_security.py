"""Auth, CSRF, logout, password recovery, and rate-limit coverage for Hedron UI."""

from __future__ import annotations

from sqlalchemy import select

from app.config import get_settings
from app.database import SessionLocal
from app.models import AuditEvent, RateLimitBucket, RefreshSession, User
from tests.helpers import (
    ADMIN_EMAIL,
    ADMIN_PASSWORD,
    NEW_PASSWORD,
    USER_PASSWORD,
    csrf_from,
    latest_email_token,
    login_csrf_from,
    web_login,
)


def test_ready_endpoint(client) -> None:
    response = client.get("/ready")
    assert response.status_code == 200
    assert response.json() == {"status": "ready"}


def test_login_rejects_missing_csrf_and_bad_credentials(client) -> None:
    missing = client.post(
        "/login",
        data={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD, "next": "/profile"},
    )
    assert missing.status_code == 403

    preauth = login_csrf_from(client.get("/login").text)
    wrong = client.post(
        "/login",
        data={
            "email": ADMIN_EMAIL,
            "password": "definitely-wrong",
            "next": "/profile",
            "preauth_csrf_token": preauth,
        },
    )
    assert wrong.status_code == 400
    assert "Unable to sign in" in wrong.text


def test_authenticated_home_and_login_redirect(client) -> None:
    web_login(client)
    home = client.get("/")
    assert home.status_code == 303
    assert "/profile" in home.headers["location"]
    login_page = client.get("/login?next=/security")
    assert login_page.status_code == 303
    assert "/security" in login_page.headers["location"]


def test_logout_clears_session(client) -> None:
    web_login(client)
    profile = client.get("/profile")
    assert profile.status_code == 200
    csrf = csrf_from(profile.text)
    logged_out = client.post("/logout", data={"csrf_token": csrf})
    assert logged_out.status_code == 303
    assert "/login" in logged_out.headers["location"]
    blocked = client.get("/profile")
    assert blocked.status_code in {302, 303, 401}
    if blocked.status_code in {302, 303}:
        assert "/login" in blocked.headers["location"]


def test_session_csrf_required_for_profile_mutation(client) -> None:
    web_login(client)
    rejected = client.post(
        "/profile",
        data={
            "full_name": "Forged",
            "organization": "",
            "job_title": "",
            "phone": "",
        },
    )
    assert rejected.status_code == 403


def test_password_recovery_flow(client) -> None:
    unknown = client.post("/password/forgot", data={"email": "unknown@example.gov"})
    assert unknown.status_code == 200
    assert "reset link was sent" in unknown.text.lower() or "account exists" in unknown.text.lower()

    requested = client.post("/password/forgot", data={"email": ADMIN_EMAIL})
    assert requested.status_code == 200
    token = latest_email_token(ADMIN_EMAIL, subject_like="%password%")
    page = client.get(f"/password/reset?token={token}")
    assert page.status_code == 200

    mismatch = client.post(
        "/password/reset",
        data={
            "token": token,
            "password": NEW_PASSWORD,
            "password_confirm": "different-password",
        },
    )
    assert mismatch.status_code == 400

    changed = client.post(
        "/password/reset",
        data={
            "token": token,
            "password": NEW_PASSWORD,
            "password_confirm": NEW_PASSWORD,
        },
    )
    assert changed.status_code in {200, 303}
    web_login(client, ADMIN_EMAIL, NEW_PASSWORD)

    # Single-use token
    replay = client.post(
        "/password/reset",
        data={
            "token": token,
            "password": NEW_PASSWORD,
            "password_confirm": NEW_PASSWORD,
        },
    )
    assert replay.status_code == 400


def test_password_change_signs_out(client) -> None:
    web_login(client)
    security = client.get("/security")
    assert security.status_code == 200
    csrf = csrf_from(security.text)
    wrong = client.post(
        "/security/password",
        data={
            "csrf_token": csrf,
            "current_password": "wrong",
            "new_password": NEW_PASSWORD,
            "new_password_confirm": NEW_PASSWORD,
        },
    )
    assert wrong.status_code == 400
    assert "Current password is incorrect" in wrong.text

    changed = client.post(
        "/security/password",
        data={
            "csrf_token": csrf_from(client.get("/security").text),
            "current_password": ADMIN_PASSWORD,
            "new_password": NEW_PASSWORD,
            "new_password_confirm": NEW_PASSWORD,
        },
    )
    assert changed.status_code == 303
    assert "password=changed" in changed.headers["location"]
    blocked = client.get("/profile")
    assert blocked.status_code in {302, 303, 401}


def test_login_rate_limit_html(client) -> None:
    settings = get_settings()
    original_source = settings.rate_limit_login_per_source
    original_account = settings.rate_limit_login_per_account
    settings.rate_limit_login_per_source = 1
    settings.rate_limit_login_per_account = 1
    try:
        preauth = login_csrf_from(client.get("/login").text)
        rejected = client.post(
            "/login",
            data={
                "email": "missing@example.gov",
                "password": "wrong",
                "next": "/profile",
                "preauth_csrf_token": preauth,
            },
        )
        assert rejected.status_code == 400
        preauth = login_csrf_from(rejected.text)
        limited = client.post(
            "/login",
            data={
                "email": "missing@example.gov",
                "password": "wrong",
                "next": "/profile",
                "preauth_csrf_token": preauth,
            },
            headers={"Accept": "text/html"},
        )
        assert limited.status_code == 429
        assert limited.headers.get("retry-after")
        with SessionLocal() as db:
            assert db.scalars(select(RateLimitBucket)).first() is not None
            event = db.scalar(
                select(AuditEvent).where(AuditEvent.event_type == "security.rate_limited")
            )
            assert event is not None
    finally:
        settings.rate_limit_login_per_source = original_source
        settings.rate_limit_login_per_account = original_account


def test_registration_and_reset_rate_limits(client) -> None:
    settings = get_settings()
    original_reg_source = settings.rate_limit_registration_per_source
    original_reg_account = settings.rate_limit_registration_per_account
    original_reset_source = settings.rate_limit_reset_per_source
    original_reset_account = settings.rate_limit_reset_per_account
    settings.rate_limit_registration_per_source = 1
    settings.rate_limit_registration_per_account = 1
    settings.rate_limit_reset_per_source = 1
    settings.rate_limit_reset_per_account = 1
    try:
        first = client.post(
            "/register",
            data={"email": "rate.reg@example.gov", "full_name": "Rate Reg"},
        )
        limited = client.post(
            "/register",
            data={"email": "rate.reg@example.gov", "full_name": "Rate Reg"},
        )
        assert first.status_code == 202
        assert limited.status_code == 429

        reset_first = client.post("/password/forgot", data={"email": "rate.reset@example.gov"})
        reset_limited = client.post("/password/forgot", data={"email": "rate.reset@example.gov"})
        assert reset_first.status_code == 200
        assert reset_limited.status_code == 429
    finally:
        settings.rate_limit_registration_per_source = original_reg_source
        settings.rate_limit_registration_per_account = original_reg_account
        settings.rate_limit_reset_per_source = original_reset_source
        settings.rate_limit_reset_per_account = original_reset_account


def test_session_revoke_success(client, make_user, hedron_app) -> None:
    from fastapi.testclient import TestClient

    from app.dependencies import ACCESS_COOKIE
    from app.security.tokens import decode_access_token

    user = make_user("sessions.user@example.gov")
    web_login(client, user.email, USER_PASSWORD)
    other = TestClient(hedron_app, follow_redirects=False)
    web_login(other, user.email, USER_PASSWORD)

    current_sid = decode_access_token(client.cookies.get(ACCESS_COOKIE), get_settings())["sid"]
    with SessionLocal() as db:
        remote = db.scalar(
            select(RefreshSession).where(
                RefreshSession.user_id == user.id,
                RefreshSession.id != current_sid,
                RefreshSession.revoked_at.is_(None),
            )
        )
        assert remote is not None
        remote_id = remote.id

    csrf = csrf_from(client.get("/security").text)
    revoked = client.post(
        f"/security/sessions/{remote_id}/revoke",
        data={"csrf_token": csrf},
    )
    assert revoked.status_code == 303
    assert "session-revoked" in revoked.headers["location"]
    with SessionLocal() as db:
        session = db.get(RefreshSession, remote_id)
        assert session is not None and session.revoked_at is not None
