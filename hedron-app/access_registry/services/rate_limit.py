import hashlib
import hmac
from datetime import UTC, datetime, timedelta

from fastapi import HTTPException, Request, status
from sqlalchemy import delete
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from access_registry.config import Settings
from access_registry.models import RateLimitBucket, utcnow
from access_registry.security.client import client_ip
from access_registry.services.audit import record_event


def _window_start(now: datetime, seconds: int) -> datetime:
    epoch = int(now.replace(tzinfo=UTC).timestamp())
    return datetime.fromtimestamp(epoch - (epoch % seconds), UTC).replace(tzinfo=None)


def _key_hash(settings: Settings, *, scope: str, kind: str, value: str) -> str:
    return hmac.new(
        settings.session_pepper.encode("utf-8"),
        f"{scope}:{kind}:{value}".encode(),
        hashlib.sha256,
    ).hexdigest()


def _increment(
    db: Session,
    *,
    scope: str,
    key_hash: str,
    window_started_at: datetime,
    expires_at: datetime,
) -> int:
    values = {
        "scope": scope,
        "key_hash": key_hash,
        "window_started_at": window_started_at,
        "count": 1,
        "expires_at": expires_at,
    }
    dialect = db.get_bind().dialect.name
    if dialect == "postgresql":
        statement = postgresql_insert(RateLimitBucket).values(**values)
    elif dialect == "sqlite":
        statement = sqlite_insert(RateLimitBucket).values(**values)
    else:
        raise RuntimeError(f"Rate limiting does not support database dialect {dialect!r}")
    statement = statement.on_conflict_do_update(
        index_elements=["scope", "key_hash", "window_started_at"],
        set_={"count": RateLimitBucket.count + 1, "expires_at": expires_at},
    ).returning(RateLimitBucket.count)
    return int(db.execute(statement).scalar_one())


def check_rate_limit(
    db: Session,
    settings: Settings,
    request: Request,
    *,
    scope: str,
    source_limit: int,
    account_limit: int | None = None,
    account_key: str | None = None,
) -> None:
    """Apply atomic fixed-window limits shared by every process using the application database."""
    if not settings.rate_limit_enabled:
        return

    now = utcnow()
    window_seconds = settings.rate_limit_window_seconds
    window_started_at = _window_start(now, window_seconds)
    expires_at = window_started_at + timedelta(seconds=window_seconds * 2)
    source = client_ip(request, settings) or "unknown"
    checks = [("source", source, source_limit)]
    if account_key and account_limit is not None:
        checks.append(("account", account_key.strip().casefold(), account_limit))

    exceeded: list[str] = []
    for kind, value, limit in checks:
        count = _increment(
            db,
            scope=f"{scope}:{kind}",
            key_hash=_key_hash(settings, scope=scope, kind=kind, value=value),
            window_started_at=window_started_at,
            expires_at=expires_at,
        )
        if count > limit:
            exceeded.append(kind)

    db.execute(delete(RateLimitBucket).where(RateLimitBucket.expires_at <= now))
    if exceeded:
        record_event(
            db,
            "security.rate_limited",
            request=request,
            outcome="denied",
            detail={"scope": scope, "dimensions": exceeded},
        )
    db.commit()
    if exceeded:
        retry_after = max(
            1,
            int((window_started_at + timedelta(seconds=window_seconds) - now).total_seconds()),
        )
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many requests. Try again later.",
            headers={"Retry-After": str(retry_after)},
        )
