from __future__ import annotations

from collections.abc import Callable, Mapping

from fastapi import Request
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.connectors.base import Connector
from app.connectors.registry import connector_for
from app.db_compat import execute_dml, insert_for, supports_returning
from app.models import ApiTokenKeyUsage, User, UserSecret, new_id, utcnow
from app.services.audit import record_event
from app.services.secret_catalog import (
    SECRET_CATALOG,
    SECRET_PROVIDER_MAP,
    SECRET_PROVIDERS,
)
from app.services.secret_crypto import (
    CredentialEnvelope,
    CredentialEnvelopeError,
    decode,
    encode,
)
from app.services.secret_validation import CredentialValidator
from app.services.secrets_types import CredentialField, SecretProvider

__all__ = [
    "CredentialField",
    "SecretProvider",
    "SECRET_PROVIDERS",
    "SECRET_PROVIDER_MAP",
    "SecretStorageError",
    "decrypt_user_credentials_for_run",
    "decrypt_user_secret_for_run",
    "delete_user_secret",
    "list_user_secrets",
    "require_secret_provider",
    "store_user_credentials",
    "store_user_secret",
    "test_user_connection",
]

_FIELD_MAX_BYTES = 8192


class SecretStorageError(CredentialEnvelopeError):
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
    return SECRET_CATALOG.require(provider)


def list_user_secrets(db: Session, user: User) -> list[tuple[SecretProvider, UserSecret | None]]:
    stored = {
        secret.provider: secret
        for secret in db.scalars(select(UserSecret).where(UserSecret.user_id == user.id)).all()
    }
    return [(provider, stored.get(provider.name)) for provider in SECRET_CATALOG.providers]


def store_user_secret(
    db: Session,
    settings: Settings,
    *,
    user: User,
    provider: str,
    token: str,
    request: Request | None = None,
) -> UserSecret:
    """Backward-compatible token-only wrapper for API providers."""
    return store_user_credentials(
        db,
        settings,
        user=user,
        provider=provider,
        credentials={"token": token},
        request=request,
    )


def store_user_credentials(
    db: Session,
    settings: Settings,
    *,
    user: User,
    provider: str,
    credentials: Mapping[str, str],
    request: Request | None = None,
) -> UserSecret:
    specification = require_secret_provider(provider)
    normalized = _validate_credentials(specification, credentials)
    encoded_token = CredentialEnvelope.serialize(normalized)
    if len(encoded_token) > _FIELD_MAX_BYTES:
        raise ValueError("Connection credentials are too large to store.")

    return _store_encrypted_value(
        db,
        settings,
        user=user,
        specification=specification,
        encoded_value=encoded_token,
        request=request,
    )


def _store_encrypted_value(
    db: Session,
    settings: Settings,
    *,
    user: User,
    specification: SecretProvider,
    encoded_value: bytes,
    request: Request | None,
) -> UserSecret:
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
    with db.no_autoflush:
        _reserve_master_key_use(db, settings, key_id)
    envelope = CredentialEnvelope.encrypt(
        settings,
        user_id=user.id,
        secret_id=stored.id,
        provider=specification.name,
        plaintext=encoded_value,
        key_id=key_id,
    )
    stored.ciphertext = envelope.ciphertext
    stored.nonce = envelope.nonce
    stored.encrypted_data_key = envelope.encrypted_data_key
    stored.key_nonce = envelope.key_nonce
    stored.master_key_id = envelope.master_key_id
    stored.updated_at = utcnow()
    stored.validation_status = "untested"
    stored.validated_at = None
    stored.validation_message = "Saved. Test the connection before running a transfer."
    stored.runtime_status = ""
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


def test_user_connection(
    db: Session,
    *,
    user: User,
    provider: str,
    settings: Settings,
    request: Request | None = None,
    connector_resolver: Callable[[str], Connector] = connector_for,
) -> UserSecret:
    """Decrypt credentials inside this call, test the connector, and persist health."""
    specification = require_secret_provider(provider)
    stored = db.scalar(
        select(UserSecret).where(
            UserSecret.user_id == user.id,
            UserSecret.provider == specification.name,
        )
    )
    if stored is None:
        raise SecretStorageError("Configure the connection before testing it.")
    credentials = decrypt_user_credentials_for_run(
        db, settings, user=user, provider=provider, request=request
    )
    from app.connectors.errors import ConnectorError

    started = utcnow()
    try:
        health = connector_resolver(provider).test_connection(credentials)
        stored.validation_status = health.status
        stored.validation_message = health.message[:240]
    except ConnectorError as exc:
        stored.validation_status = "failed"
        stored.validation_message = str(exc)[:240]
    stored.validated_at = utcnow()
    stored.runtime_status = ""
    latency_ms = int((utcnow() - started).total_seconds() * 1000)
    if stored.validation_status == "connected" and latency_ms:
        stored.validation_message = f"{stored.validation_message} · {latency_ms} ms"[:240]
    record_event(
        db,
        "connection.tested",
        request=request,
        actor=user,
        target=user,
        detail={"provider": specification.name, "status": stored.validation_status},
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
    """Return a provider's primary secret; retained for token-only integrations."""
    credentials = decrypt_user_credentials_for_run(
        db,
        settings,
        user=user,
        provider=provider,
        request=request,
    )
    primary_field = "password" if provider.casefold() in {"postgres", "mongodb"} else "token"
    secret = credentials.get(primary_field)
    if not secret:
        raise SecretStorageError("The stored connection is missing its primary credential.")
    return secret


def decrypt_user_credentials_for_run(
    db: Session,
    settings: Settings,
    *,
    user: User,
    provider: str,
    request: Request | None = None,
) -> dict[str, str]:
    """Return credentials at the authorized run boundary; never expose them through a route."""
    specification = require_secret_provider(provider)
    stored = db.scalar(
        select(UserSecret).where(
            UserSecret.user_id == user.id, UserSecret.provider == specification.name
        )
    )
    if not stored:
        raise SecretStorageError("The requested connection is not configured.")
    try:
        credentials = CredentialEnvelope.decrypt(settings, stored)
    except CredentialEnvelopeError as exc:
        raise SecretStorageError(str(exc)) from exc
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
    return credentials


def _validate_credentials(
    specification: SecretProvider, credentials: Mapping[str, str]
) -> dict[str, str]:
    return CredentialValidator(max_bytes=_FIELD_MAX_BYTES).validate(specification, credentials)


def _aad(user_id: str, secret_id: str, provider: str) -> bytes:
    return CredentialEnvelope.associated_data(user_id, secret_id, provider)


def _encode(value: bytes) -> str:
    return encode(value)


def _decode(value: str) -> bytes:
    return decode(value)
