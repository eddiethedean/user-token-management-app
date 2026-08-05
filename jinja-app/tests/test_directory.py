import asyncio
from concurrent.futures import ThreadPoolExecutor

import httpx2
import pytest
import starlette.testclient
from sqlalchemy import select

from app.config import Settings, get_settings
from app.database import SessionLocal
from app.models import User
from app.services.directory import (
    DirectoryEligibilityError,
    DirectoryUnavailableError,
    validate_directory_email,
)


def test_starlette_and_directory_use_httpx2() -> None:
    assert starlette.testclient.httpx is httpx2


def directory_settings(**updates) -> Settings:
    values = {
        "directory_lookup_url": "https://directory.example.gov/lookup",
        "directory_lookup_required": True,
        **updates,
    }
    return get_settings().model_copy(update=values)


def run_lookup(response_handler, **settings_updates):
    transport = httpx2.MockTransport(response_handler)
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(
            asyncio.run,
            validate_directory_email(
                "person@example.gov",
                directory_settings(**settings_updates),
                transport=transport,
            ),
        )
        return future.result()


def test_directory_accepts_matching_generic_and_attribute_responses() -> None:
    generic = run_lookup(
        lambda request: httpx2.Response(
            200,
            json={"email": "PERSON@example.gov", "display_name": "Government Person"},
        )
    )
    attributes = run_lookup(
        lambda request: httpx2.Response(
            200,
            json={"attributes": {"mail": ["person@example.gov"], "displayName": ["Person"]}},
        )
    )
    assert generic is not None and generic.email == "person@example.gov"
    assert generic.display_name == "Government Person"
    assert attributes is not None and attributes.display_name == "Person"


def test_directory_rejects_not_found_and_mismatched_addresses() -> None:
    with pytest.raises(DirectoryEligibilityError, match="not found"):
        run_lookup(lambda request: httpx2.Response(404))
    with pytest.raises(DirectoryEligibilityError, match="did not confirm"):
        run_lookup(lambda request: httpx2.Response(200, json={"email": "someone.else@example.gov"}))


def test_directory_outage_can_fail_open_or_fail_closed() -> None:
    def unavailable(request):
        return httpx2.Response(503)

    assert run_lookup(unavailable, directory_lookup_required=False) is None
    with pytest.raises(DirectoryUnavailableError, match="unavailable"):
        run_lookup(unavailable, directory_lookup_required=True)


def test_directory_request_uses_query_and_bearer_token() -> None:
    observed = {}

    def handler(request):
        observed["query"] = request.url.params["query"]
        observed["authorization"] = request.headers.get("authorization")
        return httpx2.Response(200, json={"email": "person@example.gov"})

    result = run_lookup(handler, directory_lookup_bearer_token="directory-secret")
    assert result is not None
    assert observed == {
        "query": "person@example.gov",
        "authorization": "Bearer directory-secret",
    }


def test_production_directory_requires_https_and_tls_verification() -> None:
    common = {
        "app_env": "production",
        "jwt_secret": "j" * 32,
        "session_pepper": "s" * 32,
        "csrf_secret": "c" * 32,
        "cookie_secure": True,
        "allowed_email_domains": "example.gov",
        "public_base_url": "https://registry.example.gov",
        "database_url": "postgresql+psycopg://registry@db.example.gov/registry",
        "email_backend": "smtp",
        "smtp_host": "relay.example.gov",
        "smtp_starttls": True,
        "email_redact_sent_bodies": True,
        "password_only_production_risk_accepted": True,
        "password_blocklist_path": "tests/fixtures/password-blocklist.txt",
    }
    with pytest.raises(ValueError, match="must use HTTPS"):
        Settings(**common, directory_lookup_url="http://directory.example.gov")
    with pytest.raises(ValueError, match="VERIFY_TLS"):
        Settings(
            **common,
            directory_lookup_url="https://directory.example.gov",
            directory_lookup_verify_tls=False,
        )


@pytest.mark.parametrize(
    ("module_path", "request_path", "payload", "content_type"),
    [
        (
            "app.routes.web.validate_directory_email",
            "/register",
            {"email": "denied@example.gov", "full_name": "Denied Person"},
            "text/html",
        ),
        (
            "app.routes.api.validate_directory_email",
            "/api/v1/auth/register",
            {"email": "denied@example.gov", "full_name": "Denied Person"},
            "application/json",
        ),
    ],
)
def test_enrollment_routes_reject_directory_mismatch_without_creating_account(
    client, monkeypatch, module_path, request_path, payload, content_type
) -> None:
    async def reject_directory(email, settings):
        raise DirectoryEligibilityError("The government directory did not confirm that address.")

    monkeypatch.setattr(module_path, reject_directory)
    if content_type == "application/json":
        response = client.post(request_path, json=payload)
    else:
        response = client.post(request_path, data=payload)

    assert response.status_code == 400
    assert "did not confirm" in response.text
    with SessionLocal() as db:
        assert db.scalar(select(User).where(User.email == "denied@example.gov")) is None
