import re

from fastapi import Request
from sqlalchemy import select

from app.config import get_settings
from app.database import SessionLocal
from app.models import AuditEvent, RateLimitBucket
from app.security.client import client_ip

ADMIN_EMAIL = "admin@example.gov"


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


def test_login_limit_is_shared_persisted_hashed_and_audited(client) -> None:
    settings = get_settings()
    original_source = settings.rate_limit_login_per_source
    original_account = settings.rate_limit_login_per_account
    settings.rate_limit_login_per_source = 2
    settings.rate_limit_login_per_account = 2
    try:
        for _ in range(2):
            response = client.post(
                "/api/v1/auth/token",
                json={"email": ADMIN_EMAIL, "password": "incorrect-password"},
            )
            assert response.status_code == 401
        limited = client.post(
            "/api/v1/auth/token",
            json={"email": ADMIN_EMAIL, "password": "incorrect-password"},
        )
        assert limited.status_code == 429
        assert 1 <= int(limited.headers["retry-after"]) <= settings.rate_limit_window_seconds

        with SessionLocal() as first_db, SessionLocal() as second_db:
            buckets = first_db.scalars(select(RateLimitBucket)).all()
            assert buckets
            assert all(len(bucket.key_hash) == 64 for bucket in buckets)
            assert all(ADMIN_EMAIL not in bucket.key_hash for bucket in buckets)
            assert max(bucket.count for bucket in buckets) == 3
            event = second_db.scalar(
                select(AuditEvent).where(AuditEvent.event_type == "security.rate_limited")
            )
            assert event is not None
            assert event.outcome == "denied"
            assert ADMIN_EMAIL not in event.detail
    finally:
        settings.rate_limit_login_per_source = original_source
        settings.rate_limit_login_per_account = original_account


def test_account_limit_does_not_block_a_different_account(client) -> None:
    settings = get_settings()
    original_source = settings.rate_limit_login_per_source
    original_account = settings.rate_limit_login_per_account
    settings.rate_limit_login_per_source = 20
    settings.rate_limit_login_per_account = 1
    try:
        first = client.post(
            "/api/v1/auth/token",
            json={"email": "first@example.gov", "password": "incorrect-password"},
        )
        repeated = client.post(
            "/api/v1/auth/token",
            json={"email": "first@example.gov", "password": "incorrect-password"},
        )
        different = client.post(
            "/api/v1/auth/token",
            json={"email": "second@example.gov", "password": "incorrect-password"},
        )
        assert first.status_code == 401
        assert repeated.status_code == 429
        assert different.status_code == 401
    finally:
        settings.rate_limit_login_per_source = original_source
        settings.rate_limit_login_per_account = original_account


def test_forwarded_source_is_used_only_for_an_explicitly_trusted_proxy() -> None:
    base = get_settings()
    untrusted = base.model_copy(update={"trusted_proxy_ips": ""})
    trusted = base.model_copy(update={"trusted_proxy_ips": "10.0.0.10"})
    request = request_with_client("10.0.0.10", forwarded="192.0.2.44, 10.0.0.10")
    assert client_ip(request, untrusted) == "10.0.0.10"
    assert client_ip(request, trusted) == "192.0.2.44"

    malformed = request_with_client("10.0.0.10", forwarded="not-an-ip")
    assert client_ip(malformed, trusted) == "10.0.0.10"


def test_browser_rate_limit_has_html_response_and_retry_header(client) -> None:
    settings = get_settings()
    original_source = settings.rate_limit_login_per_source
    original_account = settings.rate_limit_login_per_account
    settings.rate_limit_login_per_source = 1
    settings.rate_limit_login_per_account = 1
    try:
        page = client.get("/login")
        token_match = re.search(r'name="preauth_csrf_token" value="([^"]+)"', page.text)
        assert token_match is not None
        rejected = client.post(
            "/login",
            data={
                "email": "missing@example.gov",
                "password": "wrong",
                "next": "/profile",
                "preauth_csrf_token": token_match.group(1),
            },
        )
        token_match = re.search(r'name="preauth_csrf_token" value="([^"]+)"', rejected.text)
        assert token_match is not None
        limited = client.post(
            "/login",
            data={
                "email": "missing@example.gov",
                "password": "wrong",
                "next": "/profile",
                "preauth_csrf_token": token_match.group(1),
            },
            headers={"Accept": "text/html"},
        )
        assert limited.status_code == 429
        assert "Try again shortly" in limited.text
        assert limited.headers["retry-after"]
    finally:
        settings.rate_limit_login_per_source = original_source
        settings.rate_limit_login_per_account = original_account
