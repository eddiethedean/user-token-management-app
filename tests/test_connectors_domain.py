"""Unit tests for locator contracts, redaction, TLS, and transfer settings."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any, cast

import pytest
from pydantic import ValidationError

from app.config import Settings
from app.connectors.base import (
    BatchWriteResult,
    CatalogBrowser,
    CatalogPage,
    ConnectionHealth,
    Credentials,
    DestinationManifest,
    DestinationWriter,
    LoadSession,
    ObjectSchema,
    ProviderCapabilities,
    ProvisionedDataset,
    RemoteNamespace,
    SourceReader,
    TransferBatch,
)
from app.connectors.csv_source import CsvSourceConnector
from app.connectors.errors import ConnectorError
from app.connectors.locators import (
    FoundryDatasetFilesLocator,
    Locator,
    PostgresTableLocator,
    WritePolicy,
    parse_locator,
    parse_snapshot,
    parse_write_policy,
)
from app.connectors.redaction import SENTINEL_REPLACEMENT, redact_mapping, redact_text
from app.connectors.registry import ConnectorRegistry
from app.connectors.tls import apply_internal_ca_fix, ssl_context_for_bundle


class _SourceOnly:
    capabilities = ProviderCapabilities(
        provider="source-only",
        label="Source only",
        technology="test",
        mark="SRC",
        source=True,
        destination=False,
        object_model="test object",
        write_modes=(),
        namespaces_label="Namespace",
        objects_label="Object",
    )

    def test_connection(self, credentials) -> ConnectionHealth:
        return ConnectionHealth(status="connected", message="ok", latency_ms=0)

    def list_namespaces(self, credentials) -> list[RemoteNamespace]:
        return []

    def list_objects(self, credentials, namespace, cursor=None) -> CatalogPage:
        return CatalogPage(items=())

    def inspect_object(self, credentials, locator) -> ObjectSchema:
        raise NotImplementedError

    def count_rows(self, credentials, locator) -> int | None:
        return None

    def extract(self, credentials, locator, *, batch_rows, batch_bytes):
        yield from ()

    def create_dataset(self, credentials, *, parent_folder_rid, name) -> ProvisionedDataset:
        raise NotImplementedError


class _WriteOnlyDestination:
    capabilities = ProviderCapabilities(
        provider="write-only",
        label="Write only",
        technology="test",
        mark="WRT",
        source=False,
        destination=True,
        object_model="test object",
        write_modes=("append",),
        namespaces_label="Namespace",
        objects_label="Object",
    )

    def test_connection(self, credentials: Credentials) -> ConnectionHealth:
        return ConnectionHealth("connected", "ok", 0)

    def prepare_destination(
        self,
        credentials: Credentials,
        locator: Locator,
        schema: ObjectSchema,
        write_policy: WritePolicy,
        *,
        run_id: str,
    ) -> LoadSession:
        return LoadSession(locator=locator, write_policy=write_policy)

    def write_batch(self, load_session: LoadSession, batch: TransferBatch) -> BatchWriteResult:
        return BatchWriteResult(batch.row_count, batch.byte_count)

    def finalize(self, load_session: LoadSession) -> DestinationManifest:
        return DestinationManifest(locator=load_session.locator, rows=0, bytes=0)

    def abort(self, load_session: LoadSession) -> None:
        return None


class _BrowseOnly:
    capabilities = ProviderCapabilities(
        provider="browse-only",
        label="Browse only",
        technology="test",
        mark="BRW",
        source=False,
        destination=False,
        object_model="test object",
        write_modes=(),
        namespaces_label="Namespace",
        objects_label="Object",
        schema_inspection=False,
        exact_row_counts=False,
    )

    def list_namespaces(self, credentials: Credentials) -> list[RemoteNamespace]:
        return []

    def list_objects(
        self,
        credentials: Credentials,
        namespace: str,
        cursor: str | None = None,
    ) -> CatalogPage:
        return CatalogPage(items=())

    def inspect_object(self, credentials: Credentials, locator: Locator) -> ObjectSchema:
        return ObjectSchema(locator=locator, columns=())

    def count_rows(self, credentials: Credentials, locator: Locator) -> int | None:
        return None


class _ExtractionOnlySource:
    capabilities = ProviderCapabilities(
        provider="extract-only",
        label="Extract only",
        technology="test",
        mark="EXT",
        source=True,
        destination=False,
        object_model="test object",
        write_modes=(),
        namespaces_label="Namespace",
        objects_label="Object",
        schema_inspection=True,
        exact_row_counts=False,
    )

    def test_connection(self, credentials: Credentials) -> ConnectionHealth:
        return ConnectionHealth("connected", "ok", 0)

    def inspect_object(self, credentials: Credentials, locator: Locator) -> ObjectSchema:
        return ObjectSchema(locator=locator, columns=())

    def extract(
        self,
        credentials: Credentials,
        locator: Locator,
        *,
        batch_rows: int,
        batch_bytes: int,
    ) -> Iterator[TransferBatch]:
        yield from ()


def test_registry_rejects_unsupported_connector_roles() -> None:
    registry = ConnectorRegistry()
    registry.register(_SourceOnly)

    assert isinstance(registry.source_reader_for("source-only"), SourceReader)
    with pytest.raises(ConnectorError, match="destination writes"):
        registry.destination_writer_for("source-only")
    with pytest.raises(ConnectorError, match="dataset creation"):
        registry.dataset_provisioner_for("source-only")


def test_registry_respects_csv_destination_capability() -> None:
    registry = ConnectorRegistry()
    registry.register(CsvSourceConnector)

    assert isinstance(registry.source_reader_for("csv"), SourceReader)
    with pytest.raises(ConnectorError, match="destination writes"):
        registry.destination_writer_for("csv")


def test_registry_accepts_write_only_destination_adapter() -> None:
    connector = _WriteOnlyDestination()
    registry = ConnectorRegistry()
    registry.register(lambda: connector)

    assert isinstance(registry.destination_writer_for("write-only"), DestinationWriter)
    resolved = registry.connector_for("write-only")
    assert resolved is connector
    assert not hasattr(resolved, "extract")


def test_registry_accepts_browse_only_catalog_adapter() -> None:
    registry = ConnectorRegistry()
    registry.register(_BrowseOnly)

    assert isinstance(registry.catalog_browser_for("browse-only"), CatalogBrowser)
    with pytest.raises(ConnectorError, match="catalog browsing"):
        registry.catalog_reader_for("browse-only")
    with pytest.raises(ConnectorError, match="schema inspection"):
        registry.object_schema_inspector_for("browse-only")
    with pytest.raises(ConnectorError, match="row counting"):
        registry.row_counter_for("browse-only")


def test_registry_accepts_extraction_only_source_adapter() -> None:
    connector = _ExtractionOnlySource()
    registry = ConnectorRegistry()
    registry.register(lambda: connector)

    assert isinstance(registry.source_reader_for("extract-only"), SourceReader)


def test_postgres_and_foundry_locators_fail_closed() -> None:
    postgres = parse_locator({"kind": "postgres_table", "schema": "public", "table": "events"})
    assert isinstance(postgres, PostgresTableLocator)
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


def test_redactor_strips_api_key_and_non_postgres_dsn_credentials() -> None:
    text = redact_text(
        "api_key=api-canary mongodb://user:mongo-canary@db.example/app "
        "mysql://user:mysql-canary@db.example/app"
    )
    assert "api-canary" not in text
    assert "mongo-canary" not in text
    assert "mysql-canary" not in text


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

    fake.__dict__["add_nipr_ca"] = add_nipr_ca
    monkeypatch.setitem(sys.modules, "socom_ca_fix", fake)
    assert apply_internal_ca_fix() is True
    assert called["n"] == 1


def test_real_mode_allows_sqlite_for_session_scoped_workbench(tmp_path) -> None:
    spool = tmp_path / "spool"
    spool.mkdir()
    settings = cast(Any, Settings)(
        _env_file=None,
        app_env="development",
        data_mover_mode="real",
        database_url=f"sqlite:///{tmp_path / 'app.db'}",
        public_base_url="https://mover.example.gov",
        pipeline_spool_root=str(spool),
        pipeline_allowed_https_hosts="mss.example.gov",
        jwt_secret="workbench-jwt-secret-must-be-long-enough",
        session_pepper="workbench-session-pepper-must-be-long",
        csrf_secret="workbench-csrf-secret-must-be-long-enuf",
        api_token_encryption_keys={"workbench-v1": "AgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgI="},
        api_token_active_key_id="workbench-v1",
        cookie_secure=True,
    )
    assert settings.database_url.startswith("sqlite:")

    with pytest.raises(ValueError, match="strong real-mode secret"):
        cast(Any, Settings)(
            _env_file=None,
            app_env="development",
            data_mover_mode="real",
            database_url=f"sqlite:///{tmp_path / 'weak.db'}",
            public_base_url="https://mover.example.gov",
            pipeline_spool_root=str(spool),
            pipeline_allowed_https_hosts="mss.example.gov",
            cookie_secure=True,
        )

    with pytest.raises(ValueError, match="file-backed"):
        cast(Any, Settings)(
            _env_file=None,
            app_env="development",
            data_mover_mode="real",
            database_url="sqlite:///:memory:",
            public_base_url="https://mover.example.gov",
            pipeline_spool_root=str(spool),
            pipeline_allowed_https_hosts="mss.example.gov",
            jwt_secret="workbench-jwt-secret-must-be-long-enough",
            session_pepper="workbench-session-pepper-must-be-long",
            csrf_secret="workbench-csrf-secret-must-be-long-enuf",
            api_token_encryption_keys={
                "workbench-v1": "AgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgI="
            },
            api_token_active_key_id="workbench-v1",
            cookie_secure=True,
        )


def test_real_mode_requires_spool_and_allowlist(tmp_path) -> None:
    spool = tmp_path / "spool"
    spool.mkdir()
    with pytest.raises(ValueError, match="PIPELINE_ALLOWED_HTTPS_HOSTS"):
        cast(Any, Settings)(
            _env_file=None,
            app_env="development",
            data_mover_mode="real",
            database_url="postgresql+psycopg://mover:pass@localhost:5432/app",
            public_base_url="https://mover.example.gov",
            pipeline_spool_root=str(spool),
            jwt_secret="workbench-jwt-secret-must-be-long-enough",
            session_pepper="workbench-session-pepper-must-be-long",
            csrf_secret="workbench-csrf-secret-must-be-long-enuf",
            api_token_encryption_keys={
                "workbench-v1": "AgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgI="
            },
            api_token_active_key_id="workbench-v1",
            cookie_secure=True,
        )

    with pytest.raises(ValueError, match="absolute HTTPS URL"):
        cast(Any, Settings)(
            _env_file=None,
            app_env="development",
            data_mover_mode="real",
            database_url=f"sqlite:///{tmp_path / 'http.db'}",
            public_base_url="http://mover.example.gov",
            pipeline_spool_root=str(spool),
            pipeline_allowed_https_hosts="mss.example.gov",
            jwt_secret="workbench-jwt-secret-must-be-long-enough",
            session_pepper="workbench-session-pepper-must-be-long",
            csrf_secret="workbench-csrf-secret-must-be-long-enuf",
            api_token_encryption_keys={
                "workbench-v1": "AgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgI="
            },
            api_token_active_key_id="workbench-v1",
            cookie_secure=True,
        )


def test_production_real_mode_rejects_sqlite(tmp_path) -> None:
    spool = tmp_path / "spool"
    spool.mkdir()
    with pytest.raises(ValueError, match="PostgreSQL"):
        cast(Any, Settings)(
            _env_file=None,
            app_env="production",
            data_mover_mode="real",
            database_url=f"sqlite:///{tmp_path / 'app.db'}",
            pipeline_spool_root=str(spool),
            pipeline_allowed_https_hosts="mss.example.gov",
            public_base_url="https://mover.example.gov",
            cookie_secure=True,
            allowed_email_domains="example.gov",
            jwt_secret="production-jwt-secret-must-be-long-enough",
            session_pepper="production-session-pepper-must-be-long",
            csrf_secret="production-csrf-secret-must-be-long-enuf",
            email_backend="smtp",
            smtp_host="smtp.example.gov",
            email_redact_sent_bodies=True,
            password_only_production_risk_accepted=True,
            password_blocklist_path=str(Path("tests/fixtures/password-blocklist.txt").resolve()),
            api_token_encryption_keys={"prod-v1": "AQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQE="},
            api_token_active_key_id="prod-v1",
        )


def test_sqlite_worker_lock_prevents_duplicate_workers(tmp_path) -> None:
    from app.database import sqlite_worker_lock

    database_url = f"sqlite:///{tmp_path / 'app.db'}"
    with sqlite_worker_lock(database_url, "pipeline"):
        with pytest.raises(RuntimeError, match="already running"):
            with sqlite_worker_lock(database_url, "pipeline"):
                pass


def test_production_rejects_demo_mode() -> None:
    with pytest.raises(ValueError, match="DATA_MOVER_MODE must be real"):
        cast(Any, Settings)(
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
