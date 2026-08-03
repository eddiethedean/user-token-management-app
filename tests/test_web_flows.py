import re
from urllib.parse import parse_qs, urlparse

from sqlalchemy import select

from app.database import SessionLocal
from app.models import EmailOutbox, Invitation, User, UserStatus

ADMIN_EMAIL = "admin@example.gov"
ADMIN_PASSWORD = "River-Lantern-94!Blue"
NEW_PASSWORD = "Quartz-Beacon-62!Harbor"


def csrf_from(html: str) -> str:
    match = re.search(r'name="csrf_token" value="([^"]+)"', html)
    assert match is not None
    return match.group(1)


def login_csrf_from(html: str) -> str:
    match = re.search(r'name="preauth_csrf_token" value="([^"]+)"', html)
    assert match is not None
    return match.group(1)


def web_login(client, email: str, password: str) -> None:
    preauth_csrf = login_csrf_from(client.get("/login").text)
    response = client.post(
        "/login",
        data={
            "email": email,
            "password": password,
            "next": "/profile",
            "preauth_csrf_token": preauth_csrf,
        },
    )
    assert response.status_code == 303, response.text
    assert response.headers["location"] == "/profile"


def latest_reset_token(email: str) -> str:
    with SessionLocal() as db:
        message = db.scalar(
            select(EmailOutbox)
            .where(EmailOutbox.recipient == email)
            .order_by(EmailOutbox.created_at.desc())
        )
        assert message is not None
        link = re.search(r"https://[^\s]+", message.body_text)
        assert link is not None
        return parse_qs(urlparse(link.group(0)).query)["token"][0]


def test_web_login_errors_and_authenticated_redirects(client) -> None:
    missing_csrf = client.post(
        "/login",
        data={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD, "next": "/profile"},
    )
    assert missing_csrf.status_code == 403

    preauth_csrf = login_csrf_from(client.get("/login").text)
    invalid = client.post(
        "/login",
        data={
            "email": "not-an-email",
            "password": "wrong",
            "next": "/profile",
            "preauth_csrf_token": preauth_csrf,
        },
    )
    assert invalid.status_code == 400
    assert "valid government email" in invalid.text

    preauth_csrf = login_csrf_from(invalid.text)
    wrong = client.post(
        "/login",
        data={
            "email": ADMIN_EMAIL,
            "password": "wrong",
            "next": "/profile",
            "preauth_csrf_token": preauth_csrf,
        },
    )
    assert wrong.status_code == 400
    assert "Unable to sign in" in wrong.text

    web_login(client, ADMIN_EMAIL, ADMIN_PASSWORD)
    home = client.get("/")
    assert home.status_code == 303
    assert home.headers["location"] == "/profile"
    login_page = client.get("/login?next=/security")
    assert login_page.status_code == 303
    assert login_page.headers["location"] == "/security"


def test_web_password_recovery_errors_and_success(client) -> None:
    assert client.get("/password/forgot").status_code == 200
    unknown = client.post("/password/forgot", data={"email": "unknown@example.gov"})
    assert unknown.status_code == 200
    assert "eligible account exists" in unknown.text

    requested = client.post("/password/forgot", data={"email": ADMIN_EMAIL})
    assert requested.status_code == 200
    token = latest_reset_token(ADMIN_EMAIL)
    assert client.get(f"/password/reset?token={token}").status_code == 200

    mismatch = client.post(
        "/password/reset",
        data={
            "token": token,
            "password": NEW_PASSWORD,
            "password_confirm": "different-password",
        },
    )
    assert mismatch.status_code == 400
    assert "Passwords do not match" in mismatch.text

    weak = client.post(
        "/password/reset",
        data={"token": token, "password": "too-short", "password_confirm": "too-short"},
    )
    assert weak.status_code == 400
    assert "at least 15" in weak.text

    complete = client.post(
        "/password/reset",
        data={"token": token, "password": NEW_PASSWORD, "password_confirm": NEW_PASSWORD},
    )
    assert complete.status_code == 303
    assert complete.headers["location"] == "/login?reset=complete"
    replay = client.get(f"/password/reset?token={token}")
    assert replay.status_code == 400
    web_login(client, ADMIN_EMAIL, NEW_PASSWORD)


def test_invalid_invitation_web_forms_do_not_create_accounts(client) -> None:
    invalid_page = client.get("/invitations/accept?token=invalid")
    assert invalid_page.status_code == 400
    assert "invalid or expired" in invalid_page.text
    invalid_submit = client.post(
        "/invitations/accept",
        data={
            "token": "invalid",
            "full_name": "Invalid User",
            "password": NEW_PASSWORD,
            "password_confirm": NEW_PASSWORD,
        },
    )
    assert invalid_submit.status_code == 400


def test_web_security_password_validation_and_session_errors(client) -> None:
    web_login(client, ADMIN_EMAIL, ADMIN_PASSWORD)
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
        headers={"HX-Request": "true"},
    )
    assert wrong.status_code == 400
    assert "Current password is incorrect" in wrong.text
    assert "<html" not in wrong.text

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
    assert "New passwords do not match" in mismatch.text

    weak = client.post(
        "/security/password",
        data={
            "csrf_token": csrf,
            "current_password": ADMIN_PASSWORD,
            "new_password": "too-short",
            "new_password_confirm": "too-short",
        },
    )
    assert weak.status_code == 400
    assert "at least 15" in weak.text

    missing_session = client.post(
        "/security/sessions/00000000-0000-0000-0000-000000000000/revoke",
        data={"csrf_token": csrf},
    )
    assert missing_session.status_code == 404

    changed = client.post(
        "/security/password",
        data={
            "csrf_token": csrf,
            "current_password": ADMIN_PASSWORD,
            "new_password": NEW_PASSWORD,
            "new_password_confirm": NEW_PASSWORD,
        },
    )
    assert changed.status_code == 303
    assert changed.headers["location"] == "/login?password=changed"
    protected = client.get("/profile", headers={"Accept": "text/html"})
    assert protected.status_code == 303


def test_htmx_session_expiry_and_error_retargeting(client) -> None:
    unauthenticated = client.post(
        "/profile",
        data={"csrf_token": "expired", "full_name": "Expired session"},
        headers={"HX-Request": "true"},
    )
    assert unauthenticated.status_code == 303
    assert unauthenticated.headers["HX-Redirect"].startswith("/login?next=")

    web_login(client, ADMIN_EMAIL, ADMIN_PASSWORD)
    users_page = client.get("/admin/users")
    csrf = csrf_from(users_page.text)
    self_user = client.get("/api/v1/me").json()
    rejected = client.post(
        f"/admin/users/{self_user['id']}/toggle",
        data={"csrf_token": csrf},
        headers={"HX-Request": "true"},
    )
    assert rejected.status_code == 400
    assert rejected.headers["HX-Retarget"] == "#global-feedback"
    assert rejected.headers["HX-Reswap"] == "innerHTML"
    assert "cannot disable your own account" in rejected.text


def test_progressive_profile_and_htmx_admin_fragments(client, make_user) -> None:
    make_user("filter.target@example.gov")
    web_login(client, ADMIN_EMAIL, ADMIN_PASSWORD)
    profile = client.get("/profile")
    csrf = csrf_from(profile.text)

    submitted = client.post(
        "/profile",
        data={
            "csrf_token": csrf,
            "full_name": "Progressive Administrator",
            "organization": "Operations",
            "job_title": "Administrator",
            "phone": "",
        },
    )
    assert submitted.status_code == 303
    assert submitted.headers["location"] == "/profile?updated=true"
    completed = client.get(submitted.headers["location"])
    assert "Your profile has been updated" in completed.text
    assert "<!doctype html>" in completed.text

    filtered = client.get(
        "/admin/users",
        params={"q": "filter.target"},
        headers={"HX-Request": "true"},
    )
    assert filtered.status_code == 200
    assert '<div id="user-directory-region">' in filtered.text
    assert 'id="user-match-count"' in filtered.text
    assert 'hx-swap-oob="outerHTML"' in filtered.text
    assert "filter.target@example.gov" in filtered.text
    assert "<!doctype html>" not in filtered.text

    audit = client.get(
        "/admin/audit",
        params={"event_type": "profile.updated"},
        headers={"HX-Request": "true"},
    )
    assert audit.status_code == 200
    assert 'id="audit-results-region"' in audit.text
    assert 'id="audit-match-count"' in audit.text
    assert "<!doctype html>" not in audit.text


def test_web_admin_invitation_toggle_audit_and_self_protection(client, make_user) -> None:
    normal = make_user("managed.user@example.gov")
    web_login(client, ADMIN_EMAIL, ADMIN_PASSWORD)
    users_page = client.get("/admin/users")
    assert users_page.status_code == 200
    assert "managed.user@example.gov" in users_page.text
    csrf = csrf_from(users_page.text)

    invalid_invite = client.post(
        "/admin/invitations",
        data={"csrf_token": csrf, "email": "new.user@example.gov", "role": "invalid"},
        headers={"HX-Request": "true"},
    )
    assert invalid_invite.status_code == 400
    assert "valid role" in invalid_invite.text
    valid_invite = client.post(
        "/admin/invitations",
        data={"csrf_token": csrf, "email": "new.user@example.gov", "role": "user"},
        headers={"HX-Request": "true"},
    )
    assert valid_invite.status_code == 200
    assert "queued for delivery" in valid_invite.text
    with SessionLocal() as db:
        invitation = db.scalar(select(Invitation).where(Invitation.email == "new.user@example.gov"))
        assert invitation is not None
        invitation_id = invitation.id
    revoked_invite = client.post(
        f"/admin/invitations/{invitation_id}/revoke",
        data={"csrf_token": csrf},
        headers={"HX-Request": "true"},
    )
    assert revoked_invite.status_code == 200
    assert "Invitation revoked" in revoked_invite.text

    disabled = client.post(
        f"/admin/users/{normal.id}/toggle",
        data={"csrf_token": csrf},
        headers={"HX-Request": "true"},
    )
    assert disabled.status_code == 200
    with SessionLocal() as db:
        stored = db.get(User, normal.id)
        assert stored is not None and stored.status == UserStatus.DISABLED.value

    enabled = client.post(
        f"/admin/users/{normal.id}/toggle",
        data={"csrf_token": csrf},
        headers={"HX-Request": "true"},
    )
    assert enabled.status_code == 200
    self_user = client.get("/api/v1/me").json()
    self_toggle = client.post(f"/admin/users/{self_user['id']}/toggle", data={"csrf_token": csrf})
    assert self_toggle.status_code == 400
    missing = client.post(
        "/admin/users/00000000-0000-0000-0000-000000000000/toggle",
        data={"csrf_token": csrf},
    )
    assert missing.status_code == 404
    audit = client.get("/admin/audit")
    assert audit.status_code == 200
    assert "admin.user.status_changed" in audit.text
    filtered = client.get("/admin/users", params={"q": "managed.user", "status": "active"})
    assert filtered.status_code == 200
    assert "managed.user@example.gov" in filtered.text
    assert "1 matching accounts" in filtered.text


def test_web_admin_pages_forbid_standard_user(client, make_user) -> None:
    user = make_user("standard.user@example.gov")
    web_login(client, user.email, "Aspen-Compass-64!River")
    forbidden = client.get("/admin/users", headers={"Accept": "text/html"})
    assert forbidden.status_code == 403
    assert "You do not have permission to perform this action." in forbidden.text
    assert "<!doctype html>" in forbidden.text
