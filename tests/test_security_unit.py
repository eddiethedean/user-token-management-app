"""Unit security coverage for Hedron Access Registry (no HTTP)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import jwt
import pytest
from fastapi import Request

from app.config import Settings
from app.models import Role, User
from app.routing import (
    app_base_url,
    app_path,
    cookie_path,
    normalize_workbench_scope,
    safe_base_path,
)
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


def request_with_client(host: str, *, forwarded: str = "") -> Request:
    headers = []
    if forwarded:
        headers.append((b"x-forwarded-for", forwarded.encode()))
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


@pytest.mark.parametrize(
    "value",
    [
        "relative/path",
        "//external.example/path",
        "/path\\escape",
        "/path?query=yes",
        "/path#fragment",
        "/path\nheader",
        "ftp://example.gov/path",
    ],
)
def test_proxy_base_path_rejects_unsafe_values(value: str) -> None:
    assert safe_base_path(value, allow_absolute_url=True) == ""


def test_workbench_scope_strips_root_path_when_uvicorn_includes_it_in_path() -> None:
    scope = {
        "type": "http",
        "path": "/s/session/p/8000/profile",
        "raw_path": b"/s/session/p/8000/profile",
        "root_path": "/s/session/p/8000",
        "query_string": b"",
    }
    normalized = normalize_workbench_scope(scope)
    assert normalized["path"] == "/profile"
    assert normalized["raw_path"] == b"/profile"
    assert normalized["root_path"] == "/s/session/p/8000"


def test_workbench_scope_discovers_session_mount_from_path_without_root_path() -> None:
    scope = {
        "type": "http",
        "path": "/s/e886e3c9ab5a7de8990d1/p/679ea2ac/",
        "raw_path": b"/s/e886e3c9ab5a7de8990d1/p/679ea2ac/",
        "root_path": "",
        "query_string": b"",
    }
    normalized = normalize_workbench_scope(scope)
    assert normalized["path"] == "/"
    assert normalized["root_path"] == "/s/e886e3c9ab5a7de8990d1/p/679ea2ac"


def test_workbench_scope_discovers_proxy_mount_from_path_without_root_path() -> None:
    scope = {
        "type": "http",
        "path": "/proxy/8000/login",
        "raw_path": b"/proxy/8000/login",
        "root_path": "",
        "query_string": b"",
    }
    normalized = normalize_workbench_scope(scope)
    assert normalized["path"] == "/login"
    assert normalized["root_path"] == "/proxy/8000"


def test_workbench_scope_infers_proxy_root_when_prefix_was_stripped(monkeypatch) -> None:
    monkeypatch.setenv("UVICORN_ROOT_PATH", "https://workbench.socom.mil/s/session/p/679ea2ac/")
    monkeypatch.setenv("PORT", "8000")
    scope = {
        "type": "http",
        "path": "/",
        "raw_path": b"/",
        "root_path": "",
        "query_string": b"",
    }
    normalized = normalize_workbench_scope(scope)
    assert normalized["path"] == "/"
    assert normalized["root_path"] == "/proxy/8000"

    from starlette.requests import Request

    from app.routing import app_path

    assert app_path(Request(normalized), "/login") == "/proxy/8000/login"


def test_workbench_scope_prefers_session_path_over_inferred_proxy(monkeypatch) -> None:
    monkeypatch.setenv("UVICORN_ROOT_PATH", "https://workbench.socom.mil/s/session/p/679ea2ac/")
    monkeypatch.setenv("PORT", "8000")
    scope = {
        "type": "http",
        "path": "/s/session/p/679ea2ac/",
        "raw_path": b"/s/session/p/679ea2ac/",
        "root_path": "",
        "query_string": b"",
    }
    normalized = normalize_workbench_scope(scope)
    assert normalized["path"] == "/"
    assert normalized["root_path"] == "/s/session/p/679ea2ac"


def test_forwarded_source_is_used_only_for_an_explicitly_trusted_proxy() -> None:
    untrusted = settings(trusted_proxy_ips="")
    trusted = settings(trusted_proxy_ips="10.0.0.10")
    request = request_with_client("10.0.0.10", forwarded="192.0.2.44, 10.0.0.10")
    assert client_ip(request, untrusted) == "10.0.0.10"
    assert client_ip(request, trusted) == "192.0.2.44"

    malformed = request_with_client("10.0.0.10", forwarded="not-an-ip")
    assert client_ip(malformed, trusted) == "10.0.0.10"


def test_app_path_and_cookie_path_defaults() -> None:
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
        }
    )
    assert app_base_url(bare) == ""
    assert app_path(bare, "profile") == "/profile"
    assert cookie_path(bare, "auto") == "/"
    assert cookie_path(bare, "/explicit") == "/explicit"
