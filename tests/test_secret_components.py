"""Focused tests for the extracted secret-domain policies."""

from __future__ import annotations

import pytest

from app.config import Settings
from app.models import UserSecret
from app.services.secret_catalog import SECRET_CATALOG, SECRET_PROVIDERS
from app.services.secret_crypto import CredentialEnvelope, CredentialEnvelopeError
from app.services.secret_validation import CredentialValidator


def test_secret_catalog_is_case_insensitive_and_immutable() -> None:
    assert SECRET_CATALOG.require("POSTGRES").name == "postgres"
    assert SECRET_CATALOG.providers == SECRET_PROVIDERS
    with pytest.raises(ValueError, match="supported connection provider"):
        SECRET_CATALOG.require("unknown")


def test_credential_validator_normalizes_non_secret_fields() -> None:
    provider = SECRET_CATALOG.require("mss")
    normalized = CredentialValidator().validate(
        provider,
        {"endpoint": " https://mss.example ", "token": "secure-token-42"},
    )
    assert normalized == {
        "endpoint": "https://mss.example",
        "token": "secure-token-42",
        "branch": "master",
        "ca_profile": "system",
    }


@pytest.mark.parametrize(
    ("credentials", "message"),
    [
        ({"endpoint": "ftp://mss.example", "token": "secure-token-42"}, "HTTP or HTTPS"),
        ({"endpoint": "https://mss.example", "token": "short"}, "at least 8"),
        (
            {"endpoint": "https://user:password@mss.example", "token": "secure-token-42"},
            "without credentials",
        ),
        (
            {"endpoint": "https://mss.example?tenant=secret", "token": "secure-token-42"},
            "without credentials",
        ),
        (
            {"endpoint": "https://mss.example:99999", "token": "secure-token-42"},
            "invalid port",
        ),
    ],
)
def test_credential_validator_rejects_invalid_values(
    credentials: dict[str, str], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        CredentialValidator().validate(SECRET_CATALOG.require("mss"), credentials)


def test_credential_envelope_round_trips_without_database_dependencies() -> None:
    settings = Settings(
        api_token_encryption_keys={
            "test": "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",
        },
        api_token_active_key_id="test",
    )
    stored = UserSecret(
        id="secret-1",
        user_id="user-1",
        provider="mss",
    )
    envelope = CredentialEnvelope.encrypt(
        settings,
        user_id=stored.user_id,
        secret_id=stored.id,
        provider=stored.provider,
        plaintext=CredentialEnvelope.serialize({"token": "secure-token-42"}),
        key_id="test",
    )
    stored.ciphertext = envelope.ciphertext
    stored.nonce = envelope.nonce
    stored.encrypted_data_key = envelope.encrypted_data_key
    stored.key_nonce = envelope.key_nonce
    stored.master_key_id = envelope.master_key_id

    assert CredentialEnvelope.decrypt(settings, stored) == {"token": "secure-token-42"}


def test_credential_envelope_rejects_tampering() -> None:
    settings = Settings(
        api_token_encryption_keys={
            "test": "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",
        },
        api_token_active_key_id="test",
    )
    stored = UserSecret(
        id="secret-1",
        user_id="user-1",
        provider="mss",
        ciphertext="tampered",
        nonce="tampered",
        encrypted_data_key="tampered",
        key_nonce="tampered",
        master_key_id="test",
    )
    with pytest.raises(CredentialEnvelopeError):
        CredentialEnvelope.decrypt(settings, stored)
