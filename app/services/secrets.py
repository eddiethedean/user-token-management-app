from __future__ import annotations

import logging
import time
from collections.abc import Callable, Mapping

from fastapi import Request
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.application.feedback import connection_failure
from app.application.ports import RequestMetadata
from app.config import Settings
from app.connectors.base import ConnectionTester
from app.connectors.errors import TransferErrorCode
from app.connectors.redaction import redact_text
from app.connectors.registry import connection_tester_for
from app.db_compat import execute_dml, insert_for, supports_returning
from app.logging_config import log_event, safe_exception_traceback
from app.models import ApiTokenKeyUsage, PipelineCatalogCache, User, UserSecret, new_id, utcnow
from app.services.audit import record_event
from app.services.secret_catalog import (
    SECRET_CATALOG,
    SECRET_PROVIDER_MAP,
    SECRET_PROVIDERS,
)
from app.services.secret_crypto import (
    CredentialEnvelope,
    CredentialEnvelopeError,
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
log = logging.getLogger(__name__)


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
    request: Request | RequestMetadata | None = None,
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
    request: Request | RequestMetadata | None = None,
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
    request: Request | RequestMetadata | None,
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
    stored.validation_code = "connection_saved_untested"
    stored.validation_reference = ""
    stored.validation_message = "Saved. Test the connection before running a transfer."
    stored.runtime_status = ""
    db.execute(
        delete(PipelineCatalogCache).where(
            PipelineCatalogCache.user_id == user.id,
            PipelineCatalogCache.provider == specification.name,
        )
    )
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
    request: Request | RequestMetadata | None = None,
    connector_resolver: Callable[[str], ConnectionTester] | None = None,
) -> UserSecret:
    """Decrypt credentials inside this call, test the connector, and persist health."""
    started = time.perf_counter()
    reference_id = _connection_reference(request)
    specification = require_secret_provider(provider)
    try:
        stored = db.scalar(
            select(UserSecret).where(
                UserSecret.user_id == user.id,
                UserSecret.provider == specification.name,
            )
        )
        if stored is None:
            raise SecretStorageError("Configure the connection before testing it.")
        credentials = decrypt_user_credentials_for_run(
            db,
            settings,
            user=user,
            provider=provider,
            request=request,
            purpose="connection_test",
        )
    except SecretStorageError as exc:
        log_event(
            log,
            "connection.test.failed",
            outcome="failed",
            error_code=(
                "connection_not_configured"
                if "not configured" in str(exc).casefold()
                else str(TransferErrorCode.INTERNAL_ERROR)
            ),
            reference_id=reference_id,
            user_id=user.id,
            provider=specification.name,
            operation="test_connection",
            exception_type=type(exc).__name__,
            duration_ms=int((time.perf_counter() - started) * 1000),
        )
        raise
    from app.connectors.errors import ConnectorError

    try:
        resolver = connector_resolver or connection_tester_for
        health = resolver(provider).test_connection(credentials)
        stored.validation_status = health.status
        stored.validation_message = redact_text(health.message)[:240]
        stored.validation_code = (
            "connection_test_succeeded"
            if health.status == "connected"
            else "connection_test_incomplete"
        )
        stored.validation_reference = reference_id
        log_event(
            log,
            "connection.test.completed",
            outcome="success" if health.status == "connected" else "incomplete",
            reference_id=reference_id,
            user_id=user.id,
            provider=specification.name,
            operation="test_connection",
            duration_ms=int((time.perf_counter() - started) * 1000),
        )
    except ConnectorError as exc:
        feedback = connection_failure(
            exc.code,
            reference_id=reference_id,
            message=exc.summary,
        )
        stored.validation_status = "failed"
        stored.validation_code = str(feedback.code)
        stored.validation_reference = reference_id
        stored.validation_message = feedback.message[:240]
        log_event(
            log,
            "connection.test.failed",
            outcome="failed",
            error_code=str(exc.code),
            reference_id=reference_id,
            user_id=user.id,
            provider=specification.name,
            operation="test_connection",
            retryable=bool(exc.retryable),
            http_status=exc.http_status,
            provider_correlation_id=exc.provider_correlation_id,
            duration_ms=int((time.perf_counter() - started) * 1000),
        )
    except Exception as exc:
        feedback = connection_failure(
            TransferErrorCode.INTERNAL_ERROR,
            reference_id=reference_id,
        )
        stored.validation_status = "failed"
        stored.validation_code = str(feedback.code)
        stored.validation_reference = reference_id
        stored.validation_message = feedback.message[:240]
        log_event(
            log,
            "connection.test.failed",
            outcome="failed",
            error_code=str(TransferErrorCode.INTERNAL_ERROR),
            reference_id=reference_id,
            user_id=user.id,
            provider=specification.name,
            operation="test_connection",
            retryable=False,
            exception_type=type(exc).__name__,
            duration_ms=int((time.perf_counter() - started) * 1000),
            traceback=safe_exception_traceback((type(exc), exc, exc.__traceback__)),
        )
    stored.validated_at = utcnow()
    stored.runtime_status = ""
    latency_ms = int((time.perf_counter() - started) * 1000)
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


def _connection_reference(request: Request | RequestMetadata | None) -> str:
    """Use the request correlation ID when available, otherwise mint a safe reference."""

    if isinstance(request, RequestMetadata):
        return request.reference_id or request.request_id or new_id()
    request_id = (
        getattr(getattr(request, "state", None), "support_reference", "") if request else ""
    )
    return request_id or new_id()


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
    db.execute(
        delete(PipelineCatalogCache).where(
            PipelineCatalogCache.user_id == user.id,
            PipelineCatalogCache.provider == specification.name,
        )
    )
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
    request: Request | RequestMetadata | None = None,
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
    request: Request | RequestMetadata | None = None,
    purpose: str = "run",
) -> dict[str, str]:
    """Return credentials at an authorized connector boundary; never expose them in a response."""
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
        detail={"provider": specification.name, "purpose": purpose},
    )
    db.commit()
    return credentials


def _validate_credentials(
    specification: SecretProvider, credentials: Mapping[str, str]
) -> dict[str, str]:
    return CredentialValidator(max_bytes=_FIELD_MAX_BYTES).validate(specification, credentials)
