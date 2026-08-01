import hashlib
import hmac
import secrets
import uuid
from datetime import timedelta
from typing import Any

import jwt
from jwt import InvalidTokenError

from app.config import Settings
from app.models import User, utcnow


class AccessTokenError(ValueError):
    pass


def random_token(bytes_count: int = 32) -> str:
    return secrets.token_urlsafe(bytes_count)


def hash_token(token: str, pepper: str) -> str:
    return hmac.new(pepper.encode("utf-8"), token.encode("utf-8"), hashlib.sha256).hexdigest()


def create_access_token(user: User, session_id: str, settings: Settings) -> tuple[str, int]:
    now = utcnow()
    expires = now + timedelta(minutes=settings.access_token_minutes)
    payload: dict[str, Any] = {
        "iss": settings.jwt_issuer,
        "aud": settings.jwt_audience,
        "sub": user.id,
        "sid": session_id,
        "jti": str(uuid.uuid4()),
        "iat": now,
        "nbf": now,
        "exp": expires,
        "sv": user.security_version,
        "roles": user.role_names,
    }
    encoded = jwt.encode(payload, settings.jwt_secret, algorithm="HS256")
    return encoded, settings.access_token_minutes * 60


def decode_access_token(token: str, settings: Settings) -> dict[str, Any]:
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=["HS256"],
            audience=settings.jwt_audience,
            issuer=settings.jwt_issuer,
            options={"require": ["exp", "iat", "nbf", "iss", "aud", "sub", "sid", "jti"]},
        )
    except InvalidTokenError as exc:
        raise AccessTokenError("Invalid or expired access token") from exc
    if not isinstance(payload.get("sub"), str) or not isinstance(payload.get("sid"), str):
        raise AccessTokenError("Malformed access token")
    return payload

