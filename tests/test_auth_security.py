"""Auth, CSRF, logout, password recovery, and rate-limit coverage for Hedron UI."""

from __future__ import annotations

import json
from datetime import timedelta
from urllib.parse import urljoin

from sqlalchemy import select

from app.config import get_settings
from app.database import SessionLocal
from app.models import (
    AuditEvent,
    RateLimitBucket,
    RefreshSession,
    RefreshTokenHistory,
    User,
    utcnow,
)
from app.security.cookies import SESSION_CSRF_COOKIE
from app.security.tokens import hash_token
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


def test_overlapping_refreshes_return_same_successor_without_revoking_session(client) -> None:
    web_login(client)
    original = client.cookies.get("access_registry_refresh")
    csrf_proof = client.cookies.get(SESSION_CSRF_COOKIE)
    assert original
    assert csrf_proof
    client.cookies.clear()
    stale_cookies = {
        "Cookie": (
            f"access_registry_access=expired; access_registry_refresh={original}; "
            f"{SESSION_CSRF_COOKIE}={csrf_proof}"
        )
    }

    first = client.get("/profile", headers=stale_cookies)
    client.cookies.clear()
    second = client.get("/profile", headers=stale_cookies)

    assert first.status_code == second.status_code == 200
    replacement = first.cookies.get("access_registry_refresh")
    assert replacement and replacement != original
    assert second.cookies.get("access_registry_refresh") == replacement
    with SessionLocal() as db:
        session = db.scalar(select(RefreshSession))
        assert session and session.revoked_at is None
        assert session.refresh_token_hash == hash_token(replacement, get_settings().session_pepper)
        assert len(db.scalars(select(RefreshTokenHistory)).all()) == 1
        assert db.scalar(
            select(AuditEvent).where(AuditEvent.event_type == "auth.session.refresh_duplicate")
        )
        assert not db.scalar(
            select(AuditEvent).where(AuditEvent.event_type == "auth.session.refresh_reuse")
        )


def test_overlapping_refresh_without_proof_retries_without_revoking_session(client) -> None:
    web_login(client)
    original = client.cookies.get("access_registry_refresh")
    csrf_proof = client.cookies.get(SESSION_CSRF_COOKIE)
    assert original and csrf_proof
    client.cookies.clear()

    first = client.get(
        "/profile",
        headers={
            "Cookie": (
                f"access_registry_access=expired; access_registry_refresh={original}; "
                f"{SESSION_CSRF_COOKIE}={csrf_proof}"
            )
        },
    )
    replacement = first.cookies.get("access_registry_refresh")
    assert first.status_code == 200 and replacement

    client.cookies.clear()
    denied = client.get(
        "/profile",
        headers={"Cookie": f"access_registry_access=expired; access_registry_refresh={original}"},
    )

    assert denied.status_code == 409
    assert denied.headers["retry-after"] == "1"
    with SessionLocal() as db:
        session = db.scalar(select(RefreshSession))
        event = db.scalar(
            select(AuditEvent).where(
                AuditEvent.event_type == "auth.session.refresh_duplicate_denied"
            )
        )
        assert session and session.revoked_at is None
        assert session.refresh_token_hash == hash_token(replacement, get_settings().session_pepper)
        assert event and event.outcome == "denied"
        assert event.actor_user_id == event.target_user_id == session.user_id
        assert json.loads(event.detail)["user_id"] == session.user_id


def test_old_refresh_reuse_after_overlap_window_revokes_session(client) -> None:
    web_login(client)
    original = client.cookies.get("access_registry_refresh")
    assert original
    client.cookies.clear()
    stale_cookies = {
        "Cookie": f"access_registry_access=expired; access_registry_refresh={original}"
    }
    assert client.get("/profile", headers=stale_cookies).status_code == 200
    with SessionLocal() as db:
        history = db.scalar(select(RefreshTokenHistory))
        assert history
        history.consumed_at = utcnow() - timedelta(minutes=1)
        db.commit()

    client.cookies.clear()
    denied = client.get("/profile", headers=stale_cookies)
    assert denied.status_code == 401
    with SessionLocal() as db:
        session = db.scalar(select(RefreshSession))
        assert session and session.revoked_at is not None
        assert db.scalar(
            select(AuditEvent).where(AuditEvent.event_type == "auth.session.refresh_reuse")
        )


def test_old_refresh_reuse_after_next_rotation_revokes_session(client) -> None:
    web_login(client)
    original = client.cookies.get("access_registry_refresh")
    assert original
    client.cookies.clear()
    first = client.get(
        "/profile",
        headers={"Cookie": f"access_registry_access=expired; access_registry_refresh={original}"},
    )
    successor = first.cookies.get("access_registry_refresh")
    assert first.status_code == 200 and successor
    client.cookies.clear()
    second = client.get(
        "/profile",
        headers={"Cookie": f"access_registry_access=expired; access_registry_refresh={successor}"},
    )
    assert second.status_code == 200

    client.cookies.clear()
    denied = client.get(
        "/profile",
        headers={"Cookie": f"access_registry_access=expired; access_registry_refresh={original}"},
    )
    assert denied.status_code == 401
    with SessionLocal() as db:
        session = db.scalar(select(RefreshSession))
        assert session and session.revoked_at is not None


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
    assert "/pipeline" in home.headers["location"]
    login_page = client.get("/login?next=/security")
    assert login_page.status_code == 303
    assert "/security" in login_page.headers["location"]


def test_login_redirect_preserves_local_query_parameters(client) -> None:
    next_path = "/pipeline?notice=saved&pipeline_id=route-123"
    preauth = login_csrf_from(client.get(f"/login?next={next_path}").text)
    response = client.post(
        "/login",
        data={
            "email": ADMIN_EMAIL,
            "password": ADMIN_PASSWORD,
            "next": next_path,
            "preauth_csrf_token": preauth,
        },
    )

    assert response.status_code == 303
    assert response.headers["location"] == next_path


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
    account = client.get("/profile")
    assert account.status_code == 200
    csrf = csrf_from(account.text)
    wrong = client.post(
        "/profile/password",
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
        "/profile/password",
        data={
            "csrf_token": csrf_from(client.get("/profile").text),
            "current_password": ADMIN_PASSWORD,
            "new_password": NEW_PASSWORD,
            "new_password_confirm": NEW_PASSWORD,
        },
    )
    assert changed.status_code == 303
    assert "password=changed" in changed.headers["location"]
    blocked = client.get("/profile")
    assert blocked.status_code in {302, 303, 401}


def test_login_rate_limit_html(client, request_settings_override) -> None:
    settings = get_settings()
    original_source = settings.rate_limit_login_per_source
    original_account = settings.rate_limit_login_per_account
    settings.rate_limit_login_per_source = 1
    settings.rate_limit_login_per_account = 1
    request_settings_override(settings)
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


def test_registration_and_reset_rate_limits(client, request_settings_override) -> None:
    settings = get_settings()
    original_reg_source = settings.rate_limit_registration_per_source
    original_reg_account = settings.rate_limit_registration_per_account
    original_reset_source = settings.rate_limit_reset_per_source
    original_reset_account = settings.rate_limit_reset_per_account
    settings.rate_limit_registration_per_source = 1
    settings.rate_limit_registration_per_account = 1
    settings.rate_limit_reset_per_source = 1
    settings.rate_limit_reset_per_account = 1
    request_settings_override(settings)
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

    csrf = csrf_from(client.get("/profile").text)
    revoked = client.post(
        f"/profile/sessions/{remote_id}/revoke",
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
        f"/profile/sessions/{remote_id}/revoke",
        data={"csrf_token": csrf_from(client.get("/profile").text)},
        headers={"HX-Request": "true", "HX-Target": "#session-list"},
    )
    assert htmx.status_code == 200
    assert 'id="session-list"' in htmx.text
    assert "<html" not in htmx.text.lower()


def test_login_lockout_is_generic_audited_and_blocks_correct_password(client) -> None:
    for _attempt in range(5):
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
        assert "temporarily locked" not in rejected.text

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
    assert "temporarily locked" not in locked.text

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
        assert user.locked_until is not None
        outcomes = db.scalars(
            select(AuditEvent.outcome)
            .where(AuditEvent.event_type == "auth.login")
            .order_by(AuditEvent.occurred_at)
        ).all()
        assert outcomes == ["failure"] * 4 + ["locked", "locked"]


def test_htmx_unauthenticated_redirect_and_admin_error_retarget(client) -> None:
    unauthenticated = client.post(
        "/profile",
        data={"csrf_token": "expired", "full_name": "Expired"},
        headers={"HX-Request": "true", "HX-Target": "#profile-form-region"},
    )
    assert unauthenticated.status_code == 200
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
    assert rejected.headers.get("HX-Retarget") == "#hedron-toast"
    assert rejected.headers.get("HX-Reswap") == "innerHTML"
    assert "cannot disable your own account" in rejected.text.lower()
    assert "<html" not in rejected.text.lower()


def test_htmx_unauthenticated_redirect_is_root_local_when_workbench_is_active(
    client, monkeypatch
) -> None:
    from app.main import app

    monkeypatch.setattr(app.state, "hedron_workbench_active", True, raising=False)
    response = client.post(
        "/profile",
        data={"csrf_token": "expired", "full_name": "Expired"},
        headers={"HX-Request": "true", "HX-Target": "#profile-form-region"},
    )

    assert response.status_code == 200
    assert response.headers.get("HX-Redirect") == "/login?next=%2Fprofile"


def test_password_change_and_reset_validation_edges(client) -> None:
    web_login(client)
    csrf = csrf_from(client.get("/profile").text)

    mismatch = client.post(
        "/profile/password",
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
        "/profile/password",
        data={
            "csrf_token": csrf_from(client.get("/profile").text),
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
        "/profile/sessions/00000000-0000-0000-0000-000000000000/revoke",
        data={"csrf_token": csrf_from(client.get("/profile").text)},
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
    assert response.headers["location"] == "/pipeline"


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

    from app.ui.urls import form_action, mounted_path

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


def test_workbench_redirects_are_relative_for_both_entry_points() -> None:
    from types import SimpleNamespace

    from starlette.requests import Request

    from app.ui.urls import redirect_path

    app = SimpleNamespace(state=SimpleNamespace(hedron_workbench_active=True))
    login = Request(
        {
            "type": "http",
            "path": "/login",
            "root_path": "/s/session/p/port",
            "headers": [],
            "app": app,
        }
    )
    assert redirect_path(login, "/profile") == "profile"

    admin = Request(
        {
            "type": "http",
            "path": "/admin/users",
            "root_path": "/s/session/p/port",
            "headers": [],
            "app": app,
        }
    )
    assert redirect_path(admin, "/admin/users?notice=queued") == "users?notice=queued"

    pipeline_save = Request(
        {
            "type": "http",
            "path": "/pipeline/save",
            "root_path": "/s/session/p/port",
            "headers": [],
            "app": app,
        }
    )
    saved_location = redirect_path(
        pipeline_save,
        "/pipeline?notice=saved&pipeline_id=route-123",
    )
    assert saved_location == "../pipeline?notice=saved&pipeline_id=route-123"
    assert urljoin(
        "https://workbench.example/s/session/p/port/pipeline/save",
        saved_location,
    ) == ("https://workbench.example/s/session/p/port/pipeline?notice=saved&pipeline_id=route-123")

    nested_action = Request(
        {
            "type": "http",
            "path": "/security/secrets/mss",
            "root_path": "/s/session/p/port",
            "headers": [],
            "app": app,
        }
    )
    secret_location = redirect_path(nested_action, "/security?notice=secret-saved")
    assert secret_location == "../../security?notice=secret-saved"
    assert (
        urljoin(
            "https://workbench.example/s/session/p/port/security/secrets/mss",
            secret_location,
        )
        == "https://workbench.example/s/session/p/port/security?notice=secret-saved"
    )


def test_htmx_redirect_path_is_root_local_for_workbench() -> None:
    from app.ui.urls import htmx_redirect_path

    assert htmx_redirect_path("/login?password=changed") == "/login?password=changed"
    assert htmx_redirect_path("login") == "/login"
