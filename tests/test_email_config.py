"""Compatibility settings for the jwt-user-management email and directory interface."""

from __future__ import annotations

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
