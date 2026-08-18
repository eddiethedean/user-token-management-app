"""Unit tests for locator contracts, redaction, TLS, and transfer settings."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from app.config import Settings
from app.connectors.locators import (
    FoundryDatasetFilesLocator,
    parse_locator,
    parse_snapshot,
    parse_write_policy,
)
from app.connectors.redaction import SENTINEL_REPLACEMENT, redact_mapping, redact_text
from app.connectors.tls import apply_internal_ca_fix, ssl_context_for_bundle


def test_postgres_and_foundry_locators_fail_closed() -> None:
    postgres = parse_locator({"kind": "postgres_table", "schema": "public", "table": "events"})
    assert postgres.table == "events"
    with pytest.raises(ValidationError):
        parse_locator({"kind": "postgres_table", "schema": "public;drop", "table": "events"})
    with pytest.raises(ValidationError):
        parse_locator(
            {"kind": "foundry_dataset_files", "dataset_rid": "not-a-rid", "branch": "master"}
        )
    files = FoundryDatasetFilesLocator(
        dataset_rid="ri.foundry.main.dataset.example",
        branch="master",
        file_paths=["part-000.parquet"],
    )
    assert files.file_paths == ["part-000.parquet"]
    with pytest.raises(ValidationError):
        FoundryDatasetFilesLocator(
            dataset_rid="ri.foundry.main.dataset.example",
            branch="master",
            file_paths=["../secret.parquet"],
        )


def test_unknown_locator_kind_is_rejected() -> None:
    with pytest.raises(ValidationError):
        parse_locator({"kind": "mongodb_collection", "database": "ops", "collection": "events"})


def test_mongodb_is_not_a_secret_provider() -> None:
    from app.services.secrets import SECRET_PROVIDERS

    assert "mongodb" not in {provider.name for provider in SECRET_PROVIDERS}


def test_write_policies_and_snapshots_round_trip() -> None:
    policy = parse_write_policy(
        {"kind": "postgres_upsert", "conflict_columns": ["event_id"], "action": "ignore"}
    )
    snapshot = parse_snapshot(
        {
            "version": 2,
            "name": "MSS to warehouse",
            "source_provider": "mss",
            "destination_provider": "postgres",
            "source": {
                "kind": "foundry_dataset_files",
                "dataset_rid": "ri.foundry.main.dataset.example",
                "branch": "master",
                "file_paths": "all_supported",
            },
            "destination": {"kind": "postgres_table", "schema": "public", "table": "events"},
            "write_policy": policy.model_dump(),
        }
    )
    assert snapshot.write_policy.kind == "postgres_upsert"


def test_redactor_strips_tokens_passwords_and_dsns() -> None:
    text = redact_text(
        "Authorization: Bearer super-secret-token password=hunter2 "
        "postgresql://mover:hunter2@db.example/app"
    )
    assert "super-secret-token" not in text
    assert "hunter2" not in text
    assert SENTINEL_REPLACEMENT in text
    mapping = redact_mapping({"token": "abc", "nested": {"password": "x"}, "ok": "ready"})
    assert mapping["token"] == SENTINEL_REPLACEMENT
    assert mapping["nested"]["password"] == SENTINEL_REPLACEMENT
    assert mapping["ok"] == "ready"


def test_tls_adapter_loads_bundle_and_mocks_internal_ca(monkeypatch, tmp_path) -> None:
    import app.connectors.tls as tls

    monkeypatch.setattr(tls, "apply_internal_ca_fix", lambda: True)
    assert tls.apply_internal_ca_fix() is True
    missing = tmp_path / "missing.pem"
    with pytest.raises(tls.TlsBootstrapError):
        ssl_context_for_bundle(str(missing))
    ssl_context_for_bundle.cache_clear()
    default = ssl_context_for_bundle("")
    assert default is not None


def test_apply_internal_ca_fix_is_optional(monkeypatch) -> None:
    import sys
    from types import ModuleType

    fake = ModuleType("socom_ca_fix")
    called = {"n": 0}

    def add_nipr_ca() -> None:
        called["n"] += 1

    fake.add_nipr_ca = add_nipr_ca
    monkeypatch.setitem(sys.modules, "socom_ca_fix", fake)
    assert apply_internal_ca_fix() is True
    assert called["n"] == 1


def test_real_mode_rejects_sqlite_and_empty_allowlist(tmp_path) -> None:
    with pytest.raises(ValueError, match="PostgreSQL application database"):
        Settings(
            _env_file=None,
            app_env="development",
            data_mover_mode="real",
            database_url=f"sqlite:///{tmp_path / 'app.db'}",
        )
    spool = tmp_path / "spool"
    spool.mkdir()
    with pytest.raises(ValueError, match="PIPELINE_ALLOWED_HTTPS_HOSTS"):
        Settings(
            _env_file=None,
            app_env="development",
            data_mover_mode="real",
            database_url="postgresql+psycopg://mover:pass@localhost:5432/app",
            pipeline_spool_root=str(spool),
        )


def test_production_rejects_demo_mode() -> None:
    with pytest.raises(ValueError, match="DATA_MOVER_MODE must be real"):
        Settings(
            _env_file=None,
            app_env="production",
            data_mover_mode="demo",
            public_base_url="https://mover.example.gov",
            cookie_secure=True,
            allowed_email_domains="example.gov",
            jwt_secret="production-jwt-secret-must-be-long-enough",
            session_pepper="production-session-pepper-must-be-long",
            csrf_secret="production-csrf-secret-must-be-long-enuf",
            database_url="postgresql+psycopg://mover:pass@localhost:5432/app",
            email_backend="smtp",
            smtp_host="smtp.example.gov",
            email_redact_sent_bodies=True,
            password_only_production_risk_accepted=True,
            password_blocklist_path=str(Path("tests/fixtures/password-blocklist.txt").resolve()),
            api_token_encryption_keys={"prod-v1": "AQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQE="},
            api_token_active_key_id="prod-v1",
        )
