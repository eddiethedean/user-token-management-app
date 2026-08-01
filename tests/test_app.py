import re
from urllib.parse import parse_qs, urlparse

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.database import SessionLocal
from app.main import app
from app.models import EmailOutbox, Invitation, PasswordReset, RefreshSession, User

ADMIN_EMAIL = "admin@example.gov"
ADMIN_PASSWORD = "River-Lantern-94!Blue"
USER_EMAIL = "analyst@example.gov"
USER_PASSWORD = "Sable-Meadow-83!Cloud"
NEW_PASSWORD = "Copper-Orbit-71!Forest"


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def login(client, email: str, password: str) -> str:
    response = client.post("/api/v1/auth/token", json={"email": email, "password": password})
    assert response.status_code == 200, response.text
    assert response.json()["token_type"] == "Bearer"
    return response.json()["access_token"]


def latest_email_token(recipient: str, path: str) -> str:
    with SessionLocal() as db:
        message = db.scalar(
            select(EmailOutbox)
            .where(EmailOutbox.recipient == recipient)
            .order_by(EmailOutbox.created_at.desc())
        )
        assert message is not None
        match = re.search(r"https://[^\s]+", message.body_text)
        assert match is not None
        parsed = urlparse(match.group(0))
        assert parsed.path == path
        return parse_qs(parsed.query)["token"][0]


def invite_and_accept(client) -> tuple[str, str]:
    admin_token = login(client, ADMIN_EMAIL, ADMIN_PASSWORD)
    invited = client.post(
        "/api/v1/admin/invitations",
        json={"email": USER_EMAIL, "role": "user"},
        headers=bearer(admin_token),
    )
    assert invited.status_code == 201, invited.text
    raw_token = latest_email_token(USER_EMAIL, "/invitations/accept")

    viewed = client.get(f"/invitations/accept?token={raw_token}")
    assert viewed.status_code == 200
    with SessionLocal() as db:
        invitation = db.scalar(select(Invitation).where(Invitation.email == USER_EMAIL))
        assert invitation is not None
        assert invitation.accepted_at is None

    accepted = client.post(
        "/invitations/accept",
        data={
            "token": raw_token,
            "full_name": "Avery Analyst",
            "password": USER_PASSWORD,
            "password_confirm": USER_PASSWORD,
        },
    )
    assert accepted.status_code == 303
    assert accepted.headers["location"] == "/login?invitation=accepted"
    return admin_token, login(client, USER_EMAIL, USER_PASSWORD)


def test_pages_assets_and_connect_mount_path(client) -> None:
    assert client.get("/health").json() == {"status": "ok"}
    login_page = client.get("/login")
    assert login_page.status_code == 200
    assert "htmx.min.js" in login_page.text
    assert client.get("/assets/app.css").status_code == 200
    assert client.get("/assets/htmx.min.js").status_code == 200
    assert "frame-ancestors 'none'" in login_page.headers["content-security-policy"]

    mounted = client.get(
        "/login", headers={"rstudio-connect-app-base-url": "/content/access-registry"}
    )
    assert '<base href="/content/access-registry/">' in mounted.text
    protected = client.get(
        "/profile",
        headers={
            "Accept": "text/html",
            "rstudio-connect-app-base-url": "/content/access-registry",
        },
    )
    assert protected.status_code == 303
    assert protected.headers["location"].startswith(
        "/content/access-registry/login?next=%2Fprofile"
    )


def test_workbench_root_path_routes_without_code_changes(client) -> None:
    root_path = "/s/7f42/session/p/8000"
    with TestClient(app, root_path=root_path, follow_redirects=False) as workbench_client:
        login_page = workbench_client.get("/login")
        assert login_page.status_code == 200
        assert f'<base href="{root_path}/">' in login_page.text
        assert workbench_client.get("/assets/app.css").status_code == 200

        home = workbench_client.get("/")
        assert home.status_code == 303
        assert home.headers["location"] == f"{root_path}/login"

        protected = workbench_client.get("/profile", headers={"Accept": "text/html"})
        assert protected.status_code == 303
        assert protected.headers["location"].startswith(f"{root_path}/login?next=%2Fprofile")

        signed_in = workbench_client.post(
            "/login",
            data={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD, "next": "/profile"},
        )
        assert signed_in.status_code == 303
        assert signed_in.headers["location"] == f"{root_path}/profile"
        set_cookie = signed_in.headers.get_list("set-cookie")
        assert set_cookie
        assert all(f"Path={root_path}" in cookie for cookie in set_cookie)


def test_invalid_connect_base_header_cannot_create_external_redirect(client) -> None:
    response = client.get(
        "/profile",
        headers={
            "Accept": "text/html",
            "rstudio-connect-app-base-url": "https://attacker.example/redirect",
        },
    )
    assert response.status_code == 303
    assert response.headers["location"].startswith("/redirect/login?")
    assert "attacker.example" not in response.headers["location"]


def test_absolute_connect_base_url_is_reduced_to_its_mount_path(client) -> None:
    response = client.get(
        "/login",
        headers={
            "rstudio-connect-app-base-url": ("https://connect.example.gov/content/access-registry/")
        },
    )
    assert response.status_code == 200
    assert '<base href="/content/access-registry/">' in response.text


def test_login_next_rejects_browser_normalized_external_paths(client) -> None:
    response = client.post(
        "/login",
        data={
            "email": ADMIN_EMAIL,
            "password": ADMIN_PASSWORD,
            "next": "/\\attacker.example",
        },
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/profile"


def test_invitation_jwt_profile_and_session_flow(client) -> None:
    _, user_token = invite_and_accept(client)

    me = client.get("/api/v1/me", headers=bearer(user_token))
    assert me.status_code == 200
    assert me.json()["email"] == USER_EMAIL
    assert me.json()["roles"] == ["user"]

    updated = client.patch(
        "/api/v1/me",
        json={
            "full_name": "Avery Analyst",
            "organization": "Program Office",
            "job_title": "Data Analyst",
            "phone": "555-0100",
        },
        headers=bearer(user_token),
    )
    assert updated.status_code == 200
    assert updated.json()["organization"] == "Program Office"

    sessions = client.get("/api/v1/me/sessions", headers=bearer(user_token))
    assert sessions.status_code == 200
    assert len(sessions.json()) == 1
    assert sessions.json()[0]["current"] is True


def test_browser_login_refresh_and_htmx_profile_flow(client) -> None:
    invite_and_accept(client)
    client.cookies.clear()

    signed_in = client.post(
        "/login",
        data={"email": USER_EMAIL, "password": USER_PASSWORD, "next": "/profile"},
    )
    assert signed_in.status_code == 303
    assert signed_in.headers["location"] == "/profile"
    assert "access_registry_access" in client.cookies
    assert "access_registry_refresh" in client.cookies

    client.cookies.delete("access_registry_access")
    profile = client.get("/profile")
    assert profile.status_code == 200
    assert "access_registry_access" in client.cookies
    csrf_match = re.search(r'name="csrf_token" value="([^"]+)"', profile.text)
    assert csrf_match is not None

    updated = client.post(
        "/profile",
        data={
            "csrf_token": csrf_match.group(1),
            "full_name": "Avery A. Analyst",
            "organization": "Mission Analytics",
            "job_title": "Senior Analyst",
            "phone": "555-0101",
        },
        headers={"HX-Request": "true"},
    )
    assert updated.status_code == 200
    assert "Your profile has been updated" in updated.text
    assert "Mission Analytics" in updated.text

    rejected = client.post(
        "/profile",
        data={"csrf_token": "invalid", "full_name": "Attacker"},
        headers={"HX-Request": "true", "Accept": "text/html"},
    )
    assert rejected.status_code == 403


def test_password_reset_is_single_use_and_revokes_sessions(client) -> None:
    _, old_access_token = invite_and_accept(client)
    requested = client.post("/api/v1/auth/forgot-password", json={"email": USER_EMAIL})
    assert requested.status_code == 202
    raw_token = latest_email_token(USER_EMAIL, "/password/reset")

    viewed = client.get(f"/password/reset?token={raw_token}")
    assert viewed.status_code == 200
    with SessionLocal() as db:
        reset = db.scalar(select(PasswordReset))
        assert reset is not None
        assert reset.used_at is None

    completed = client.post(
        "/api/v1/auth/reset-password",
        json={"token": raw_token, "new_password": NEW_PASSWORD},
    )
    assert completed.status_code == 204
    assert client.get("/api/v1/me", headers=bearer(old_access_token)).status_code == 401
    assert login(client, USER_EMAIL, NEW_PASSWORD)

    replayed = client.post(
        "/api/v1/auth/reset-password",
        json={"token": raw_token, "new_password": USER_PASSWORD},
    )
    assert replayed.status_code == 400
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.email == USER_EMAIL))
        assert user is not None
        revoked = db.scalars(
            select(RefreshSession).where(
                RefreshSession.user_id == user.id, RefreshSession.revoked_at.is_not(None)
            )
        ).all()
        assert revoked


def test_disabling_user_immediately_invalidates_access_token(client) -> None:
    admin_token, user_token = invite_and_accept(client)
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.email == USER_EMAIL))
        assert user is not None
        user_id = user.id

    disabled = client.patch(
        f"/api/v1/admin/users/{user_id}",
        json={"status": "disabled"},
        headers=bearer(admin_token),
    )
    assert disabled.status_code == 200
    assert disabled.json()["status"] == "disabled"
    assert client.get("/api/v1/me", headers=bearer(user_token)).status_code == 401
