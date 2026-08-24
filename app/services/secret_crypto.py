"""Authenticated credential envelope encoding, independent of database writes."""

from __future__ import annotations

import base64
import json
import secrets
from dataclasses import dataclass
from typing import Any

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.config import Settings
from app.models import UserSecret

CREDENTIAL_FORMAT = "relay-credentials-v1"


class CredentialEnvelopeError(RuntimeError):
    """Raised when an encrypted credential envelope cannot be trusted."""


@dataclass(frozen=True)
class EncryptedEnvelope:
    ciphertext: str
    nonce: str
    encrypted_data_key: str
    key_nonce: str
    master_key_id: str


class CredentialEnvelope:
    """Pure cryptographic policy for structured provider credentials."""

    @staticmethod
    def associated_data(user_id: str, secret_id: str, provider: str) -> bytes:
        return f"access-registry:user-secret:v1:{user_id}:{secret_id}:{provider}".encode()

    @staticmethod
    def serialize(credentials: dict[str, str]) -> bytes:
        return json.dumps(
            {"format": CREDENTIAL_FORMAT, "credentials": credentials},
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")

    @staticmethod
    def encrypt(
        settings: Settings,
        *,
        user_id: str,
        secret_id: str,
        provider: str,
        plaintext: bytes,
        key_id: str,
    ) -> EncryptedEnvelope:
        master_key = settings.api_token_key_ring[key_id]
        data_key = AESGCM.generate_key(bit_length=256)
        value_nonce = secrets.token_bytes(12)
        key_nonce = secrets.token_bytes(12)
        aad = CredentialEnvelope.associated_data(user_id, secret_id, provider)
        return EncryptedEnvelope(
            ciphertext=encode(AESGCM(data_key).encrypt(value_nonce, plaintext, aad + b":value")),
            nonce=encode(value_nonce),
            encrypted_data_key=encode(
                AESGCM(master_key).encrypt(
                    key_nonce,
                    data_key,
                    aad + b":data-key:" + key_id.encode(),
                )
            ),
            key_nonce=encode(key_nonce),
            master_key_id=key_id,
        )

    @staticmethod
    def decrypt(settings: Settings, stored: UserSecret) -> dict[str, str]:
        master_key = settings.api_token_key_ring.get(stored.master_key_id)
        if not master_key:
            raise CredentialEnvelopeError("The credential encryption key is unavailable.")
        aad = CredentialEnvelope.associated_data(stored.user_id, stored.id, stored.provider)
        try:
            data_key = AESGCM(master_key).decrypt(
                decode(stored.key_nonce),
                decode(stored.encrypted_data_key),
                aad + b":data-key:" + stored.master_key_id.encode(),
            )
            plaintext = AESGCM(data_key).decrypt(
                decode(stored.nonce),
                decode(stored.ciphertext),
                aad + b":value",
            )
            decoded = plaintext.decode("utf-8")
        except (InvalidTag, UnicodeDecodeError, ValueError) as exc:
            raise CredentialEnvelopeError("The stored credentials could not be decrypted.") from exc

        try:
            payload: Any = json.loads(decoded)
        except json.JSONDecodeError:
            # Values stored before structured credentials were introduced were plain tokens.
            return {"token": decoded}
        if not isinstance(payload, dict) or payload.get("format") != CREDENTIAL_FORMAT:
            raise CredentialEnvelopeError("The stored credential format is not supported.")
        credentials = payload.get("credentials")
        if not isinstance(credentials, dict) or not all(
            isinstance(key, str) and isinstance(value, str) for key, value in credentials.items()
        ):
            raise CredentialEnvelopeError("The stored credential payload is invalid.")
        return credentials


def encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii")


def decode(value: str) -> bytes:
    return base64.b64decode(value, altchars=b"-_", validate=True)
