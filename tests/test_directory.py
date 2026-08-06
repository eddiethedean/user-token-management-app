"""Unit tests for government directory lookup fail-open / fail-closed behavior."""

from __future__ import annotations

import asyncio
import json

import httpx2
import pytest

from app.config import get_settings
from app.services.directory import (
    DirectoryEligibilityError,
    DirectoryUnavailableError,
    validate_directory_email,
)


def _run(coro):
    return asyncio.run(coro)


def _json_transport(
    *, status_code: int = 200, payload: object | None = None
) -> httpx2.MockTransport:
    body = json.dumps(payload if payload is not None else {}).encode()

    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(status_code, content=body, request=request)

    return httpx2.MockTransport(handler)


@pytest.fixture()
def settings(access_app, monkeypatch):
    monkeypatch.setenv("DIRECTORY_LOOKUP_URL", "https://directory.example.gov/lookup")
    monkeypatch.setenv("ALLOWED_EMAIL_DOMAINS", "example.gov")
    get_settings.cache_clear()
    return get_settings()


def test_no_directory_url_returns_none(access_app, monkeypatch):
    monkeypatch.setenv("DIRECTORY_LOOKUP_URL", "")
    get_settings.cache_clear()
    result = _run(validate_directory_email("user@example.gov", get_settings()))
    assert result is None


def test_success_returns_record(settings):
    transport = _json_transport(
        payload={"email": "user@example.gov", "display_name": "User Example"}
    )
    record = _run(validate_directory_email("user@example.gov", settings, transport=transport))
    assert record is not None
    assert record.email == "user@example.gov"
    assert record.display_name == "User Example"


def test_not_found_raises_eligibility(settings):
    transport = _json_transport(status_code=404, payload={})
    with pytest.raises(DirectoryEligibilityError):
        _run(validate_directory_email("missing@example.gov", settings, transport=transport))


def test_fail_open_on_http_error(settings, monkeypatch):
    monkeypatch.setenv("DIRECTORY_LOOKUP_REQUIRED", "false")
    get_settings.cache_clear()
    settings = get_settings()

    def handler(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("down", request=request)

    result = _run(
        validate_directory_email(
            "user@example.gov", settings, transport=httpx2.MockTransport(handler)
        )
    )
    assert result is None


def test_fail_closed_on_http_error(settings, monkeypatch):
    monkeypatch.setenv("DIRECTORY_LOOKUP_REQUIRED", "true")
    get_settings.cache_clear()
    settings = get_settings()

    def handler(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("down", request=request)

    with pytest.raises(DirectoryUnavailableError):
        _run(
            validate_directory_email(
                "user@example.gov", settings, transport=httpx2.MockTransport(handler)
            )
        )


def test_email_mismatch_raises_eligibility(settings):
    transport = _json_transport(payload={"email": "other@example.gov"})
    with pytest.raises(DirectoryEligibilityError):
        _run(validate_directory_email("user@example.gov", settings, transport=transport))


def test_invalid_json_fail_closed(settings, monkeypatch):
    monkeypatch.setenv("DIRECTORY_LOOKUP_REQUIRED", "true")
    get_settings.cache_clear()
    settings = get_settings()

    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, content=b"not-json", request=request)

    with pytest.raises(DirectoryUnavailableError):
        _run(
            validate_directory_email(
                "user@example.gov", settings, transport=httpx2.MockTransport(handler)
            )
        )
