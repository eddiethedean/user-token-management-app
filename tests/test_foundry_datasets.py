"""Credential-generation checks for Foundry dataset provisioning."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Literal
from unittest.mock import Mock

import pytest
from sqlalchemy import select

from app.config import get_settings
from app.connectors.base import ConnectionHealth, ProvisionedDataset
from app.connectors.errors import ConnectorError, TransferErrorCode
from app.database import SessionLocal
from app.models import FoundryDataset, User, UserSecret
from app.services import foundry_datasets
from app.services.secrets import (
    delete_user_secret,
    store_user_credentials,
)
from app.services.secrets import (
    test_user_connection as check_user_connection,
)


@pytest.mark.parametrize("change", ["replace", "delete"])
def test_dataset_creation_discards_results_for_changed_credentials(
    access_app, make_user, monkeypatch, change: Literal["replace", "delete"]
) -> None:
    user = make_user("dataset-generation@example.gov")
    settings = get_settings()
    old_credentials = {"endpoint": "https://old.demo.invalid", "token": "old-token"}
    new_credentials = {"endpoint": "https://new.demo.invalid", "token": "new-token"}
    with SessionLocal() as db:
        owner = db.get(User, user.id)
        assert owner is not None
        store_user_credentials(
            db,
            settings,
            user=owner,
            provider="mss",
            credentials=old_credentials,
        )
        successful_check = Mock()
        successful_check.test_connection.return_value = ConnectionHealth(
            status="connected", message="Old endpoint connected", latency_ms=1
        )
        check_user_connection(
            db,
            user=owner,
            provider="mss",
            settings=settings,
            connector_resolver=lambda _: successful_check,
        )

    class InterleavedProvisioner:
        capabilities = SimpleNamespace(dataset_creation=True)

        def create_dataset(
            self, credentials, *, parent_folder_rid: str, name: str
        ) -> ProvisionedDataset:
            assert credentials["endpoint"] == old_credentials["endpoint"]
            with SessionLocal() as other:
                current_owner = other.get(User, user.id)
                assert current_owner is not None
                if change == "replace":
                    store_user_credentials(
                        other,
                        settings,
                        user=current_owner,
                        provider="mss",
                        credentials=new_credentials,
                    )
                    failed_check = Mock()
                    failed_check.test_connection.return_value = ConnectionHealth(
                        status="failed", message="Replacement endpoint failed", latency_ms=1
                    )
                    check_user_connection(
                        other,
                        user=current_owner,
                        provider="mss",
                        settings=settings,
                        connector_resolver=lambda _: failed_check,
                    )
                else:
                    assert delete_user_secret(
                        other,
                        user=current_owner,
                        provider="mss",
                        settings=settings,
                    )
            return ProvisionedDataset(
                dataset_rid="ri.foundry.main.dataset.synthetic-created",
                name=name,
                parent_folder_rid=parent_folder_rid,
            )

    monkeypatch.setattr(
        foundry_datasets,
        "dataset_provisioner_for",
        lambda _: InterleavedProvisioner(),
    )
    with SessionLocal() as db:
        owner = db.get(User, user.id)
        assert owner is not None
        with pytest.raises(ConnectorError) as exc:
            foundry_datasets.create_foundry_dataset(
                db,
                settings,
                user=owner,
                provider="mss",
                parent_folder_rid="ri.compass.main.folder.synthetic",
                name="Audit dataset",
                writer_policy=lambda _: True,
            )
    assert exc.value.code == TransferErrorCode.CREDENTIALS_STALE

    with SessionLocal() as db:
        dataset = db.scalar(
            select(FoundryDataset).where(
                FoundryDataset.user_id == user.id,
                FoundryDataset.dataset_rid == "ri.foundry.main.dataset.synthetic-created",
            )
        )
        assert dataset is None
        current_secret = db.scalar(
            select(UserSecret).where(
                UserSecret.user_id == user.id,
                UserSecret.provider == "mss",
            )
        )
        if change == "replace":
            assert current_secret is not None
            assert current_secret.validation_status == "failed"
        else:
            assert current_secret is None
