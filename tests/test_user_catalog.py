"""Owner-scoped connector catalog access coverage."""

from __future__ import annotations

from types import SimpleNamespace
from typing import cast
from unittest.mock import Mock

import pytest

from app.config import Settings
from app.connectors.base import ProviderCapabilities, RemoteNamespace
from app.connectors.errors import ConnectorError
from app.models import User
from app.services import catalogs, secrets


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
        access.inspect_object("provider", Mock())
    db.scalar.assert_not_called()
