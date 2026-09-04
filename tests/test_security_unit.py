"""Unit security coverage for Hedron Data Mover (no HTTP)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import jwt
import pytest
from fastapi import Request
from hedron_posit import browser_mount_from_request, local_href

from app.config import Settings
from app.models import Role, User
from app.security.client import client_ip
from app.security.csrf import issue_preauth_csrf, validate_preauth_csrf
from app.security.email import EmailPolicyError, normalize_email
from app.security.passwords import PasswordPolicyError, PasswordService, validate_password
from app.security.tokens import (
    AccessTokenError,
    create_access_token,
    decode_access_token,
    hash_token,
    random_token,
)

HEDRON_APP = SimpleNamespace(
    state=SimpleNamespace(hedron_mount_path="", hedron_mount_was_configured=False)
)


def settings(**updates) -> Settings:
    values = {
        "app_env": "test",
        "allowed_email_domains": "example.gov, Example.MIL",
        "jwt_secret": "unit-test-jwt-secret-that-is-at-least-thirty-two-bytes",
        "session_pepper": "unit-test-pepper-that-is-at-least-thirty-two-bytes",
        "csrf_secret": "unit-test-csrf-secret-that-is-at-least-thirty-two-bytes",
        "password_hash_scheme": "pbkdf2_sha256",
        "pbkdf2_iterations": 100_000,
        "trusted_proxy_ips": "10.0.0.10",
    }
    values.update(updates)
    return Settings(_env_file=None, **values)


def request_with_client(host: str, *, forwarded: str = "", connect_base: str = "") -> Request:
    headers = []
    if forwarded:
        headers.append((b"x-forwarded-for", forwarded.encode()))
    if connect_base:
        headers.append((b"rstudio-connect-app-base-url", connect_base.encode()))
    return Request(
        {
            "type": "http",
            "method": "POST",
            "scheme": "https",
            "path": "/login",
            "raw_path": b"/login",
            "root_path": "",
            "query_string": b"",
            "headers": headers,
            "client": (host, 12345),
            "server": ("registry.example.gov", 443),
        }
    )


def test_email_normalization_and_allowlist() -> None:
    canonical, original = normalize_email("  Analyst@Example.GOV ", settings())
    assert canonical == "analyst@example.gov"
    assert original == "Analyst@example.gov"
    assert settings().email_domain_allowlist == {"example.gov", "example.mil"}


@pytest.mark.parametrize("email", ["not-an-email", "analyst@outside.test"])
def test_email_policy_rejects_invalid_or_unapproved_addresses(email: str) -> None:
    with pytest.raises(EmailPolicyError):
        normalize_email(email, settings())


@pytest.mark.parametrize(
    ("password", "message"),
    [
        ("too-short", "at least 15"),
        ("x" * 129, "no more than 128"),
        ("passwordpassword", "not common"),
        ("analyst-has-a-long-secret", "not common"),
    ],
)
def test_password_policy_rejects_weak_values(password: str, message: str) -> None:
    with pytest.raises(PasswordPolicyError, match=message):
        validate_password(password, email="analyst@example.gov")


def test_password_policy_normalizes_unicode() -> None:
    raw = "Long-River-Path-e\u0301"
    validated = validate_password(raw)
    assert validated == "Long-River-Path-é"


def test_password_policy_uses_configured_offline_blocklist() -> None:
    blocklist = Path(__file__).parent / "fixtures" / "password-blocklist.txt"
    with pytest.raises(PasswordPolicyError, match="not common"):
        validate_password(
            "blocked-organization-passphrase",
            blocklist_path=str(blocklist),
        )


def test_password_verification_normalizes_unicode() -> None:
    service = PasswordService(settings())
    encoded = service.hash("Long-River-Path-e\u0301")
    assert service.verify("Long-River-Path-é", encoded)


def test_pbkdf2_hashing_verification_and_rehash_detection() -> None:
    service = PasswordService(settings())
    first = service.hash("Granite-Trail-54!North")
    second = service.hash("Granite-Trail-54!North")
    assert first != second
    assert service.verify("Granite-Trail-54!North", first)
    assert not service.verify("wrong-password", first)
    assert not service.verify("anything", None)
    assert not service.verify("anything", "pbkdf2_sha256$broken")
    assert not service.verify("anything", "unknown$hash")
    assert not service.needs_rehash(first)
    assert service.needs_rehash(first.replace("100000", "99999", 1))
    assert service.needs_rehash("pbkdf2_sha256$invalid$salt$digest")
    assert service.needs_rehash("$argon2id$foreign")


def test_argon2_hashing_and_cross_scheme_rehash() -> None:
    argon = PasswordService(settings(password_hash_scheme="argon2"))
    encoded = argon.hash("Granite-Trail-54!North")
    assert encoded.startswith("$argon2")
    assert argon.verify("Granite-Trail-54!North", encoded)
    assert not argon.verify("wrong-password", encoded)
    assert not argon.needs_rehash(encoded)
    pbkdf2 = PasswordService(settings()).hash("Granite-Trail-54!North")
    assert argon.needs_rehash(pbkdf2)


def test_random_and_hashed_tokens_are_non_reversible_and_pepper_bound() -> None:
    first = random_token()
    second = random_token()
    assert first != second
    assert len(first) >= 40
    assert hash_token(first, "pepper-one") != hash_token(first, "pepper-two")
    assert first not in hash_token(first, "pepper-one")


def test_preauth_csrf_is_signed_bound_and_short_lived() -> None:
    token_settings = settings()
    token = issue_preauth_csrf(token_settings, issued_at=1_000)
    assert validate_preauth_csrf(token, token, token_settings, now=1_001)
    assert not validate_preauth_csrf(f"{token}x", token, token_settings, now=1_001)
    assert not validate_preauth_csrf(token, token, settings(csrf_secret="x" * 32), now=1_001)
    assert not validate_preauth_csrf(token, token, token_settings, now=4_601)


def test_jwt_round_trip_and_required_claims() -> None:
    user = User(id="user-id", security_version=7, roles=[Role(name="user")])
    token, expires_in = create_access_token(user, "session-id", settings())
    payload = decode_access_token(token, settings())
    assert expires_in == 600
    assert payload["sub"] == "user-id"
    assert payload["sid"] == "session-id"
    assert payload["sv"] == 7
    assert payload["roles"] == ["user"]
    assert payload["jti"]


def test_jwt_rejects_tampering_wrong_audience_and_malformed_subject() -> None:
    token_settings = settings()
    user = User(id="user-id", roles=[])
    token, _ = create_access_token(user, "session-id", token_settings)
    with pytest.raises(AccessTokenError):
        decode_access_token(f"{token}x", token_settings)
    with pytest.raises(AccessTokenError):
        decode_access_token(token, settings(jwt_audience="different-audience"))

    now = datetime.now(UTC)
    malformed = jwt.encode(
        {
            "iss": token_settings.jwt_issuer,
            "aud": token_settings.jwt_audience,
            "sub": "user-id",
            "sid": 123,
            "jti": "token-id",
            "iat": now,
            "nbf": now,
            "exp": now + timedelta(minutes=1),
        },
        token_settings.jwt_secret,
        algorithm="HS256",
    )
    with pytest.raises(AccessTokenError, match="Malformed"):
        decode_access_token(malformed, token_settings)


def test_hedron_posit_mount_drives_app_paths_and_root_cookie_emission() -> None:
    mount = "/s/e886e3c9ab5a7de8990d1/p/679ea2ac"
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "scheme": "https",
            "server": ("workbench.socom.mil", 443),
            "path": f"{mount}/profile",
            "raw_path": f"{mount}/profile".encode(),
            "root_path": mount,
            "query_string": b"",
            "headers": [],
            "client": ("127.0.0.1", 1),
            "app": HEDRON_APP,
        }
    )

    assert browser_mount_from_request(request) == mount
    assert local_href("/login", mount=browser_mount_from_request(request)) == f"{mount}/login"
    assert settings().cookie_path == "auto"


def test_application_cookies_use_hedron_posit_mount_registry() -> None:
    from hedron_posit import CookieRegistry, CookieSpec
    from starlette.responses import Response

    from app.security.cookies import set_application_cookie

    mount = "/s/session/p/8765"
    registry_app = SimpleNamespace(
        state=SimpleNamespace(hedron_mount_path=mount),
        _owned_cookie_names=lambda: (),
    )
    cookies = CookieRegistry(registry_app)
    cookies.register(CookieSpec("data-mover-test", secure=False))
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "scheme": "https",
            "server": ("workbench.example", 443),
            "path": f"{mount}/profile",
            "raw_path": f"{mount}/profile".encode(),
            "root_path": mount,
            "query_string": b"",
            "headers": [],
            "client": ("127.0.0.1", 1),
            "app": SimpleNamespace(cookies=cookies),
        }
    )
    response = Response()
    set_application_cookie(
        response,
        request,
        settings(cookie_secure=False),
        "data-mover-test",
        "enabled",
        max_age=60,
    )

    assert response.headers["set-cookie"] == (
        "data-mover-test=enabled; HttpOnly; Max-Age=60; Path=/s/session/p/8765; SameSite=lax"
    )


def test_application_cookies_fall_back_without_an_asgi_app() -> None:
    from starlette.responses import Response

    from app.security.cookies import delete_application_cookie, set_application_cookie

    request = Request(
        {
            "type": "http",
            "method": "GET",
            "scheme": "http",
            "server": ("testserver", 80),
            "path": "/",
            "raw_path": b"/",
            "root_path": "",
            "query_string": b"",
            "headers": [],
            "client": ("127.0.0.1", 1),
        }
    )
    response = Response()
    test_settings = settings(cookie_secure=False)

    set_application_cookie(response, request, test_settings, "data-mover-test", "enabled")
    delete_application_cookie(response, request, test_settings, "data-mover-test")

    assert response.headers["set-cookie"].startswith("data-mover-test=enabled;")
    deleted = response.raw_headers[-1][1].decode()
    assert deleted.startswith('data-mover-test=""; expires=')
    assert "Max-Age=0" in deleted
    assert "Path=/" in deleted
    assert "SameSite=lax" in deleted


def test_forwarded_source_is_used_only_for_an_explicitly_trusted_proxy() -> None:
    untrusted = settings(trusted_proxy_ips="")
    trusted = settings(trusted_proxy_ips="10.0.0.10")
    request = request_with_client("10.0.0.10", forwarded="192.0.2.44, 10.0.0.10")
    assert client_ip(request, untrusted) == "10.0.0.10"
    assert client_ip(request, trusted) == "192.0.2.44"

    malformed = request_with_client("10.0.0.10", forwarded="not-an-ip")
    assert client_ip(malformed, trusted) == "10.0.0.10"


def test_connect_mount_comes_from_asgi_scope_and_cookies_remain_root_scoped() -> None:
    mount = "/content/access-registry"
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "scheme": "https",
            "server": ("connect.example.gov", 443),
            "path": f"{mount}/login",
            "raw_path": f"{mount}/login".encode(),
            "root_path": mount,
            "query_string": b"",
            "headers": [
                (
                    b"rstudio-connect-app-base-url",
                    b"https://connect.example.gov/content/access-registry/",
                )
            ],
            "client": ("127.0.0.1", 1),
            "app": HEDRON_APP,
        }
    )

    assert browser_mount_from_request(request) == mount
    assert local_href("/login", mount=browser_mount_from_request(request)) == f"{mount}/login"
    assert settings().cookie_path == "auto"


def test_hedron_posit_path_and_cookie_path_defaults(monkeypatch) -> None:
    monkeypatch.delenv("POSIT_PRODUCT", raising=False)
    monkeypatch.delenv("UVICORN_ROOT_PATH", raising=False)
    monkeypatch.delenv("RS_SERVER_URL", raising=False)
    bare = Request(
        {
            "type": "http",
            "method": "GET",
            "scheme": "https",
            "server": ("example.gov", 443),
            "path": "/profile",
            "root_path": "",
            "query_string": b"",
            "headers": [],
            "client": ("127.0.0.1", 50000),
            "app": HEDRON_APP,
        }
    )
    assert browser_mount_from_request(bare) == ""
    assert local_href("/profile", mount=browser_mount_from_request(bare)) == "/profile"
    assert settings().cookie_path == "auto"
    assert settings(cookie_path="/explicit").cookie_path == "/explicit"
