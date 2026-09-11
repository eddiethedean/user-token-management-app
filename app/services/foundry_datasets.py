"""Owner-authorized provisioning of Foundry datasets."""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import cast

from fastapi import Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.connectors.base import ProvisionedDataset
from app.connectors.errors import ConnectorError, TransferErrorCode
from app.connectors.locators import DATASET_RID_PATTERN
from app.connectors.registry import capabilities_for, connector_for, writer_enabled
from app.models import FoundryDataset, User, UserSecret, new_id, utcnow
from app.services.audit import record_event
from app.services.secrets import decrypt_user_credentials_for_run

FOLDER_RID_PATTERN = re.compile(r"^ri\.[A-Za-z0-9._-]+\.[A-Za-z0-9._-]+\.folder\.[A-Za-z0-9._-]+$")
DATASET_NAME_MAX_LENGTH = 160


def normalize_dataset_name(value: str) -> str:
    name = " ".join(value.split())
    if not name or len(name) > DATASET_NAME_MAX_LENGTH:
        raise ValueError("Dataset names must contain 1–160 characters.")
    if name in {".", ".."} or "/" in name or "\\" in name:
        raise ValueError("Dataset names cannot be '.', '..', or contain slashes.")
    return name


def validate_parent_folder_rid(value: str) -> str:
    rid = value.strip()
    if not FOLDER_RID_PATTERN.fullmatch(rid):
        raise ValueError("Enter a valid Foundry folder RID.")
    return rid


def create_foundry_dataset(
    db: Session,
    settings: Settings,
    *,
    user: User,
    provider: str,
    parent_folder_rid: str,
    name: str,
    request: Request | None = None,
) -> FoundryDataset:
    provider_id = provider.casefold()
    try:
        capabilities = capabilities_for(provider_id)
    except ConnectorError as exc:
        raise ValueError("Select MSS or MCS-COP to create a dataset.") from exc
    if not capabilities.dataset_creation:
        raise ValueError("The selected destination does not support dataset creation.")
    if not writer_enabled(provider_id):
        raise ValueError("The selected destination writer is not enabled by the operator.")

    stored = db.scalar(
        select(UserSecret).where(
            UserSecret.user_id == user.id,
            UserSecret.provider == provider_id,
        )
    )
    if stored is None or stored.validation_status not in {"connected", "untested"}:
        raise ValueError("Configure the Foundry connection before creating a dataset.")

    folder_rid = validate_parent_folder_rid(parent_folder_rid)
    normalized_name = normalize_dataset_name(name)
    credentials = decrypt_user_credentials_for_run(
        db,
        settings,
        user=user,
        provider=provider_id,
        request=request,
        purpose="dataset_create",
    )
    provision = getattr(connector_for(provider_id), "create_dataset", None)
    if not callable(provision):
        raise ValueError("The selected destination does not support dataset creation.")
    create_dataset = cast(Callable[..., ProvisionedDataset], provision)
    created = create_dataset(
        credentials,
        parent_folder_rid=folder_rid,
        name=normalized_name,
    )
    if not DATASET_RID_PATTERN.fullmatch(created.dataset_rid):
        raise ConnectorError(
            TransferErrorCode.PROVIDER_UNAVAILABLE,
            "Foundry returned an invalid dataset RID.",
            retryable=False,
        )

    existing = db.scalar(
        select(FoundryDataset).where(
            FoundryDataset.user_id == user.id,
            FoundryDataset.provider == provider_id,
            FoundryDataset.dataset_rid == created.dataset_rid,
        )
    )
    dataset = existing or FoundryDataset(
        id=new_id(),
        user_id=user.id,
        provider=provider_id,
        dataset_rid=created.dataset_rid,
    )
    dataset.name = created.name[:240]
    dataset.parent_folder_rid = created.parent_folder_rid
    dataset.branch = created.branch or credentials.get("branch", "") or "master"
    if existing is None:
        db.add(dataset)
    stored.validation_status = "connected"
    stored.validated_at = utcnow()
    stored.validation_message = "Authenticated and authorized by a successful dataset creation."
    record_event(
        db,
        "foundry_dataset.created",
        request=request,
        actor=user,
        target=user,
        detail={
            "provider": provider_id,
            "dataset_id": dataset.id,
            "dataset_rid": dataset.dataset_rid,
        },
    )
    db.commit()
    db.refresh(dataset)
    return dataset
