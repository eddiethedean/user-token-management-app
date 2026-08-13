from __future__ import annotations

import base64
import json
import secrets
from collections.abc import Mapping
from dataclasses import dataclass
from urllib.parse import urlsplit

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from fastapi import Request
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.db_compat import execute_dml, insert_for, supports_returning
from app.models import ApiTokenKeyUsage, User, UserSecret, new_id, utcnow
from app.services.audit import record_event
from app.services.catalogs import require_catalog_provider


@dataclass(frozen=True)
class CredentialField:
    name: str
    label: str
    placeholder: str
    input_type: str = "text"
    autocomplete: str = "off"
    required: bool = False
    default: str = ""
    options: tuple[str, ...] = ()


@dataclass(frozen=True)
class SecretProvider:
    name: str
    label: str
    mark: str
    environment_variable: str
    fields: tuple[CredentialField, ...]


SECRET_PROVIDERS = (
    SecretProvider(
        "advana",
        "Advana",
        "AV",
        "ADVANA_API_TOKEN",
        (
            CredentialField(
                "endpoint", "API endpoint", "https://api.advana.example", input_type="url"
            ),
            CredentialField("username", "Username", "Optional service account"),
            CredentialField(
                "token",
                "API token",
                "Paste Advana API token",
                input_type="password",
                autocomplete="new-password",
                required=True,
            ),
        ),
    ),
    SecretProvider(
        "mss",
        "MSS",
        "MSS",
        "MSS_API_TOKEN",
        (
            CredentialField(
                "endpoint", "API endpoint", "https://api.mss.example", input_type="url"
            ),
            CredentialField("username", "Username", "Optional service account"),
            CredentialField(
                "token",
                "API token",
                "Paste MSS API token",
                input_type="password",
                autocomplete="new-password",
                required=True,
            ),
        ),
    ),
    SecretProvider(
        "postgres",
        "PostgreSQL",
        "PG",
        "DATABASE_URL",
        (
            CredentialField("host", "Host", "db.example.internal", required=True),
            CredentialField("port", "Port", "5432", required=True, default="5432"),
            CredentialField("database", "Database", "analytics", required=True),
            CredentialField(
                "username",
                "Username",
                "data_mover_service",
                autocomplete="username",
                required=True,
            ),
            CredentialField(
                "password",
                "Password",
                "Enter database password",
                input_type="password",
                autocomplete="new-password",
                required=True,
            ),
            CredentialField(
                "sslmode",
                "SSL mode",
                "require",
                required=True,
                default="require",
                options=("require", "verify-ca", "verify-full"),
            ),
        ),
    ),
    SecretProvider(
        "mongodb",
        "MongoDB",
        "MDB",
        "MONGODB_URI",
        (
            CredentialField("host", "Host", "mongodb.example.internal", required=True),
            CredentialField("port", "Port", "27017", required=True, default="27017"),
            CredentialField("database", "Database", "operations", required=True),
            CredentialField(
                "username",
                "Username",
                "data_mover_service",
                autocomplete="username",
                required=True,
            ),
            CredentialField(
                "password",
                "Password",
                "Enter database password",
                input_type="password",
                autocomplete="new-password",
                required=True,
            ),
            CredentialField(
                "auth_database",
                "Authentication database",
                "admin",
                required=True,
                default="admin",
            ),
            CredentialField(
                "tlsmode",
                "TLS mode",
                "require",
                required=True,
                default="require",
                options=("require", "prefer", "disable"),
            ),
        ),
    ),
)
SECRET_PROVIDER_MAP = {provider.name: provider for provider in SECRET_PROVIDERS}

_CREDENTIAL_FORMAT = "relay-credentials-v1"
_FIELD_MAX_BYTES = 8192


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
        raise ValueError("Select a supported connection provider.")
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
    plaintext = json.dumps(
        {"format": _CREDENTIAL_FORMAT, "credentials": normalized},
        separators=(",", ":"),
        sort_keys=True,
    )
    encoded_token = plaintext.encode("utf-8")
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
    master_key = settings.api_token_key_ring[key_id]
    with db.no_autoflush:
        _reserve_master_key_use(db, settings, key_id)
    data_key = AESGCM.generate_key(bit_length=256)
    value_nonce = secrets.token_bytes(12)
    key_nonce = secrets.token_bytes(12)
    aad = _aad(user.id, stored.id, specification.name)
    stored.ciphertext = _encode(
        AESGCM(data_key).encrypt(value_nonce, encoded_value, aad + b":value")
    )
    stored.nonce = _encode(value_nonce)
    stored.encrypted_data_key = _encode(
        AESGCM(master_key).encrypt(key_nonce, data_key, aad + b":data-key:" + key_id.encode())
    )
    stored.key_nonce = _encode(key_nonce)
    stored.master_key_id = key_id
    stored.updated_at = utcnow()
    _set_simulated_connection_health(stored, specification.name)
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
    request: Request | None = None,
) -> UserSecret:
    """Run the demo connection check and persist its synthetic health telemetry."""
    specification = require_secret_provider(provider)
    stored = db.scalar(
        select(UserSecret).where(
            UserSecret.user_id == user.id,
            UserSecret.provider == specification.name,
        )
    )
    if stored is None:
        raise SecretStorageError("Configure the connection before testing it.")
    _set_simulated_connection_health(stored, specification.name)
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


def wake_provider_runtime(
    db: Session,
    *,
    user: User,
    provider: str,
    request: Request | None = None,
) -> UserSecret:
    """Wake a simulated provider compute runtime when the catalog supports it."""
    specification = require_secret_provider(provider)
    catalog = require_catalog_provider(specification.name)
    if not catalog.supports_runtime_wake:
        raise ValueError(f"{catalog.label} does not expose a wakeable runtime.")
    stored = db.scalar(
        select(UserSecret).where(
            UserSecret.user_id == user.id,
            UserSecret.provider == specification.name,
        )
    )
    if stored is None:
        raise SecretStorageError("Configure the connection before waking its runtime.")
    now = utcnow()
    stored.runtime_status = "running"
    stored.runtime_updated_at = now
    stored.validation_status = "connected"
    stored.validated_at = now
    stored.validation_message = (
        f"{catalog.technology} cluster running · SQL endpoint ready · "
        f"{catalog.validation_latency_ms} ms"
    )
    record_event(
        db,
        "connection.runtime_woken",
        request=request,
        actor=user,
        target=user,
        detail={"provider": specification.name, "runtime": "running"},
    )
    db.commit()
    db.refresh(stored)
    return stored


def _set_simulated_connection_health(stored: UserSecret, provider: str) -> None:
    catalog = require_catalog_provider(provider)
    now = utcnow()
    stored.validation_status = "connected"
    stored.validated_at = now
    stored.validation_message = (
        f"{catalog.technology} handshake succeeded · {catalog.validation_latency_ms} ms"
    )
    if catalog.supports_runtime_wake and not stored.runtime_status:
        stored.runtime_status = "sleeping"
        stored.runtime_updated_at = now


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
    master_key = settings.api_token_key_ring.get(stored.master_key_id)
    if not master_key:
        raise SecretStorageError("The credential encryption key is unavailable.")
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
        decoded = plaintext.decode("utf-8")
    except (InvalidTag, UnicodeDecodeError, ValueError) as exc:
        raise SecretStorageError("The stored credentials could not be decrypted.") from exc
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
    try:
        payload = json.loads(decoded)
    except json.JSONDecodeError:
        # Values stored before structured credentials were introduced are plain API tokens.
        return {"token": decoded}
    if not isinstance(payload, dict) or payload.get("format") != _CREDENTIAL_FORMAT:
        raise SecretStorageError("The stored credential format is not supported.")
    credentials = payload.get("credentials")
    if not isinstance(credentials, dict) or not all(
        isinstance(key, str) and isinstance(value, str) for key, value in credentials.items()
    ):
        raise SecretStorageError("The stored credential payload is invalid.")
    return credentials


def _validate_credentials(
    specification: SecretProvider, credentials: Mapping[str, str]
) -> dict[str, str]:
    allowed = {field.name for field in specification.fields}
    unexpected = set(credentials) - allowed
    if unexpected:
        raise ValueError("Unsupported credential fields were submitted.")

    normalized: dict[str, str] = {}
    for field in specification.fields:
        value = str(credentials.get(field.name, field.default))
        if field.input_type != "password":
            value = value.strip()
        if field.required and not value:
            raise ValueError(f"{field.label} is required for {specification.label}.")
        if len(value.encode("utf-8")) > _FIELD_MAX_BYTES:
            raise ValueError(f"{field.label} is too long.")
        if value:
            normalized[field.name] = value

    token = normalized.get("token", "")
    if token and (token != token.strip() or len(token.encode("utf-8")) < 8):
        raise ValueError("API tokens must contain at least 8 bytes without surrounding whitespace.")
    password = normalized.get("password", "")
    if password and len(password.encode("utf-8")) < 1:
        raise ValueError("Password is required.")
    endpoint = normalized.get("endpoint", "")
    if endpoint:
        parsed = urlsplit(endpoint)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("API endpoint must be a complete HTTP or HTTPS URL.")
    host = normalized.get("host", "")
    if host and any(character in host for character in ("/", " ", "://")):
        raise ValueError("Host must be a hostname or IP address without a URL scheme.")
    port = normalized.get("port", "")
    if port and (not port.isdigit() or not 1 <= int(port) <= 65535):
        raise ValueError("Port must be between 1 and 65535.")
    sslmode = normalized.get("sslmode", "")
    ssl_field = next((field for field in specification.fields if field.name == "sslmode"), None)
    if sslmode and ssl_field and sslmode not in ssl_field.options:
        raise ValueError("Select a supported PostgreSQL SSL mode.")
    tlsmode = normalized.get("tlsmode", "")
    tls_field = next((field for field in specification.fields if field.name == "tlsmode"), None)
    if tlsmode and tls_field and tlsmode not in tls_field.options:
        raise ValueError("Select a supported MongoDB TLS mode.")
    return normalized


def _aad(user_id: str, secret_id: str, provider: str) -> bytes:
    return f"access-registry:user-secret:v1:{user_id}:{secret_id}:{provider}".encode()


def _encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii")


def _decode(value: str) -> bytes:
    return base64.b64decode(value, altchars=b"-_", validate=True)
