"""Advisory expiry parsing, provenance, persistence, and owner-visible UI."""

from __future__ import annotations

import base64
import hashlib
import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import httpx2
import pytest

from app.config import get_settings
from app.connectors.base import ConnectionHealth
from app.connectors.databricks import DatabricksConnector
from app.database import SessionLocal
from app.domain.token_expiry import TokenExpiry, expiry_from_credentials, parse_expiry_input
from app.models import User
from app.services.secret_catalog import SECRET_CATALOG
from app.services.secret_validation import CredentialValidator
from app.services.secrets import (
    store_user_credentials,
    store_user_credentials_if_changed,
)
from app.services.secrets import (
    test_user_connection as check_connection,
)
from app.ui.presenters.token_expiry import token_expiry_view
from tests.helpers import USER_PASSWORD, csrf_from, web_login


def _jwt(claims: object) -> str:
    payload = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    return f"eyJhbGciOiJIUzI1NiJ9.{payload}.synthetic-signature"


def _databricks_credentials() -> dict[str, str]:
    return {
        "workspace_url": "https://dbc-12345678.cloud.databricks.com",
        "token": "synthetic-databricks-token",
        "compute_kind": "warehouse",
        "compute_id": "warehouse-1",
        "http_path": "sql/1.0/warehouses/warehouse-1",
    }


def _settings(mode="real"):
    return get_settings().model_copy(
        update={
            "data_mover_mode": mode,
            "pipeline_allowed_https_hosts": "dbc-12345678.cloud.databricks.com",
        }
    )


def test_expiry_input_normalizes_utc_and_manual_date_overrides_unverified_claim() -> None:
    credentials = {
        "endpoint": "https://mss.example",
        "token": _jwt({"exp": 1893456000}),
        "token_expires_at": "2031-01-01T05:30:00+05:30",
    }
    normalized = CredentialValidator().validate(SECRET_CATALOG.require("mss"), credentials)
    assert normalized["token_expires_at"] == "2031-01-01T00:00:00"
    assert expiry_from_credentials(normalized) == TokenExpiry(datetime(2031, 1, 1), "user")
    assert expiry_from_credentials({"token": credentials["token"]}) == TokenExpiry(
        datetime(2030, 1, 1), "token_claim"
    )
    assert parse_expiry_input("2031-01-01T00:00") == datetime(2031, 1, 1)


@pytest.mark.parametrize("value", ["2030-01-01", "tomorrow", "2030-02-30T00:00", ""])
def test_invalid_manual_expiry_is_rejected_without_echoing_input(value: str) -> None:
    with pytest.raises(ValueError, match="valid date and time in UTC") as error:
        parse_expiry_input(value)
    if value:
        assert value not in str(error.value)


@pytest.mark.parametrize(
    "claims",
    [
        {},
        [],
        {"exp": True},
        {"exp": "1893456000"},
        {"exp": None},
        {"exp": 1e300},
        {"exp": float("nan")},
        {"exp": float("inf")},
        {"exp": 10**1000},
    ],
)
def test_invalid_claims_leave_expiry_unknown(claims: object) -> None:
    assert expiry_from_credentials({"token": _jwt(claims)}) == TokenExpiry()


@pytest.mark.parametrize(
    "token", ["opaque-api-token", "a.!!!.c", "a._w.c", "a.e30.c.d", "a" * 8193]
)
def test_opaque_or_malformed_tokens_leave_expiry_unknown(token: str) -> None:
    assert expiry_from_credentials({"token": token}) == TokenExpiry()
    assert expiry_from_credentials({"password": "opaque-password"}) == TokenExpiry()


def test_deeply_nested_token_payload_cannot_crash_credential_saving() -> None:
    payload = base64.urlsafe_b64encode(("[" * 1500 + "]" * 1500).encode()).decode().rstrip("=")
    assert expiry_from_credentials({"token": f"header.{payload}.signature"}) == TokenExpiry()


@pytest.mark.parametrize(
    ("expiry", "badge", "tone"),
    [
        (timedelta(seconds=-1), "Expiry date passed", "danger"),
        (timedelta(), "Expiry date passed", "danger"),
        (timedelta(days=7), "Expires within 7 days", "warning"),
        (timedelta(days=7, seconds=1), "", "neutral"),
    ],
)
def test_expiry_warning_boundaries(expiry: timedelta, badge: str, tone: str) -> None:
    now = datetime(2030, 1, 1, tzinfo=UTC)
    secret = SimpleNamespace(token_expires_at=now + expiry, token_expiry_source="token_claim")
    view = token_expiry_view(secret, now=now)
    assert view.badge == badge
    assert view.tone == tone
    assert "UTC (unverified token claim)" in view.summary


def test_unknown_and_no_scheduled_expiry_do_not_claim_validity() -> None:
    assert "Unknown" in token_expiry_view(SimpleNamespace()).summary
    view = token_expiry_view(SimpleNamespace(token_expiry_source="provider_no_expiry"))
    assert "No scheduled expiry reported by Databricks" in view.summary
    assert view.badge == ""


@pytest.mark.parametrize(
    ("milliseconds", "expected"),
    [
        (1893456000000, TokenExpiry(datetime(2030, 1, 1), "provider")),
        (-1, TokenExpiry(source="provider_no_expiry")),
        (0, TokenExpiry()),
        (True, TokenExpiry()),
        ("1893456000000", TokenExpiry()),
        (10**1000, TokenExpiry()),
    ],
)
def test_databricks_matches_current_token_and_uses_milliseconds(milliseconds, expected) -> None:
    token = "synthetic-databricks-token"
    records = [
        {"token_id": "unrelated", "expiry_time": 123},
        {"token_id": hashlib.sha256(token.encode()).hexdigest(), "expiry_time": milliseconds},
    ]
    workspace = SimpleNamespace(
        config=SimpleNamespace(host="https://workspace.invalid", workspace_id=None)
    )
    transport = httpx2.MockTransport(lambda _: httpx2.Response(200, json={"token_infos": records}))
    assert DatabricksConnector._token_expiry(workspace, token, transport=transport) == expected
    # A sole unrelated token must never be treated as the caller's token.
    assert (
        DatabricksConnector._token_expiry(workspace, "another-token", transport=transport)
        == TokenExpiry()
    )


def test_databricks_metadata_permission_failure_is_optional_and_not_logged(caplog) -> None:
    def denied(request):
        return httpx2.Response(403, json={"message": "sensitive-token-list-details"})

    workspace = SimpleNamespace(
        config=SimpleNamespace(host="https://workspace.invalid", workspace_id=None),
        warehouses=SimpleNamespace(get=lambda **_: SimpleNamespace(state="STOPPED")),
    )
    connector = DatabricksConnector(
        _settings(),
        workspace_client_factory=lambda **_: workspace,
        token_metadata_transport=httpx2.MockTransport(denied),
    )
    health = connector.test_connection(_databricks_credentials())
    assert health.status == "connected"
    assert health.token_expiry == TokenExpiry()
    assert "sensitive-token-list-details" not in caplog.text


@pytest.mark.parametrize("failure", ["throttled", "unavailable", "timeout", "redirect"])
def test_optional_expiry_makes_one_short_request_and_never_uses_sdk_retries(
    failure, caplog
) -> None:
    requests: list[httpx2.Request] = []

    def metadata(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        if failure == "timeout":
            raise httpx2.ReadTimeout("sensitive-token-list-details", request=request)
        code = {"throttled": 429, "unavailable": 503, "redirect": 302}[failure]
        return httpx2.Response(
            code, headers={"Retry-After": "600", "Location": "https://other.invalid"}
        )

    workspace = SimpleNamespace(
        config=SimpleNamespace(host="https://workspace.invalid", workspace_id=123),
        tokens=SimpleNamespace(list=lambda: pytest.fail("Optional metadata used SDK retries")),
        warehouses=SimpleNamespace(get=lambda **_: SimpleNamespace(state="STOPPED")),
    )
    transport = httpx2.MockTransport(metadata)
    connector = DatabricksConnector(
        _settings(),
        workspace_client_factory=lambda **_: workspace,
        token_metadata_transport=transport,
    )
    health = connector.test_connection(_databricks_credentials())
    assert health.status == "connected"
    assert health.runtime_status == "STOPPED"
    assert health.token_expiry == TokenExpiry()
    assert len(requests) == 1
    # Keep assertions outside the lookup's exception handler so failures reach pytest.
    request = requests[0]
    assert request.method == "GET"
    assert str(request.url) == "https://workspace.invalid/api/2.0/token/list"
    assert request.headers["Authorization"] == "Bearer synthetic-databricks-token"
    assert request.headers["X-Databricks-Workspace-Id"] == "123"
    assert request.extensions["timeout"] == dict.fromkeys(("connect", "read", "write", "pool"), 2.0)
    assert "sensitive-token-list-details" not in caplog.text
    assert "synthetic-databricks-token" not in caplog.text


@pytest.mark.parametrize(
    "body", [b"not-json", b"[]", b'{"token_infos":{}}', b"x" * (1024 * 1024 + 1)]
)
def test_optional_expiry_rejects_malformed_or_oversized_metadata(body) -> None:
    workspace = SimpleNamespace(
        config=SimpleNamespace(host="https://workspace.invalid", workspace_id=None)
    )
    assert (
        DatabricksConnector._token_expiry(
            workspace,
            "synthetic-token",
            transport=httpx2.MockTransport(lambda _: httpx2.Response(200, content=body)),
        )
        == TokenExpiry()
    )


@pytest.mark.parametrize("mode", ["real", "demo"])
def test_provider_expiry_updates_only_live_checks_and_rotation_clears_it(make_user, mode) -> None:
    user = make_user(f"expiry.{mode}@example.gov")
    settings = _settings(mode)
    credentials = {**_databricks_credentials(), "token_expires_at": "2031-01-01T00:00"}
    provider_date = datetime(2030, 1, 1)

    class Connector:
        capabilities = DatabricksConnector.capabilities

        def test_connection(self, credentials):
            return ConnectionHealth(
                "connected",
                "Synthetic check",
                0,
                token_expiry=TokenExpiry(provider_date, "provider"),
            )

    with SessionLocal() as db:
        owner = db.get(User, user.id)
        assert owner is not None
        stored = store_user_credentials(
            db, settings, user=owner, provider="databricks", credentials=credentials
        )
        assert stored.token_expiry_source == "user"
        checked = check_connection(
            db,
            user=owner,
            provider="databricks",
            settings=settings,
            connector_resolver=lambda _: Connector(),
        )
        assert checked.validation_status == "connected"
        assert checked.token_expiry_source == ("provider" if mode == "real" else "user")
        assert checked.token_expires_at == (
            provider_date if mode == "real" else datetime(2031, 1, 1)
        )
        unchanged = store_user_credentials_if_changed(
            db, settings, user=owner, provider="databricks", credentials=credentials
        )
        assert not unchanged.changed
        assert unchanged.secret.token_expiry_source == checked.token_expiry_source
        replacement = {**_databricks_credentials(), "token": "replacement-opaque-token"}
        rotated = store_user_credentials(
            db, settings, user=owner, provider="databricks", credentials=replacement
        )
        assert rotated.token_expiry_source == "unknown"
        assert rotated.token_expires_at is None


def test_inflight_check_cannot_overwrite_replacement_expiry(make_user) -> None:
    user = make_user("expiry.race@example.gov")
    settings = _settings()

    class Connector:
        capabilities = DatabricksConnector.capabilities

        def test_connection(self, credentials):
            with SessionLocal() as replacement_db:
                owner = replacement_db.get(User, user.id)
                assert owner is not None
                store_user_credentials(
                    replacement_db,
                    settings,
                    user=owner,
                    provider="databricks",
                    credentials={
                        **_databricks_credentials(),
                        "token": "replacement-token",
                        "token_expires_at": "2032-01-01T00:00",
                    },
                )
            return ConnectionHealth(
                "connected",
                "Superseded",
                0,
                token_expiry=TokenExpiry(datetime(2030, 1, 1), "provider"),
            )

    with SessionLocal() as db:
        owner = db.get(User, user.id)
        assert owner is not None
        store_user_credentials(
            db, settings, user=owner, provider="databricks", credentials=_databricks_credentials()
        )
        checked = check_connection(
            db,
            user=owner,
            provider="databricks",
            settings=settings,
            connector_resolver=lambda _: Connector(),
        )
        assert checked.token_expires_at == datetime(2032, 1, 1)
        assert checked.token_expiry_source == "user"
        assert checked.validation_status == "untested"


def test_retest_backfills_unverified_expiry_on_an_existing_connection(make_user) -> None:
    user = make_user("expiry.backfill@example.gov")
    token = _jwt({"exp": 1893456000})
    with SessionLocal() as db:
        owner = db.get(User, user.id)
        assert owner is not None
        stored = store_user_credentials(
            db,
            get_settings(),
            user=owner,
            provider="mss",
            credentials={"endpoint": "https://mss.example", "token": token},
        )
        stored.token_expires_at = None
        stored.token_expiry_source = "unknown"
        db.commit()
        checked = check_connection(db, user=owner, provider="mss", settings=get_settings())
        assert checked.token_expires_at == datetime(2030, 1, 1)
        assert checked.token_expiry_source == "token_claim"


def test_expiry_is_visible_only_to_owner_and_tokens_stay_hidden(client, make_user) -> None:
    user = make_user("expiry.owner@example.gov")
    other = make_user("expiry.other@example.gov")
    web_login(client, user.email, USER_PASSWORD)
    page = client.get("/security")
    assert 'type="datetime-local"' in page.text
    assert 'id="postgres-token_expires_at"' not in page.text
    token = _jwt({"exp": int((datetime.now(UTC) + timedelta(days=2)).timestamp())})
    saved = client.post(
        "/security/secrets/mss",
        data={
            "csrf_token": csrf_from(page.text),
            "endpoint": "https://mss.example",
            "token": token,
        },
        headers={"HX-Request": "true", "HX-Target": "secret-slot-mss"},
    )
    assert saved.status_code == 200
    assert "Expires within 7 days" in saved.text
    assert "unverified token claim" in saved.text
    assert token not in saved.text
    status = client.get("/security?tab=status")
    assert status.status_code == 200
    assert "Expires within 7 days" in status.text
    assert token not in status.text
    client.cookies.clear()
    web_login(client, other.email, USER_PASSWORD)
    other_page = client.get("/security?tab=status")
    assert "unverified token claim" not in other_page.text
    assert "Expires within 7 days" not in other_page.text
