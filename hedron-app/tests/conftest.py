"""Shared pytest fixtures for the Hedron Access Registry port."""

from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def hedron_app(tmp_path, monkeypatch):
    """Boot a fresh Hedron app against an isolated SQLite database."""
    db_path = tmp_path / "test.db"
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    monkeypatch.setenv("PUBLIC_BASE_URL", "http://testserver")
    monkeypatch.setenv("JWT_SECRET", "test-jwt-secret-at-least-32-bytes-long!!")
    monkeypatch.setenv("SESSION_PEPPER", "test-session-pepper-at-least-32-bytes!!")
    monkeypatch.setenv("CSRF_SECRET", "test-csrf-secret-at-least-32-bytes-long!")
    monkeypatch.setenv(
        "API_TOKEN_ENCRYPTION_KEYS",
        '{"development-v1":"AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="}',
    )
    monkeypatch.setenv("API_TOKEN_ACTIVE_KEY_ID", "development-v1")
    monkeypatch.setenv("ALLOWED_EMAIL_DOMAINS", "example.gov")
    monkeypatch.setenv("AUTHENTICATION_MODE", "local_password")
    monkeypatch.setenv("COOKIE_SECURE", "false")
    monkeypatch.setenv("EMAIL_BACKEND", "console")

    from access_registry.config import get_settings

    get_settings.cache_clear()
    from access_registry.schema import upgrade_schema

    upgrade_schema()

    from access_registry.cli import create_admin
    from access_registry.main import app

    os.environ["ADMIN_BOOTSTRAP_PASSWORD"] = "Tr0pic-Maple!River92"
    assert create_admin("admin@example.gov", password="Tr0pic-Maple!River92") == 0

    yield app

    get_settings.cache_clear()


@pytest.fixture()
def client(hedron_app):
    with TestClient(hedron_app) as test_client:
        yield test_client
