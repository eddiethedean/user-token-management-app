from __future__ import annotations

import base64
import secrets
from dataclasses import dataclass

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from fastapi import Request
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.db_compat import execute_dml, insert_for, supports_returning
from app.models import ApiTokenKeyUsage, User, UserSecret, new_id, utcnow
from app.services.audit import record_event


@dataclass(frozen=True)
class SecretProvider:
    name: str
    label: str
    mark: str
    environment_variable: str


SECRET_PROVIDERS = (
    SecretProvider("advana", "Advana", "AV", "ADVANA_API_TOKEN"),
    SecretProvider("ade", "ADE", "ADE", "ADE_API_TOKEN"),
    SecretProvider("mss", "MSS", "MSS", "MSS_API_TOKEN"),
)
SECRET_PROVIDER_MAP = {provider.name: provider for provider in SECRET_PROVIDERS}


class SecretStorageError(RuntimeError):
    pass


def _reserve_master_key_use(db: Session, settings: Settings, key_id: str) -> int:
    values = {"key_id": key_id, "wrap_count": 1, "updated_at": utcnow()}
    statement = (
        insert_for(db, ApiTokenKeyUsage)
        .values(**values)
        .on_conflict_do_update(
            index_elements=[ApiTokenKeyUsage.key_id],
            set_={
                "wrap_count": ApiTokenKeyUsage.wrap_count + 1,
                "updated_at": values["updated_at"],
            },
            where=ApiTokenKeyUsage.wrap_count < settings.api_token_max_wraps_per_key,
        )
    )
    if supports_returning(db):
        count = db.scalar(statement.returning(ApiTokenKeyUsage.wrap_count))
    else:
        result = execute_dml(db, statement)
        if result.rowcount == 0:
            count = None
        else:
            count = db.scalar(
                select(ApiTokenKeyUsage.wrap_count).where(ApiTokenKeyUsage.key_id == key_id)
            )
    if count is None:
        raise SecretStorageError(
            "The active API-token encryption key reached its usage limit; rotate the key."
        )
    return int(count)


def require_secret_provider(provider: str) -> SecretProvider:
    specification = SECRET_PROVIDER_MAP.get(provider.casefold())
    if not specification:
        raise ValueError("Select a supported API token provider.")
    return specification


def list_user_secrets(db: Session, user: User) -> list[tuple[SecretProvider, UserSecret | None]]:
    stored = {
        secret.provider: secret
        for secret in db.scalars(select(UserSecret).where(UserSecret.user_id == user.id)).all()
    }
    return [(provider, stored.get(provider.name)) for provider in SECRET_PROVIDERS]


def store_user_secret(
    db: Session,
    settings: Settings,
    *,
    user: User,
    provider: str,
    token: str,
    request: Request | None = None,
) -> UserSecret:
    specification = require_secret_provider(provider)
    if token != token.strip():
        raise ValueError("API tokens cannot begin or end with whitespace.")
    encoded_token = token.encode("utf-8")
    if len(encoded_token) < 8 or len(encoded_token) > 8192:
        raise ValueError("API tokens must contain between 8 and 8192 bytes.")

    # The user row is a stable lock target even when this provider has no secret yet.
    # This prevents concurrent first-time writes from racing the unique constraint.
    db.scalar(select(User).where(User.id == user.id).with_for_update())
    stored = db.scalar(
        select(UserSecret).where(
            UserSecret.user_id == user.id, UserSecret.provider == specification.name
        )
    )
    event_type = "api_token.replaced" if stored else "api_token.created"
    if not stored:
        stored = UserSecret(id=new_id(), user_id=user.id, provider=specification.name)
        db.add(stored)

    key_id = settings.api_token_active_key_id
    master_key = settings.api_token_key_ring[key_id]
    with db.no_autoflush:
        _reserve_master_key_use(db, settings, key_id)
    data_key = AESGCM.generate_key(bit_length=256)
    value_nonce = secrets.token_bytes(12)
    key_nonce = secrets.token_bytes(12)
    aad = _aad(user.id, stored.id, specification.name)
    stored.ciphertext = _encode(
        AESGCM(data_key).encrypt(value_nonce, encoded_token, aad + b":value")
    )
    stored.nonce = _encode(value_nonce)
    stored.encrypted_data_key = _encode(
        AESGCM(master_key).encrypt(key_nonce, data_key, aad + b":data-key:" + key_id.encode())
    )
    stored.key_nonce = _encode(key_nonce)
    stored.master_key_id = key_id
    stored.updated_at = utcnow()
    record_event(
        db,
        event_type,
        request=request,
        actor=user,
        target=user,
        detail={"provider": specification.name},
    )
    db.commit()
    db.refresh(stored)
    return stored


def delete_user_secret(
    db: Session,
    *,
    user: User,
    provider: str,
    request: Request | None = None,
) -> bool:
    specification = require_secret_provider(provider)
    db.scalar(select(User).where(User.id == user.id).with_for_update())
    remove = (
        delete(UserSecret)
        .where(UserSecret.user_id == user.id, UserSecret.provider == specification.name)
        .execution_options(synchronize_session=False)
    )
    if supports_returning(db):
        deleted_id = db.scalar(remove.returning(UserSecret.id))
    else:
        # Capture the id before DELETE — a post-write SELECT cannot see the removed row.
        existing = db.scalar(
            select(UserSecret.id).where(
                UserSecret.user_id == user.id, UserSecret.provider == specification.name
            )
        )
        result = execute_dml(db, remove)
        deleted_id = existing if result.rowcount else None
    if not deleted_id:
        db.rollback()
        return False
    record_event(
        db,
        "api_token.deleted",
        request=request,
        actor=user,
        target=user,
        detail={"provider": specification.name},
    )
    db.commit()
    return True


def decrypt_user_secret_for_run(
    db: Session,
    settings: Settings,
    *,
    user: User,
    provider: str,
    request: Request | None = None,
) -> str:
    """Return one authorized user's token to the run boundary; never expose this through a route."""
    specification = require_secret_provider(provider)
    stored = db.scalar(
        select(UserSecret).where(
            UserSecret.user_id == user.id, UserSecret.provider == specification.name
        )
    )
    if not stored:
        raise SecretStorageError("The requested API token is not configured.")
    master_key = settings.api_token_key_ring.get(stored.master_key_id)
    if not master_key:
        raise SecretStorageError("The API token encryption key is unavailable.")
    aad = _aad(user.id, stored.id, stored.provider)
    try:
        data_key = AESGCM(master_key).decrypt(
            _decode(stored.key_nonce),
            _decode(stored.encrypted_data_key),
            aad + b":data-key:" + stored.master_key_id.encode(),
        )
        plaintext = AESGCM(data_key).decrypt(
            _decode(stored.nonce), _decode(stored.ciphertext), aad + b":value"
        )
        token = plaintext.decode("utf-8")
    except (InvalidTag, UnicodeDecodeError, ValueError) as exc:
        raise SecretStorageError("The stored API token could not be decrypted.") from exc
    stored.last_used_at = utcnow()
    record_event(
        db,
        "api_token.used",
        request=request,
        actor=user,
        target=user,
        detail={"provider": specification.name},
    )
    db.commit()
    return token


def _aad(user_id: str, secret_id: str, provider: str) -> bytes:
    return f"access-registry:user-secret:v1:{user_id}:{secret_id}:{provider}".encode()


def _encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii")


def _decode(value: str) -> bytes:
    return base64.b64decode(value, altchars=b"-_", validate=True)
