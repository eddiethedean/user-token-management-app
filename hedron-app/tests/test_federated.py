"""Federated (trusted-header) web authentication for Hedron UI."""

from __future__ import annotations

from fastapi.testclient import TestClient
from hedron.testing import assert_html_contains, assert_page_document, fastapi_fixture
from sqlalchemy import select

from app.config import get_settings
from app.database import SessionLocal
from app.models import User
from app.services.auth import create_invitation
from tests.helpers import ADMIN_EMAIL, as_adapter, login_csrf_from


def test_federated_login_page_and_proxy_allowlist(hedron_app) -> None:
    settings = get_settings()
    original_mode = settings.authentication_mode
    original_proxies = settings.trusted_proxy_ips
    try:
        settings.authentication_mode = "trusted_header"
        settings.trusted_proxy_ips = "127.0.0.1"
        with TestClient(
            hedron_app, follow_redirects=False, client=("127.0.0.1", 50000)
        ) as client:
            login_page = client.get("/login")
            adapter = as_adapter(login_page)
            assert_page_document(adapter)
            assert_html_contains(adapter, "Continue with federated sign-in")
            assert 'name="password"' not in login_page.text

            csrf = login_csrf_from(login_page.text)
            signed_in = client.post(
                "/login/federated",
                data={"next": "/profile", "preauth_csrf_token": csrf},
                headers={settings.trusted_identity_header: ADMIN_EMAIL},
            )
            assert signed_in.status_code == 303
            assert "/profile" in signed_in.headers["location"]

            client.cookies.clear()
            missing_csrf = client.post(
                "/login/federated",
                data={"next": "/profile"},
                headers={settings.trusted_identity_header: ADMIN_EMAIL},
            )
            assert missing_csrf.status_code == 403

            assert client.get("/password/forgot").status_code == 404

            csrf = login_csrf_from(client.get("/login").text)
            client.post(
                "/login/federated",
                data={"next": "/security", "preauth_csrf_token": csrf},
                headers={settings.trusted_identity_header: ADMIN_EMAIL},
            )
            security = client.get("/security")
            assert security.status_code == 200
            assert "Change password" not in security.text

        with TestClient(
            hedron_app, follow_redirects=False, client=("198.51.100.25", 50000)
        ) as spoofed:
            csrf = login_csrf_from(spoofed.get("/login").text)
            denied = spoofed.post(
                "/login/federated",
                data={"next": "/profile", "preauth_csrf_token": csrf},
                headers={settings.trusted_identity_header: ADMIN_EMAIL},
            )
            assert denied.status_code in {401, 403}
    finally:
        settings.authentication_mode = original_mode
        settings.trusted_proxy_ips = original_proxies


def test_federated_invitation_activation_skips_password(hedron_app) -> None:
    settings = get_settings()
    original_mode = settings.authentication_mode
    email = "federated.invitee@example.gov"
    try:
        settings.authentication_mode = "trusted_header"
        with SessionLocal() as db:
            administrator = db.scalar(select(User).where(User.email == ADMIN_EMAIL))
            assert administrator is not None
            _, raw_token = create_invitation(
                db,
                settings,
                email=email,
                role_name="user",
                inviter=administrator,
            )

        fixture = fastapi_fixture(hedron_app)
        page = fixture.get(f"/invitations/accept?token={raw_token}")
        assert_page_document(page)
        assert 'name="password"' not in page.body

        with TestClient(hedron_app, follow_redirects=False) as client:
            accepted = client.post(
                "/invitations/accept",
                data={"token": raw_token, "full_name": "Federated Invitee"},
            )
            assert accepted.status_code == 303
        with SessionLocal() as db:
            user = db.scalar(select(User).where(User.email == email))
            assert user is not None and user.password_hash is None
    finally:
        settings.authentication_mode = original_mode


def test_federated_registration_verification_skips_password(client) -> None:
    settings = get_settings()
    original_mode = settings.authentication_mode
    email = "federated.reg@example.gov"
    try:
        settings.authentication_mode = "trusted_header"
        assert (
            client.post(
                "/register",
                data={"email": email, "full_name": "Federated Reg"},
            ).status_code
            == 202
        )
        from tests.helpers import latest_email_token

        token = latest_email_token(email, subject_like="Verify your%registration")
        page = client.get(f"/registration/verify?token={token}")
        assert page.status_code == 200
        assert 'name="password"' not in page.text
        verified = client.post(
            "/registration/verify",
            data={"token": token},
        )
        assert verified.status_code == 200
        with SessionLocal() as db:
            user = db.scalar(select(User).where(User.email == email))
            assert user is not None
            assert user.password_hash is None
            assert user.email_verified_at is not None
    finally:
        settings.authentication_mode = original_mode
