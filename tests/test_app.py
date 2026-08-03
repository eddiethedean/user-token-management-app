import re
from urllib.parse import parse_qs, urlparse

from fastapi.testclient import TestClient
from sqlalchemy import select
from starlette.responses import Response

from app.config import get_settings
from app.database import SessionLocal
from app.main import app
from app.models import EmailOutbox, Invitation, PasswordReset, RefreshSession, User

ADMIN_EMAIL = "admin@example.gov"
ADMIN_PASSWORD = "River-Lantern-94!Blue"
USER_EMAIL = "analyst@example.gov"
USER_PASSWORD = "Sable-Meadow-83!Cloud"
NEW_PASSWORD = "Copper-Orbit-71!Forest"


class PrefixStrippingProxySimulator:
    def __init__(self, application, prefix: str, origin: str) -> None:
        self.application = application
        self.prefix = prefix.rstrip("/")
        self.base_url = f"{origin.rstrip('/')}{self.prefix}"

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.application(scope, receive, send)
            return

        external_path = scope["path"]
        if external_path == self.prefix:
            internal_path = "/"
        elif external_path.startswith(f"{self.prefix}/"):
            internal_path = external_path[len(self.prefix) :]
        else:
            await Response(status_code=404)(scope, receive, send)
            return

        proxied_scope = dict(scope)
        proxied_scope["path"] = internal_path
        proxied_scope["raw_path"] = internal_path.encode("utf-8")
        self.configure_scope(proxied_scope)
        await self.application(proxied_scope, receive, send)

    def configure_scope(self, scope) -> None:
        raise NotImplementedError


class ConnectProxySimulator(PrefixStrippingProxySimulator):
    """Model Connect's external prefix, path stripping, and base URL header."""

    def configure_scope(self, scope) -> None:
        headers = [
            item for item in scope["headers"] if item[0].lower() != b"rstudio-connect-app-base-url"
        ]
        headers.append((b"rstudio-connect-app-base-url", self.base_url.encode("utf-8")))
        scope["headers"] = headers


class WorkbenchProxySimulator(PrefixStrippingProxySimulator):
    """Model Workbench's external prefix, path stripping, and ASGI root path."""

    def configure_scope(self, scope) -> None:
        scope["root_path"] = self.prefix


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
    assert 'src="assets/htmx.min.js?v=2.0.10"' in login_page.text
    assert "cdn.jsdelivr" not in login_page.text
    assert "unpkg.com" not in login_page.text
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


def test_connect_https_cookie_round_trip_refresh_csrf_and_logout(client) -> None:
    prefix = "/content/2f3d74a6-access-registry"
    origin = "https://connect.example.gov"
    proxy = ConnectProxySimulator(app, prefix, origin)
    settings = get_settings()
    original_cookie_secure = settings.cookie_secure
    settings.cookie_secure = True
    try:
        with TestClient(proxy, base_url=origin, follow_redirects=False) as connect_client:
            signed_in = connect_client.post(
                f"{prefix}/login",
                data={
                    "email": ADMIN_EMAIL,
                    "password": ADMIN_PASSWORD,
                    "next": "/profile",
                },
            )
            assert signed_in.status_code == 303
            assert signed_in.headers["location"] == f"{prefix}/profile"
            auth_cookies = signed_in.headers.get_list("set-cookie")
            assert len(auth_cookies) == 2
            for cookie in auth_cookies:
                assert f"Path={prefix}" in cookie
                assert "HttpOnly" in cookie
                assert "SameSite=lax" in cookie
                assert "Secure" in cookie
                assert "Domain=" not in cookie

            profile = connect_client.get(f"{prefix}/profile")
            assert profile.status_code == 200
            csrf_match = re.search(r'name="csrf_token" value="([^"]+)"', profile.text)
            assert csrf_match is not None

            connect_client.cookies.delete(
                "access_registry_access",
                domain="connect.example.gov",
                path=prefix,
            )
            refreshed = connect_client.get(f"{prefix}/security")
            assert refreshed.status_code == 200
            refreshed_cookies = refreshed.headers.get_list("set-cookie")
            assert len(refreshed_cookies) == 2
            assert all(f"Path={prefix}" in cookie for cookie in refreshed_cookies)

            csrf_match = re.search(r'name="csrf_token" value="([^"]+)"', refreshed.text)
            assert csrf_match is not None
            signed_out = connect_client.post(
                f"{prefix}/logout",
                data={"csrf_token": csrf_match.group(1)},
            )
            assert signed_out.status_code == 303
            assert signed_out.headers["location"] == f"{prefix}/login"
            deleted_cookies = signed_out.headers.get_list("set-cookie")
            assert len(deleted_cookies) == 2
            assert all(f"Path={prefix}" in cookie for cookie in deleted_cookies)
            assert all("Max-Age=0" in cookie for cookie in deleted_cookies)
            assert (
                connect_client.cookies.get(
                    "access_registry_access",
                    domain="connect.example.gov",
                    path=prefix,
                )
                is None
            )
            assert (
                connect_client.cookies.get(
                    "access_registry_refresh",
                    domain="connect.example.gov",
                    path=prefix,
                )
                is None
            )
    finally:
        settings.cookie_secure = original_cookie_secure


def test_workbench_https_proxy_cookie_htmx_refresh_and_logout(client) -> None:
    prefix = "/s/3a91f7c2/session/p/8000"
    origin = "https://workbench.example.gov"
    proxy = WorkbenchProxySimulator(app, prefix, origin)
    settings = get_settings()
    original_cookie_secure = settings.cookie_secure
    settings.cookie_secure = True
    try:
        with TestClient(proxy, base_url=origin, follow_redirects=False) as workbench_client:
            login_page = workbench_client.get(f"{prefix}/login")
            assert login_page.status_code == 200
            assert f'<base href="{prefix}/">' in login_page.text
            assert workbench_client.get(f"{prefix}/assets/app.css").status_code == 200
            assert workbench_client.get(f"{prefix}/assets/htmx.min.js").status_code == 200

            signed_in = workbench_client.post(
                f"{prefix}/login",
                data={
                    "email": ADMIN_EMAIL,
                    "password": ADMIN_PASSWORD,
                    "next": "/profile",
                },
            )
            assert signed_in.status_code == 303
            assert signed_in.headers["location"] == f"{prefix}/profile"
            auth_cookies = signed_in.headers.get_list("set-cookie")
            assert len(auth_cookies) == 2
            for cookie in auth_cookies:
                assert f"Path={prefix}" in cookie
                assert "HttpOnly" in cookie
                assert "SameSite=lax" in cookie
                assert "Secure" in cookie
                assert "Domain=" not in cookie

            profile = workbench_client.get(f"{prefix}/profile")
            assert profile.status_code == 200
            csrf_match = re.search(r'name="csrf_token" value="([^"]+)"', profile.text)
            assert csrf_match is not None
            updated = workbench_client.post(
                f"{prefix}/profile",
                data={
                    "csrf_token": csrf_match.group(1),
                    "full_name": "Workbench Administrator",
                    "organization": "Development Environment",
                    "job_title": "Developer",
                    "phone": "",
                },
                headers={"HX-Request": "true"},
            )
            assert updated.status_code == 200
            assert "Your profile has been updated" in updated.text

            workbench_client.cookies.delete(
                "access_registry_access",
                domain="workbench.example.gov",
                path=prefix,
            )
            refreshed = workbench_client.get(f"{prefix}/security")
            assert refreshed.status_code == 200
            refreshed_cookies = refreshed.headers.get_list("set-cookie")
            assert len(refreshed_cookies) == 2
            assert all(f"Path={prefix}" in cookie for cookie in refreshed_cookies)

            csrf_match = re.search(r'name="csrf_token" value="([^"]+)"', refreshed.text)
            assert csrf_match is not None
            signed_out = workbench_client.post(
                f"{prefix}/logout",
                data={"csrf_token": csrf_match.group(1)},
            )
            assert signed_out.status_code == 303
            assert signed_out.headers["location"] == f"{prefix}/login"
            deleted_cookies = signed_out.headers.get_list("set-cookie")
            assert len(deleted_cookies) == 2
            assert all(f"Path={prefix}" in cookie for cookie in deleted_cookies)
            assert all("Max-Age=0" in cookie for cookie in deleted_cookies)
            assert (
                workbench_client.cookies.get(
                    "access_registry_access",
                    domain="workbench.example.gov",
                    path=prefix,
                )
                is None
            )
            assert (
                workbench_client.cookies.get(
                    "access_registry_refresh",
                    domain="workbench.example.gov",
                    path=prefix,
                )
                is None
            )
    finally:
        settings.cookie_secure = original_cookie_secure


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
