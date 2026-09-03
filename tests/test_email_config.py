"""Compatibility settings for the jwt-user-management email and directory interface."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.config import Settings


def test_reference_smtp_environment_names_are_accepted(monkeypatch):
    monkeypatch.delenv("EMAIL_FROM", raising=False)
    monkeypatch.delenv("SMTP_STARTTLS", raising=False)
    monkeypatch.setenv("SMTP_FROM_EMAIL", "Data Mover <relay@example.gov>")
    monkeypatch.setenv("SMTP_USE_TLS", "false")

    settings = Settings(_env_file=None)

    assert settings.email_from == "Data Mover <relay@example.gov>"
    assert settings.smtp_starttls is False


def test_reference_directory_environment_names_are_accepted(monkeypatch):
    monkeypatch.delenv("DIRECTORY_LOOKUP_TIMEOUT_SECONDS", raising=False)
    monkeypatch.delenv("DIRECTORY_LOOKUP_VERIFY_TLS", raising=False)
    monkeypatch.setenv("DIRECTORY_LOOKUP_TIMEOUT_S", "7")
    monkeypatch.setenv("DIRECTORY_LOOKUP_VERIFY_SSL", "false")

    settings = Settings(_env_file=None)

    assert settings.directory_lookup_timeout_seconds == 7
    assert settings.directory_lookup_verify_tls is False


def test_production_rejects_plaintext_port_25_fallback() -> None:
    with pytest.raises(ValueError, match="SMTP_ALLOW_LEGACY_PORT25_FALLBACK"):
        Settings(
            _env_file=None,
            app_env="production",
            public_base_url="https://mover.example.gov",
            database_url="postgresql+psycopg://mover:pass@localhost:5432/app",
            jwt_secret="production-jwt-secret-must-be-long-enough",
            session_pepper="production-session-pepper-must-be-long",
            csrf_secret="production-csrf-secret-must-be-long-enuf",
            api_token_encryption_keys={"prod-v1": "AQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQE="},
            api_token_active_key_id="prod-v1",
            cookie_secure=True,
            allowed_email_domains="example.gov",
            email_backend="smtp",
            email_redact_sent_bodies=True,
            smtp_host="smtp.example.gov",
            smtp_starttls=True,
            smtp_allow_legacy_port25_fallback=True,
            password_only_production_risk_accepted=True,
            password_blocklist_path=str(Path("tests/fixtures/password-blocklist.txt").resolve()),
        )
