"""SQLAlchemy SQLite/PostgreSQL compatibility for upserts and RETURNING."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import delete, select

from app.db_compat import insert_for, supports_returning
from app.models import RateLimitBucket
from app.services.rate_limit import _increment
from tests.helpers import ADMIN_EMAIL, ADMIN_PASSWORD, login_csrf_from, web_login


def test_sqlite_avoids_returning(access_app) -> None:
    from app.database import SessionLocal

    with SessionLocal() as db:
        assert supports_returning(db) is False
        insert = insert_for(db, RateLimitBucket)
        assert insert is not None


def test_rate_limit_increment_uses_sqlalchemy_upsert(access_app) -> None:
    from app.database import SessionLocal

    window = datetime(2026, 8, 11, 20, 37, tzinfo=UTC).replace(tzinfo=None)
    expires = datetime(2026, 8, 11, 20, 39, tzinfo=UTC).replace(tzinfo=None)
    with SessionLocal() as db:
        first = _increment(
            db,
            scope="login:source",
            key_hash="compat-test-key",
            window_started_at=window,
            expires_at=expires,
        )
        second = _increment(
            db,
            scope="login:source",
            key_hash="compat-test-key",
            window_started_at=window,
            expires_at=expires,
        )
        db.commit()
        stored = db.scalar(
            select(RateLimitBucket.count).where(
                RateLimitBucket.scope == "login:source",
                RateLimitBucket.key_hash == "compat-test-key",
            )
        )
    assert first == 1
    assert second == 2
    assert stored == 2


def test_login_succeeds_on_sqlite_without_returning(client) -> None:
    web_login(client, ADMIN_EMAIL, ADMIN_PASSWORD)
    profile = client.get("/profile")
    assert profile.status_code == 200
    assert ADMIN_EMAIL in profile.text


def test_rate_limit_throttles_on_sqlite(client) -> None:
    from app.config import get_settings
    from app.database import SessionLocal

    settings = get_settings()
    original_source = settings.rate_limit_login_per_source
    original_account = settings.rate_limit_login_per_account
    settings.rate_limit_login_per_source = 1
    settings.rate_limit_login_per_account = 1
    try:
        page = client.get("/login")
        token = login_csrf_from(page.text)
        first = client.post(
            "/login",
            data={
                "email": ADMIN_EMAIL,
                "password": "wrong-password-value!!",
                "next": "/profile",
                "preauth_csrf_token": token,
            },
        )
        assert first.status_code in {200, 400}
        page = client.get("/login")
        token = login_csrf_from(page.text)
        limited = client.post(
            "/login",
            data={
                "email": ADMIN_EMAIL,
                "password": "wrong-password-value!!",
                "next": "/profile",
                "preauth_csrf_token": token,
            },
        )
        assert limited.status_code == 429
    finally:
        settings.rate_limit_login_per_source = original_source
        settings.rate_limit_login_per_account = original_account
        with SessionLocal() as db:
            db.execute(delete(RateLimitBucket))
            db.commit()
