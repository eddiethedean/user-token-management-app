"""Owner-scoped connector catalog access coverage."""

from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import Mock

import pytest
from sqlalchemy import select

from app.config import Settings
from app.connectors.base import ProviderCapabilities, RemoteNamespace
from app.connectors.errors import ConnectorError
from app.connectors.locators import postgres_table
from app.database import SessionLocal
from app.infrastructure.persistence.catalog_cache import SqlAlchemyCatalogCache
from app.models import PipelineCatalogCache, User, utcnow
from app.services import catalogs, secrets
from app.services.secrets import delete_user_secret, store_user_credentials


def test_real_catalog_decrypts_owner_credentials_for_connector(monkeypatch) -> None:
    db = Mock()
    db.scalar.side_effect = [None, None]
    credentials = {"host": "postgres.internal", "username": "owner"}
    decrypt = Mock(return_value=credentials)
    connector = Mock()
    connector.list_namespaces.return_value = [
        RemoteNamespace(name="public", display_name="public", kind="schema")
    ]
    monkeypatch.setattr(secrets, "decrypt_user_credentials_for_run", decrypt)
    monkeypatch.setattr(catalogs, "catalog_browser_for", lambda provider: connector)
    user = cast(User, SimpleNamespace(id="user-1"))
    settings = cast(Settings, SimpleNamespace(is_demo_mode=False, pipeline_catalog_ttl_seconds=300))

    access = catalogs.UserCatalog(db, settings, user)
    items = access.list_namespaces("postgres")

    assert items[0].name == "public"
    decrypt.assert_called_once_with(
        db,
        settings,
        user=user,
        provider="postgres",
        request=None,
        purpose="catalog",
    )
    connector.list_namespaces.assert_called_once_with(credentials)
    db.add.assert_called_once()
    db.commit.assert_called_once()


def test_demo_catalog_passes_saved_credentials_to_the_emulator(monkeypatch) -> None:
    db = Mock()
    db.scalar.side_effect = [None, None]
    db.scalars.return_value.all.return_value = []
    credentials = {
        "endpoint": "https://mss.demo.invalid",
        "token": "demo-token",
        "branch": "release",
    }
    decrypt = Mock(return_value=credentials)
    connector = Mock()
    connector.list_namespaces.return_value = []
    monkeypatch.setattr(secrets, "decrypt_user_credentials_for_run", decrypt)
    monkeypatch.setattr(catalogs, "catalog_browser_for", lambda provider: connector)
    user = cast(User, SimpleNamespace(id="user-1"))
    settings = cast(Settings, SimpleNamespace(is_demo_mode=True, pipeline_catalog_ttl_seconds=300))

    access = catalogs.UserCatalog(db, settings, user)
    access.list_namespaces("mss")

    connector.list_namespaces.assert_called_once_with(credentials)
    assert access.default_branch("mss") == "release"
    decrypt.assert_called_once()


def test_demo_catalog_cache_reset_is_committed() -> None:
    db = Mock()
    db.execute.return_value.rowcount = 2

    assert catalogs.clear_demo_catalog_cache(db) == 2
    db.execute.assert_called_once()
    db.commit.assert_called_once()


def test_catalog_rejects_disabled_optional_metadata_before_decrypting() -> None:
    db = Mock()
    inspector = Mock()
    inspector.capabilities = ProviderCapabilities(
        provider="provider",
        label="Provider",
        technology="test",
        mark="TST",
        source=True,
        destination=False,
        object_model="object",
        write_modes=(),
        namespaces_label="Namespace",
        objects_label="Object",
        schema_inspection=False,
        exact_row_counts=False,
    )
    access = catalogs.UserCatalog(
        db,
        cast(Settings, SimpleNamespace(is_demo_mode=False, pipeline_catalog_ttl_seconds=300)),
        cast(User, SimpleNamespace(id="user-1")),
        schema_resolver=lambda _provider: inspector,
    )

    with pytest.raises(ConnectorError, match="schema inspection"):
        access.inspect_object("provider", postgres_table("public", "events"))
    db.scalar.assert_not_called()


def test_catalog_rejects_invalid_locator_before_resolving_credentials() -> None:
    db = Mock()
    inspector = Mock()
    inspector.capabilities = ProviderCapabilities(
        provider="provider",
        label="Provider",
        technology="test",
        mark="TST",
        source=True,
        destination=False,
        object_model="object",
        write_modes=(),
        namespaces_label="Namespace",
        objects_label="Object",
        schema_inspection=True,
        exact_row_counts=True,
    )
    credentials = Mock()
    access = catalogs.UserCatalog(
        db,
        cast(Settings, SimpleNamespace(is_demo_mode=False, pipeline_catalog_ttl_seconds=300)),
        cast(User, SimpleNamespace(id="user-1")),
        schema_resolver=lambda _provider: inspector,
        credential_resolver=credentials,
    )

    with pytest.raises(ValueError):
        access.inspect_object("provider", cast(Any, "not-a-locator"))
    credentials.assert_not_called()
    inspector.inspect_object.assert_not_called()


def test_catalog_revalidates_mutated_locator_before_resolving_connector() -> None:
    db = Mock()
    inspector = Mock()
    inspector.capabilities = ProviderCapabilities(
        provider="provider",
        label="Provider",
        technology="test",
        mark="TST",
        source=True,
        destination=False,
        object_model="object",
        write_modes=(),
        namespaces_label="Namespace",
        objects_label="Object",
        schema_inspection=True,
        exact_row_counts=True,
    )
    resolver = Mock(return_value=inspector)
    access = catalogs.UserCatalog(
        db,
        cast(Settings, SimpleNamespace(is_demo_mode=False, pipeline_catalog_ttl_seconds=300)),
        cast(User, SimpleNamespace(id="user-1")),
        schema_resolver=resolver,
    )
    locator = postgres_table("public", "events")
    locator.table = "invalid table name"

    with pytest.raises(ValueError):
        access.inspect_object("provider", locator)
    resolver.assert_not_called()


def test_catalog_cache_is_scoped_expiring_and_invalidated_by_real_credentials(
    access_app, make_user
) -> None:
    del access_app
    from app.config import get_settings

    user_two = make_user("catalog-owner@example.gov")
    settings = get_settings()
    credentials = {
        "host": "db.example.internal",
        "port": "5432",
        "database": "analytics",
        "username": "catalog-user",
        "password": "catalog-secret",
        "sslmode": "require",
    }

    with SessionLocal() as db:
        user_one = db.scalar(select(User).where(User.email == "admin@example.gov"))
        assert user_one is not None
        store_user_credentials(
            db,
            settings,
            user=user_one,
            provider="postgres",
            credentials=credentials,
        )
        cache_one = SqlAlchemyCatalogCache(db, settings, user_one)
        cache_two = SqlAlchemyCatalogCache(db, settings, user_two)

        cache_one.put("postgres", "public", {"items": [{"name": "owner-one"}]})
        cache_one.put("postgres", "analytics", {"items": [{"name": "namespace-one"}]})
        cache_one.put("mss", "public", {"items": [{"name": "mss-one"}]})
        cache_two.put("postgres", "public", {"items": [{"name": "owner-two"}]})

        assert cache_one.get("postgres", "public") == {"items": [{"name": "owner-one"}]}
        assert cache_one.get("postgres", "analytics") == {"items": [{"name": "namespace-one"}]}
        assert cache_one.get("mss", "public") == {"items": [{"name": "mss-one"}]}
        assert cache_two.get("postgres", "public") == {"items": [{"name": "owner-two"}]}
        stored = db.scalar(
            select(PipelineCatalogCache).where(
                PipelineCatalogCache.user_id == user_one.id,
                PipelineCatalogCache.provider == "postgres",
                PipelineCatalogCache.namespace == "public",
            )
        )
        assert stored is not None
        assert "catalog-secret" not in stored.payload_json
        assert "db.example.internal" not in stored.payload_json

        stored.expires_at = utcnow() - timedelta(seconds=1)
        db.commit()
        assert cache_one.get("postgres", "public") is None
        assert cache_one.get("postgres", "analytics") == {"items": [{"name": "namespace-one"}]}

        cache_one.put("postgres", "public", {"items": [{"name": "before-replace"}]})
        cache_one.put("mss", "public", {"items": [{"name": "keep-mss"}]})
        store_user_credentials(
            db,
            settings,
            user=user_one,
            provider="postgres",
            credentials={**credentials, "database": "replacement"},
        )
        assert cache_one.get("postgres", "public") is None
        assert cache_one.get("postgres", "analytics") is None
        assert cache_one.get("mss", "public") == {"items": [{"name": "keep-mss"}]}

        cache_one.put("postgres", "public", {"items": [{"name": "before-delete"}]})
        assert delete_user_secret(db, user=user_one, provider="postgres") is True
        assert cache_one.get("postgres", "public") is None
        assert cache_two.get("postgres", "public") == {"items": [{"name": "owner-two"}]}
