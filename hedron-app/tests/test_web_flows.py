"""Admin directory, toggle, audit, secrets, and profile coverage for Hedron UI."""

from __future__ import annotations

from sqlalchemy import select

from app.database import SessionLocal
from app.models import AuditEvent, User, UserSecret, UserStatus
from tests.helpers import (
    ADMIN_EMAIL,
    USER_PASSWORD,
    csrf_from,
    web_login,
)

ADVANA_TOKEN = "advana-secret-token-value-123456"


def test_profile_update_redirect_and_htmx(client) -> None:
    web_login(client)
    csrf = csrf_from(client.get("/profile").text)
    redirected = client.post(
        "/profile",
        data={
            "csrf_token": csrf,
            "full_name": "Updated Administrator",
            "organization": "Operations",
            "job_title": "Admin",
            "phone": "",
        },
    )
    assert redirected.status_code == 303
    assert "updated=true" in redirected.headers["location"]
    page = client.get(redirected.headers["location"])
    assert page.status_code == 200
    assert "Updated Administrator" in page.text

    fragment = client.post(
        "/profile",
        data={
            "csrf_token": csrf_from(page.text),
            "full_name": "HTMX Administrator",
            "organization": "Operations",
            "job_title": "Admin",
            "phone": "",
        },
        headers={"HX-Request": "true", "HX-Target": "#profile-form-region"},
    )
    assert fragment.status_code == 200
    assert "profile-form-region" in fragment.text
    assert "HTMX Administrator" in fragment.text
    assert "<html" not in fragment.text.lower()


def test_admin_invite_toggle_filter_and_self_protection(client, make_user) -> None:
    target = make_user("managed.user@example.gov")
    web_login(client, next_path="/admin/users")
    users = client.get("/admin/users")
    assert users.status_code == 200
    assert ADMIN_EMAIL in users.text
    csrf = csrf_from(users.text)

    invited = client.post(
        "/admin/invitations",
        data={"csrf_token": csrf, "email": "new.invite@example.gov", "role": "user"},
    )
    assert invited.status_code == 303
    assert "invitation-queued" in invited.headers["location"]

    with SessionLocal() as db:
        admin = db.scalar(select(User).where(User.email == ADMIN_EMAIL))
        assert admin is not None
        admin_id = admin.id

    self_toggle = client.post(
        f"/admin/users/{admin_id}/toggle",
        data={"csrf_token": csrf_from(client.get("/admin/users").text)},
    )
    assert self_toggle.status_code == 400
    assert "cannot disable your own account" in self_toggle.text.lower()

    disabled = client.post(
        f"/admin/users/{target.id}/toggle",
        data={"csrf_token": csrf_from(client.get("/admin/users").text)},
    )
    assert disabled.status_code == 303
    with SessionLocal() as db:
        user = db.get(User, target.id)
        assert user is not None and user.status == UserStatus.DISABLED.value
        event = db.scalar(
            select(AuditEvent).where(
                AuditEvent.target_user_id == target.id,
                AuditEvent.event_type == "admin.user.status_changed",
            )
        )
        assert event is not None

    filtered = client.get("/admin/users", params={"q": "managed.user", "status": "disabled"})
    assert filtered.status_code == 200
    assert "managed.user@example.gov" in filtered.text


def test_admin_audit_page(client) -> None:
    web_login(client)
    audit = client.get("/admin/audit")
    assert audit.status_code == 200
    assert "auth.login" in audit.text or "Audit" in audit.text or "audit" in audit.text.lower()
    filtered = client.get("/admin/audit", params={"event_type": "auth.login"})
    assert filtered.status_code == 200


def test_non_admin_forbidden_from_admin_pages(client, make_user) -> None:
    user = make_user("standard.user@example.gov")
    web_login(client, user.email, USER_PASSWORD)
    forbidden = client.get("/admin/users")
    assert forbidden.status_code == 403
    audit = client.get("/admin/audit")
    assert audit.status_code == 403


def test_secret_save_and_delete_never_reveal_token(client, make_user) -> None:
    user = make_user("secrets.user@example.gov")
    web_login(client, user.email, USER_PASSWORD)
    security = client.get("/security")
    assert security.status_code == 200
    assert "Advana" in security.text
    csrf = csrf_from(security.text)

    saved = client.post(
        "/security/secrets/advana",
        data={"csrf_token": csrf, "token": ADVANA_TOKEN},
    )
    assert saved.status_code == 303
    assert "secret-saved" in saved.headers["location"]
    page = client.get(saved.headers["location"])
    assert page.status_code == 200
    assert ADVANA_TOKEN not in page.text
    assert "ciphertext" not in page.text

    with SessionLocal() as db:
        stored = db.scalar(
            select(UserSecret).where(
                UserSecret.user_id == user.id,
                UserSecret.provider == "advana",
            )
        )
        assert stored is not None
        assert ADVANA_TOKEN not in stored.ciphertext

    deleted = client.post(
        "/security/secrets/advana/delete",
        data={"csrf_token": csrf_from(page.text)},
    )
    assert deleted.status_code == 303
    assert "secret-deleted" in deleted.headers["location"]
    with SessionLocal() as db:
        remaining = db.scalar(
            select(UserSecret).where(
                UserSecret.user_id == user.id,
                UserSecret.provider == "advana",
            )
        )
        assert remaining is None

    unknown = client.post(
        "/security/secrets/not-a-provider",
        data={"csrf_token": csrf_from(client.get("/security").text), "token": "x"},
    )
    assert unknown.status_code == 404
