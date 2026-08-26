"""Credential validation policy independent of persistence and encryption."""

from __future__ import annotations

from collections.abc import Mapping
from urllib.parse import urlsplit

from app.services.secrets_types import SecretProvider

FIELD_MAX_BYTES = 8192


class CredentialValidator:
    """Normalize and validate provider credentials before they reach storage."""

    def __init__(self, *, max_bytes: int = FIELD_MAX_BYTES) -> None:
        self.max_bytes = max_bytes

    def validate(
        self,
        specification: SecretProvider,
        credentials: Mapping[str, str],
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
            if len(value.encode("utf-8")) > self.max_bytes:
                raise ValueError(f"{field.label} is too long.")
            if value:
                normalized[field.name] = value

        self._validate_token(normalized)
        self._validate_endpoint(normalized)
        self._validate_host(normalized)
        self._validate_port(normalized)
        self._validate_choice(specification, normalized, "sslmode", "PostgreSQL SSL mode")
        self._validate_choice(specification, normalized, "tlsmode", "MongoDB TLS mode")
        return normalized

    @staticmethod
    def _validate_token(credentials: Mapping[str, str]) -> None:
        token = credentials.get("token", "")
        if token and (token != token.strip() or len(token.encode("utf-8")) < 8):
            raise ValueError(
                "API tokens must contain at least 8 bytes without surrounding whitespace."
            )

    @staticmethod
    def _validate_endpoint(credentials: Mapping[str, str]) -> None:
        endpoint = credentials.get("endpoint", "")
        if endpoint:
            parsed = urlsplit(endpoint)
            try:
                port = parsed.port
            except ValueError as exc:
                raise ValueError("API endpoint contains an invalid port.") from exc
            if (
                parsed.scheme not in {"http", "https"}
                or not parsed.hostname
                or parsed.username
                or parsed.password
                or parsed.query
                or parsed.fragment
                or "\\" in endpoint
                or any(character.isspace() for character in endpoint)
                or (port is not None and not 1 <= port <= 65535)
            ):
                raise ValueError(
                    "API endpoint must be a complete HTTP or HTTPS URL without credentials, query, or fragment."
                )

    @staticmethod
    def _validate_host(credentials: Mapping[str, str]) -> None:
        host = credentials.get("host", "")
        if host and any(character in host for character in ("/", " ", "://")):
            raise ValueError("Host must be a hostname or IP address without a URL scheme.")

    @staticmethod
    def _validate_port(credentials: Mapping[str, str]) -> None:
        port = credentials.get("port", "")
        if port and (not port.isdigit() or not 1 <= int(port) <= 65535):
            raise ValueError("Port must be between 1 and 65535.")

    @staticmethod
    def _validate_choice(
        specification: SecretProvider,
        credentials: Mapping[str, str],
        field_name: str,
        label: str,
    ) -> None:
        value = credentials.get(field_name, "")
        field = next((item for item in specification.fields if item.name == field_name), None)
        if value and field and value not in field.options:
            raise ValueError(f"Select a supported {label}.")


def validate_credentials(
    specification: SecretProvider,
    credentials: Mapping[str, str],
) -> dict[str, str]:
    """Functional adapter for callers that do not need a custom policy."""
    return CredentialValidator().validate(specification, credentials)
