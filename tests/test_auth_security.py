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
    preauth_post,
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


def test_login_accepts_mount_cookie_when_stale_root_duplicate_follows(client) -> None:
    page = client.get("/login")
    preauth = login_csrf_from(page.text)
    client.cookies.clear()
    signed_in = client.post(
        "/login",
        data={
            "email": ADMIN_EMAIL,
            "password": ADMIN_PASSWORD,
            "next": "/profile",
            "preauth_csrf_token": preauth,
        },
        headers={
            "Cookie": (
                f"access_registry_login_csrf={preauth}; access_registry_login_csrf=stale-root-value"
            )
        },
    )
    assert signed_in.status_code == 303


def test_auth_accepts_mount_cookie_when_stale_root_duplicate_follows(client) -> None:
    web_login(client)
    access = client.cookies.get("access_registry_access")
    assert access
    client.cookies.clear()
    profile = client.get(
        "/profile",
        headers={
            "Cookie": (f"access_registry_access={access}; access_registry_access=stale-root-value")
        },
    )
    assert profile.status_code == 200


def test_refresh_accepts_mount_cookie_when_stale_root_duplicate_follows(client) -> None:
    web_login(client)
    refresh = client.cookies.get("access_registry_refresh")
    assert refresh
    client.cookies.clear()
    profile = client.get(
        "/profile",
        headers={
            "Cookie": (
                "access_registry_access=expired-or-stale; "
                f"access_registry_refresh={refresh}; "
                "access_registry_refresh=stale-root-value"
            )
        },
    )
    assert profile.status_code == 200
    assert "access_registry_refresh" in profile.headers["set-cookie"]


def test_login_cookie_diagnostics_never_log_secrets(client, monkeypatch, capsys) -> None:
    monkeypatch.setenv("ACCESS_REGISTRY_DEV_TRACE", "1")
    page = client.get("/login")
    preauth = login_csrf_from(page.text)
    password = "diagnostic-password-that-must-not-be-logged"
    client.post(
        "/login",
        data={
            "email": ADMIN_EMAIL,
            "password": password,
            "next": "/profile",
            "preauth_csrf_token": preauth,
        },
    )
    output = capsys.readouterr().out
    assert "csrf.preauth.cookie.issued" in output
    assert "csrf.preauth.accepted" in output
    assert "auth.password.rejected" in output
    assert preauth not in output
    assert password not in output


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
    unknown = preauth_post(client, "/password/forgot", {"email": "unknown@example.gov"})
    assert unknown.status_code == 200
    assert "reset link was sent" in unknown.text.lower() or "account exists" in unknown.text.lower()

    requested = preauth_post(client, "/password/forgot", {"email": ADMIN_EMAIL})
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
    assert 'name="password"' in mismatch.text
    assert "Passwords do not match" in mismatch.text

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
    assert 'name="password"' not in replay.text
    assert "Request a new reset link" in replay.text


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
        first = preauth_post(
            client, "/register", {"email": "rate.reg@example.gov", "full_name": "Rate Reg"}
        )
        limited = preauth_post(
            client, "/register", {"email": "rate.reg@example.gov", "full_name": "Rate Reg"}
        )
        assert first.status_code == 202
        assert limited.status_code == 429

        reset_first = preauth_post(client, "/password/forgot", {"email": "rate.reset@example.gov"})
        reset_limited = preauth_post(
            client, "/password/forgot", {"email": "rate.reset@example.gov"}
        )
        assert reset_first.status_code == 200
        assert reset_limited.status_code == 429
    finally:
        settings.rate_limit_registration_per_source = original_reg_source
        settings.rate_limit_registration_per_account = original_reg_account
        settings.rate_limit_reset_per_source = original_reset_source
        settings.rate_limit_reset_per_account = original_reset_account


def test_session_revoke_success(client, make_user, access_app) -> None:
    from fastapi.testclient import TestClient

    from app.dependencies import ACCESS_COOKIE
    from app.security.tokens import decode_access_token

    user = make_user("sessions.user@example.gov")
    web_login(client, user.email, USER_PASSWORD)
    other = TestClient(access_app, follow_redirects=False)
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
    notice = client.get(revoked.headers["location"])
    assert "browser session was revoked" in notice.text
    with SessionLocal() as db:
        session = db.get(RefreshSession, remote_id)
        assert session is not None and session.revoked_at is not None

    # HTMX revoke of another remote session
    third = TestClient(access_app, follow_redirects=False)
    web_login(third, user.email, USER_PASSWORD)
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
    htmx = client.post(
        f"/security/sessions/{remote_id}/revoke",
        data={"csrf_token": csrf_from(client.get("/security").text)},
        headers={"HX-Request": "true", "HX-Target": "#session-list"},
    )
    assert htmx.status_code == 200
    assert 'id="session-list"' in htmx.text
    assert "<html" not in htmx.text.lower()


def test_login_lockout_is_generic_audited_and_blocks_correct_password(client) -> None:
    for _ in range(5):
        preauth = login_csrf_from(client.get("/login").text)
        rejected = client.post(
            "/login",
            data={
                "email": ADMIN_EMAIL,
                "password": "definitely-wrong",
                "next": "/profile",
                "preauth_csrf_token": preauth,
            },
        )
        assert rejected.status_code == 400
        assert "Unable to sign in" in rejected.text

    preauth = login_csrf_from(client.get("/login").text)
    locked = client.post(
        "/login",
        data={
            "email": ADMIN_EMAIL,
            "password": ADMIN_PASSWORD,
            "next": "/profile",
            "preauth_csrf_token": preauth,
        },
    )
    assert locked.status_code == 400
    assert "Unable to sign in" in locked.text

    preauth = login_csrf_from(client.get("/login").text)
    unknown = client.post(
        "/login",
        data={
            "email": "unknown@example.gov",
            "password": "definitely-wrong",
            "next": "/profile",
            "preauth_csrf_token": preauth,
        },
    )
    assert unknown.status_code == 400
    assert "Unable to sign in" in unknown.text

    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.email == ADMIN_EMAIL))
        assert user is not None
        assert user.failed_login_attempts == 5
        outcomes = db.scalars(
            select(AuditEvent.outcome)
            .where(AuditEvent.event_type == "auth.login")
            .order_by(AuditEvent.occurred_at)
        ).all()
        assert outcomes == ["failure"] * 5 + ["locked"]


def test_htmx_unauthenticated_redirect_and_admin_error_retarget(client) -> None:
    unauthenticated = client.post(
        "/profile",
        data={"csrf_token": "expired", "full_name": "Expired"},
        headers={"HX-Request": "true", "HX-Target": "#profile-form-region"},
    )
    assert unauthenticated.status_code == 303
    assert unauthenticated.headers.get("HX-Redirect", "").startswith("/login?next=")

    web_login(client)
    users = client.get("/admin/users")
    csrf = csrf_from(users.text)
    with SessionLocal() as db:
        admin = db.scalar(select(User).where(User.email == ADMIN_EMAIL))
        assert admin is not None
        admin_id = admin.id
    rejected = client.post(
        f"/admin/users/{admin_id}/toggle",
        data={"csrf_token": csrf},
        headers={"HX-Request": "true", "HX-Target": "#user-directory-body"},
    )
    assert rejected.status_code == 400
    assert rejected.headers.get("HX-Retarget") == "#global-feedback"
    assert rejected.headers.get("HX-Reswap") == "innerHTML"
    assert "cannot disable your own account" in rejected.text.lower()
    assert "<html" not in rejected.text.lower()


def test_password_change_and_reset_validation_edges(client) -> None:
    web_login(client)
    csrf = csrf_from(client.get("/security").text)

    mismatch = client.post(
        "/security/password",
        data={
            "csrf_token": csrf,
            "current_password": ADMIN_PASSWORD,
            "new_password": NEW_PASSWORD,
            "new_password_confirm": "different-password",
        },
    )
    assert mismatch.status_code == 400
    assert "do not match" in mismatch.text.lower()

    weak = client.post(
        "/security/password",
        data={
            "csrf_token": csrf_from(client.get("/security").text),
            "current_password": ADMIN_PASSWORD,
            "new_password": "too-short",
            "new_password_confirm": "too-short",
        },
        headers={"HX-Request": "true", "HX-Target": "#password-form-region"},
    )
    assert weak.status_code == 400
    assert "at least 15" in weak.text
    assert "<html" not in weak.text.lower()

    missing_session = client.post(
        "/security/sessions/00000000-0000-0000-0000-000000000000/revoke",
        data={"csrf_token": csrf_from(client.get("/security").text)},
    )
    assert missing_session.status_code == 404

    client.cookies.clear()
    assert preauth_post(client, "/password/forgot", {"email": ADMIN_EMAIL}).status_code == 200
    token = latest_email_token(ADMIN_EMAIL, subject_like="%password%")
    mismatch_reset = client.post(
        "/password/reset",
        data={
            "token": token,
            "password": NEW_PASSWORD,
            "password_confirm": "different-password",
        },
    )
    assert mismatch_reset.status_code == 400
    weak_reset = client.post(
        "/password/reset",
        data={"token": token, "password": "too-short", "password_confirm": "too-short"},
    )
    assert weak_reset.status_code == 400


def test_login_next_rejects_open_redirect(client) -> None:
    preauth = login_csrf_from(client.get("/login").text)
    response = client.post(
        "/login",
        data={
            "email": ADMIN_EMAIL,
            "password": ADMIN_PASSWORD,
            "next": "//evil.example/phish",
            "preauth_csrf_token": preauth,
        },
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/profile"


def test_password_reset_supersedes_prior_and_rejects_expiry(client) -> None:
    from datetime import timedelta

    from app.models import PasswordReset, utcnow

    unknown = preauth_post(client, "/password/forgot", {"email": "nobody@example.gov"})
    assert unknown.status_code == 200
    with SessionLocal() as db:
        assert db.scalar(select(PasswordReset)) is None

    assert preauth_post(client, "/password/forgot", {"email": ADMIN_EMAIL}).status_code == 200
    first_token = latest_email_token(ADMIN_EMAIL, subject_like="%password%")
    assert preauth_post(client, "/password/forgot", {"email": ADMIN_EMAIL}).status_code == 200
    second_token = latest_email_token(ADMIN_EMAIL, subject_like="%password%")
    assert first_token != second_token

    with SessionLocal() as db:
        resets = list(db.scalars(select(PasswordReset).order_by(PasswordReset.created_at)).all())
        assert len(resets) == 2
        assert resets[0].used_at is not None
        assert resets[1].used_at is None
        resets[1].expires_at = utcnow() - timedelta(seconds=1)
        db.commit()

    expired = client.post(
        "/password/reset",
        data={
            "token": second_token,
            "password": NEW_PASSWORD,
            "password_confirm": NEW_PASSWORD,
        },
    )
    assert expired.status_code == 400
    assert "invalid or expired" in expired.text.lower()


def test_register_and_forgot_require_preauth_csrf(client) -> None:
    missing_register = client.post(
        "/register",
        data={"email": "csrf.reg@example.gov", "full_name": "CSRF Reg"},
    )
    assert missing_register.status_code == 403

    missing_forgot = client.post("/password/forgot", data={"email": ADMIN_EMAIL})
    assert missing_forgot.status_code == 403


def test_login_mount_prefixes_forms_and_assets(client) -> None:
    response = client.get(
        "/login",
        headers={"RStudio-Connect-App-Base-URL": "https://connect.example.gov/content/abc"},
    )
    # Without a trusted proxy peer the Connect header is ignored; cookies still use root.
    assert 'action="/login"' in response.text or 'action="https://' not in response.text

    from starlette.requests import Request

    from app.ui.urls import form_action, mounted_path, page_href

    mounted = Request(
        {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": "/login",
            "raw_path": b"/login",
            "query_string": b"",
            "headers": [],
            "client": ("127.0.0.1", 0),
            "server": ("test", 80),
            "root_path": "/content/abc",
        }
    )
    assert mounted_path(mounted, "/login") == "/content/abc/login"
    assert mounted_path(mounted, "/") == "/content/abc"
    assert str(form_action(mounted, "login")).endswith("/content/abc/login")
    assert str(page_href(mounted, "/assets/theme.css")).endswith("/content/abc/assets/theme.css")
