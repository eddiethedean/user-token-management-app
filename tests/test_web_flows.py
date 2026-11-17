"""Admin directory, toggle, audit, secrets, and profile coverage for Hedron UI."""

from __future__ import annotations

import json
from contextlib import closing
from urllib.parse import urljoin
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.database import SessionLocal
from app.models import AuditEvent, Role, User, UserSecret, UserStatus
from tests.helpers import (
    ADMIN_EMAIL,
    ADMIN_PASSWORD,
    MSS_TOKEN,
    USER_PASSWORD,
    as_adapter,
    assert_redirect_path,
    csrf_from,
    login_csrf_from,
    web_login,
)


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


def test_admin_promotes_a_user_and_records_the_change(client, make_user) -> None:
    target = make_user("managed.promotion@example.gov")
    with SessionLocal() as db:
        account = db.get(User, target.id)
        assert account is not None
        account.roles.append(Role(name="reviewer"))
        db.commit()

    web_login(client)
    listing = client.get("/admin/users?q=managed.promotion&status=active")
    action = f"/admin/users/{target.id}/promote"
    assert f'data-promotion-action="{action}"' in listing.text
    form = {
        "csrf_token": csrf_from(listing.text),
        "q": "  managed.promotion ",
        "status": "active",
        "page": "50",
        "role": "administrator, arbitrary-role",
        "account_status": "disabled",
    }
    response = client.post(action, data=form, headers={"X-Request-ID": "promotion-audit"})
    assert response.status_code == 303
    query = {"q": ["managed.promotion"], "status": ["active"], "page": ["1"]}
    assert_redirect_path(response, "/admin/users", query={**query, "notice": ["role-updated"]})
    updated = client.get(response.headers["location"])
    assert "The account is now an administrator. They must sign in again." in updated.text
    assert "Administrator, reviewer, user" in updated.text
    assert f'data-promotion-action="{action}"' not in updated.text

    duplicate = client.post(action, data=form)
    assert duplicate.status_code == 303
    assert_redirect_path(
        duplicate, "/admin/users", query={**query, "notice": ["already-administrator"]}
    )
    assert (
        "This account is already an administrator."
        in client.get(duplicate.headers["location"]).text
    )
    with SessionLocal() as db:
        actor = db.scalar(select(User).where(User.email == ADMIN_EMAIL))
        account = db.get(User, target.id)
        assert actor is not None and account is not None
        assert account.role_names == ["administrator", "reviewer", "user"]
        assert account.security_version == target.security_version + 1
        assert account.status == target.status
        assert account.full_name == target.full_name
        assert account.password_hash == target.password_hash
        event = db.scalars(
            select(AuditEvent).where(AuditEvent.event_type == "admin.user.roles_changed")
        ).one()
        assert (event.actor_user_id, event.target_user_id) == (actor.id, target.id)
        assert event.source_ip == "127.0.0.1"
        assert event.request_id == "promotion-audit"
        detail = json.loads(event.detail)
        assert detail["before_roles"] == ["reviewer", "user"]
        assert detail["after_roles"] == ["administrator", "reviewer", "user"]
    for audit in (
        client.get("/admin/audit?event_type=admin.user.roles_changed"),
        client.get(
            "/admin/audit/results?event_type=admin.user.roles_changed",
            headers={"HX-Request": "true", "HX-Target": "#audit-results-region"},
        ),
    ):
        assert audit.status_code == 200
        details = audit.text.split("<details", 1)[1].split("</details>", 1)[0]
        assert "<dt>Granted by</dt>" in details and ADMIN_EMAIL in details
        assert "<dt>Promoted account</dt>" in details and target.email in details
        assert actor.id in details and target.id in details
        assert "before_roles" in details and "after_roles" in details


def test_promotion_audit_keeps_target_id_when_account_is_unavailable(client) -> None:
    web_login(client)
    target_id = str(uuid4())
    with SessionLocal() as db:
        actor = db.scalar(select(User).where(User.email == ADMIN_EMAIL))
        assert actor is not None
        db.add(
            AuditEvent(
                event_type="admin.user.roles_changed",
                actor_user_id=actor.id,
                detail=json.dumps({"target_user_id": target_id}),
            )
        )
        db.commit()

    audit = client.get("/admin/audit?event_type=admin.user.roles_changed")
    assert audit.status_code == 200
    details = audit.text.split("<details", 1)[1].split("</details>", 1)[0]
    assert "<dt>Granted by</dt>" in details and ADMIN_EMAIL in details
    assert "<dt>Promoted account</dt>" in details and "Account unavailable" in details
    assert target_id in details


@pytest.mark.parametrize("mount", ["/s/session/p/8878", "/proxy/8878"])
def test_admin_promotion_urls_resolve_through_workbench_mounts(
    access_app, make_user, monkeypatch, mount
):
    target = make_user("mounted.promotion@example.gov")
    monkeypatch.setattr(access_app.state, "hedron_workbench_active", True, raising=False)
    monkeypatch.delenv("RS_SERVER_URL", raising=False)
    monkeypatch.delenv("UVICORN_ROOT_PATH", raising=False)
    with closing(
        TestClient(
            access_app,
            base_url="http://workbench.test",
            root_path=mount,
            follow_redirects=False,
            client=("127.0.0.1", 50000),
        )
    ) as mounted:
        login = mounted.get(mount + "/login")
        signed_in = mounted.post(
            mount + "/login",
            data={
                "email": ADMIN_EMAIL,
                "password": ADMIN_PASSWORD,
                "preauth_csrf_token": login_csrf_from(login.text),
                "next": "/admin/users",
            },
        )
        assert signed_in.status_code == 303
        path = mount + "/admin/users"
        page = mounted.get(path)
        assert page.status_code == 200
        action = f"{path}/{target.id}/promote"
        assert f'data-promotion-action="{action}"' in page.text
        response = mounted.post(
            action, data={"csrf_token": csrf_from(page.text), "q": target.email}
        )
        assert response.status_code == 303
        location = response.headers["location"]
        assert not location.startswith("/")
        destination = urljoin(str(response.request.url), location)
        assert destination.startswith(f"http://workbench.test{path}?")
        result = mounted.get(destination)
        assert result.status_code == 200 and "The account is now an administrator" in result.text


def test_admin_audit_page(client) -> None:
    web_login(client)
    with SessionLocal() as db:
        admin = db.scalar(select(User).where(User.email == ADMIN_EMAIL))
        assert admin is not None
        db.add_all(
            [
                AuditEvent(
                    event_type="synthetic.alpha",
                    outcome="success",
                    actor_user_id=admin.id,
                    source_ip="198.51.100.10",
                    detail='{"marker":"ALPHA_SUCCESS"}',
                ),
                AuditEvent(
                    event_type="synthetic.beta",
                    outcome="failure",
                    actor_user_id=admin.id,
                    source_ip="198.51.100.11",
                    detail='{"marker":"BETA_FAILURE"}',
                ),
                AuditEvent(
                    event_type="synthetic.alpha",
                    outcome="failure",
                    actor_user_id=admin.id,
                    source_ip="198.51.100.12",
                    detail='{"marker":"ALPHA_FAILURE"}',
                ),
            ]
        )
        db.commit()

    audit = client.get("/admin/audit")
    assert audit.status_code == 200
    assert "Loading audit activity" in audit.text
    assert "ALPHA_SUCCESS" not in audit.text

    event_filtered = client.get("/admin/audit", params={"event_type": "synthetic.alpha"})
    assert event_filtered.status_code == 200
    assert "2 matching events" in event_filtered.text
    assert "ALPHA_SUCCESS" in event_filtered.text
    assert "ALPHA_FAILURE" in event_filtered.text
    assert "BETA_FAILURE" not in event_filtered.text

    outcome_filtered = client.get("/admin/audit", params={"outcome": "failure"})
    assert outcome_filtered.status_code == 200
    assert "2 matching events" in outcome_filtered.text
    assert "ALPHA_FAILURE" in outcome_filtered.text
    assert "BETA_FAILURE" in outcome_filtered.text
    assert "ALPHA_SUCCESS" not in outcome_filtered.text

    intersection = client.get(
        "/admin/audit",
        params={"event_type": "synthetic.alpha", "outcome": "failure"},
    )
    assert intersection.status_code == 200
    assert "1 matching event" in intersection.text
    assert "ALPHA_FAILURE" in intersection.text
    assert "ALPHA_SUCCESS" not in intersection.text
    assert "BETA_FAILURE" not in intersection.text

    no_matches = client.get("/admin/audit", params={"event_type": "synthetic.missing"})
    assert no_matches.status_code == 200
    assert "No events (0 total)." in no_matches.text


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
    assert "MSS" in security.text
    assert 'id="security-tabs"' in security.text
    assert 'id="main-panel"' in security.text
    assert "Credentials" in security.text
    assert "Status" in security.text
    assert ">Password</button>" not in security.text
    assert ">Sessions</button>" not in security.text
    assert ">Activity</button>" not in security.text
    assert "Loading activity" not in security.text

    account = client.get("/profile")
    assert account.status_code == 200
    assert 'id="account-tabs"' in account.text
    assert 'data-hedron-appearance="underline"' in account.text
    csrf = csrf_from(security.text)

    saved = client.post(
        "/security/secrets/mss",
        data={
            "csrf_token": csrf,
            "endpoint": "https://mss.example",
            "token": MSS_TOKEN,
        },
    )
    assert saved.status_code == 303
    assert "secret-saved" in saved.headers["location"]
    page = client.get(saved.headers["location"])
    assert page.status_code == 200
    assert MSS_TOKEN not in page.text
    assert "ciphertext" not in page.text

    with SessionLocal() as db:
        stored = db.scalar(
            select(UserSecret).where(
                UserSecret.user_id == user.id,
                UserSecret.provider == "mss",
            )
        )
        assert stored is not None
        assert MSS_TOKEN not in stored.ciphertext

    deleted = client.post(
        "/security/secrets/mss/delete",
        data={"csrf_token": csrf_from(page.text)},
    )
    assert deleted.status_code == 303
    assert "secret-deleted" in deleted.headers["location"]
    with SessionLocal() as db:
        remaining = db.scalar(
            select(UserSecret).where(
                UserSecret.user_id == user.id,
                UserSecret.provider == "mss",
            )
        )
        assert remaining is None

    unknown = client.post(
        "/security/secrets/not-a-provider",
        data={"csrf_token": csrf_from(client.get("/security").text), "token": "x"},
    )
    assert unknown.status_code == 422


def test_htmx_admin_directory_and_audit_fragments(client, htmx, make_user) -> None:
    from hedron.testing import assert_fragment_body, assert_html_contains

    make_user("filter.target@example.gov")
    web_login(client)
    for key, value in client.cookies.items():
        htmx.cookies.set(key, value)

    filtered = htmx.get(
        "/admin/users",
        params={"q": "filter.target"},
        headers={"HX-Target": "#user-directory", "Accept": "text/html"},
    )
    adapter = as_adapter(filtered)
    assert filtered.status_code == 200
    assert_fragment_body(adapter, contains="user-directory")
    assert_html_contains(adapter, "filter.target@example.gov")
    assert "<!doctype" not in filtered.text.lower()

    with SessionLocal() as db:
        admin = db.scalar(select(User).where(User.email == ADMIN_EMAIL))
        assert admin is not None
        db.add_all(
            [
                AuditEvent(
                    event_type="fragment.alpha",
                    outcome="success",
                    actor_user_id=admin.id,
                    detail='{"marker":"FRAGMENT_ALPHA"}',
                ),
                AuditEvent(
                    event_type="fragment.beta",
                    outcome="failure",
                    actor_user_id=admin.id,
                    detail='{"marker":"FRAGMENT_BETA"}',
                ),
            ]
        )
        db.commit()

    audit = htmx.get(
        "/admin/audit",
        params={"event_type": "fragment.alpha"},
        headers={"HX-Target": "#audit-results-region", "Accept": "text/html"},
    )
    assert audit.status_code == 200
    assert_fragment_body(as_adapter(audit), contains="audit-results-region")
    assert "FRAGMENT_ALPHA" in audit.text
    assert "FRAGMENT_BETA" not in audit.text
    assert "1 matching event" in audit.text
    assert "<html" not in audit.text.lower()


def test_htmx_admin_invitation_errors_and_missing_toggle(client, htmx) -> None:
    web_login(client, next_path="/admin/users")
    for key, value in client.cookies.items():
        htmx.cookies.set(key, value)
    csrf = csrf_from(htmx.get("/admin/users").text)

    bad_role = htmx.post(
        "/admin/invitations",
        data={"csrf_token": csrf, "email": "htmx.invite@example.gov", "role": "invalid"},
        headers={"HX-Target": "#invitation-panel"},
    )
    assert bad_role.status_code == 400
    assert "role" in bad_role.text.lower()
    assert "<html" not in bad_role.text.lower()

    missing = client.post(
        "/admin/users/00000000-0000-0000-0000-000000000000/toggle",
        data={"csrf_token": csrf_from(client.get("/admin/users").text)},
    )
    assert missing.status_code == 404

    invalid_id = client.post(
        "/admin/users/not-a-uuid/toggle",
        data={"csrf_token": csrf_from(client.get("/admin/users").text)},
    )
    assert invalid_id.status_code == 422


def test_disable_user_invalidates_active_session(client, make_user, access_app) -> None:
    from fastapi.testclient import TestClient

    target = make_user("disabled.session@example.gov")
    victim = TestClient(access_app, follow_redirects=False)
    web_login(victim, target.email, USER_PASSWORD)
    assert victim.get("/profile").status_code == 200

    web_login(client, next_path="/admin/users")
    disabled = client.post(
        f"/admin/users/{target.id}/toggle",
        data={"csrf_token": csrf_from(client.get("/admin/users").text)},
    )
    assert disabled.status_code == 303
    blocked = victim.get("/profile")
    assert blocked.status_code in {302, 303, 401}
    if blocked.status_code in {302, 303}:
        assert "/login" in blocked.headers["location"]
