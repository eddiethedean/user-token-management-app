from datetime import UTC, datetime, timedelta

import jwt
import pytest
from fastapi import Request
from pydantic import ValidationError

from app.config import Settings
from app.models import Role, User
from app.routing import (
    _safe_base_path,
    app_base_url,
    app_path,
    cookie_path,
    normalize_workbench_scope,
)
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
    }
    values.update(updates)
    return Settings(_env_file=None, **values)


def request(*, root_path: str = "", connect_base: str = "") -> Request:
    headers = []
    if connect_base:
        headers.append((b"rstudio-connect-app-base-url", connect_base.encode()))
    return Request(
        {
            "type": "http",
            "method": "GET",
            "scheme": "https",
            "server": ("example.gov", 443),
            "path": "/profile",
            "root_path": root_path,
            "query_string": b"",
            "headers": headers,
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
    assert _safe_base_path(value, allow_absolute_url=True) == ""


def test_proxy_routing_precedence_and_cookie_override() -> None:
    connect_request = request(
        root_path="/s/session/p/8000",
        connect_base="https://connect.example.gov/content/app/",
    )
    assert app_base_url(connect_request) == "/content/app"
    assert app_path(connect_request, "profile") == "/content/app/profile"
    assert cookie_path(connect_request, "auto") == "/content/app"
    assert cookie_path(connect_request, "/explicit") == "/explicit"
    assert cookie_path(request(), "auto") == "/"


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


def test_workbench_scope_removes_internal_proxy_port_prefix() -> None:
    scope = {
        "type": "http",
        "path": "/s/session/p/8000/security",
        "raw_path": b"/s/session/p/8000/security",
        "root_path": "/proxy/49152/s/session/p/8000",
        "query_string": b"",
    }
    normalized = normalize_workbench_scope(scope)
    assert normalized["path"] == "/security"
    assert normalized["root_path"] == "/s/session/p/8000"


def test_workbench_scope_decodes_absolute_url_path_and_preserves_query() -> None:
    scope = {
        "type": "http",
        "path": "/https%3A%2F%2Fworkbench.example.gov%2Fs%2Fsession%2Fp%2F8000%2Flogin%3Fnext%3D%252Fsecurity",
        "raw_path": b"",
        "root_path": "/s/session/p/8000",
        "query_string": b"",
    }
    normalized = normalize_workbench_scope(scope)
    assert normalized["path"] == "/login"
    assert normalized["root_path"] == "/s/session/p/8000"
    assert normalized["query_string"] == b"next=%2Fsecurity"


def test_workbench_scope_does_not_decode_absolute_paths_without_mount_context() -> None:
    scope = {
        "type": "http",
        "path": "/https%3A%2F%2Fattacker.example%2Flogin",
        "raw_path": b"",
        "root_path": "",
        "query_string": b"",
    }
    assert normalize_workbench_scope(scope) is scope


def production_values(**updates) -> dict:
    values = {
        "app_env": "production",
        "jwt_secret": "production-jwt-secret-that-is-at-least-thirty-two-bytes",
        "session_pepper": "production-pepper-that-is-at-least-thirty-two-bytes",
        "csrf_secret": "production-csrf-secret-that-is-at-least-thirty-two-bytes",
        "api_token_encryption_keys": {
            "production-v1": "AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8="
        },
        "api_token_active_key_id": "production-v1",
        "cookie_secure": True,
        "allowed_email_domains": "example.gov",
    }
    values.update(updates)
    return values


@pytest.mark.parametrize(
    "updates",
    [
        {"jwt_secret": "short"},
        {"session_pepper": "replace-with-a-real-secret-that-is-long-enough"},
        {"csrf_secret": "development-only-secret-that-is-long-enough"},
        {"cookie_secure": False},
        {"allowed_email_domains": ""},
        {"email_backend": "smtp", "smtp_host": ""},
        {"cookie_path": "relative"},
        {"api_token_encryption_keys": {}},
        {"api_token_active_key_id": "missing"},
        {
            "api_token_encryption_keys": {
                "development-v1": "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
            },
            "api_token_active_key_id": "development-v1",
        },
    ],
)
def test_production_configuration_rejects_unsafe_values(updates: dict) -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **production_values(**updates))


def test_valid_production_configuration() -> None:
    configured = Settings(
        _env_file=None,
        **production_values(email_backend="smtp", smtp_host="relay.example.gov"),
    )
    assert configured.is_production
    assert configured.cookie_secure
