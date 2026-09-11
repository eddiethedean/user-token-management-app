"""Owner-scoped connector catalog access coverage."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock

from app.connectors.base import RemoteNamespace
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
    monkeypatch.setattr(catalogs, "connector_for", lambda provider: connector)
    user = SimpleNamespace(id="user-1")
    settings = SimpleNamespace(is_demo_mode=False, pipeline_catalog_ttl_seconds=300)

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
