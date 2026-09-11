"""Provider catalogs sourced from the connector registry."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from fastapi import Request
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.connectors.base import CatalogPage, ProviderCapabilities, RemoteNamespace, RemoteObject
from app.connectors.locators import parse_locator
from app.connectors.registry import (
    capabilities_for,
    connector_for,
    listed_capabilities,
    load_builtin_connectors,
)
from app.models import FoundryDataset, PipelineCatalogCache, User, new_id, utcnow

CREATE_TABLE_VALUE = "__new__"
NEW_TABLE_VALUE_PREFIX = f"{CREATE_TABLE_VALUE}:"


@dataclass(frozen=True)
class ProviderCatalog:
    name: str
    label: str
    technology: str
    mark: str
    region: str
    namespaces_label: str
    objects_label: str
    source: bool
    destination: bool
    write_modes: tuple[str, ...]
    supports_runtime_wake: bool = False
    schema_inspection: bool = True
    exact_row_counts: bool = True
    verification_level: str = "exact"
    limitations: tuple[str, ...] = ()
    dataset_creation: bool = False


class UserCatalog:
    """Request-scoped, owner-authorized access to connector catalog metadata."""

    def __init__(
        self,
        db: Session,
        settings: Settings,
        user: User,
        *,
        request: Request | None = None,
    ) -> None:
        self.db = db
        self.settings = settings
        self.user = user
        self.request = request
        self._credentials: dict[str, dict[str, str]] = {}

    def list_namespaces(self, provider: str) -> list[RemoteNamespace]:
        cached = self._read_cache(provider, "")
        if cached is not None:
            items = [RemoteNamespace(**item) for item in cached.get("items", [])]
        else:
            items = connector_for(provider).list_namespaces(self._credentials_for(provider))
            self._write_cache(
                provider,
                "",
                {"items": [vars(item) for item in items]},
            )
        if provider.casefold() not in {"mss", "mcscop"}:
            return items
        provisioned = self.db.scalars(
            select(FoundryDataset)
            .where(
                FoundryDataset.user_id == self.user.id,
                FoundryDataset.provider == provider.casefold(),
            )
            .order_by(FoundryDataset.created_at.desc())
        ).all()
        known = {item.name for item in items}
        return [
            *(
                RemoteNamespace(
                    name=item.dataset_rid,
                    display_name=f"{item.name} · {item.dataset_rid}",
                    kind="dataset",
                )
                for item in provisioned
                if item.dataset_rid not in known
            ),
            *items,
        ]

    def list_objects(self, provider: str, namespace: str) -> CatalogPage:
        cached = self._read_cache(provider, namespace)
        if cached is not None:
            return CatalogPage(
                items=tuple(self._remote_object(item) for item in cached.get("items", [])),
                cursor=cached.get("cursor"),
            )
        credentials = self._credentials_for(provider)
        if provider.casefold() in {"mss", "mcscop"}:
            credentials = {
                **credentials,
                "branch": self.branch_for_namespace(provider, namespace),
            }
        page = connector_for(provider).list_objects(credentials, namespace)
        self._write_cache(
            provider,
            namespace,
            {
                "items": [self._serialize_remote_object(item) for item in page.items],
                "cursor": page.cursor,
            },
        )
        return page

    def inspect_object(self, provider: str, locator):
        return connector_for(provider).inspect_object(self._credentials_for(provider), locator)

    def count_rows(self, provider: str, locator) -> int | None:
        return connector_for(provider).count_rows(self._credentials_for(provider), locator)

    def default_branch(self, provider: str) -> str:
        """Return the branch bound to this user's validated Foundry connection."""

        if provider.casefold() in {"mss", "mcscop"}:
            return self._credentials_for(provider).get("branch", "") or "master"
        return ""

    def branch_for_namespace(self, provider: str, namespace: str) -> str:
        """Resolve the branch for a saved or newly provisioned destination dataset."""

        provider_id = provider.casefold()
        if provider_id not in {"mss", "mcscop"}:
            return ""
        provisioned = self.db.scalar(
            select(FoundryDataset).where(
                FoundryDataset.user_id == self.user.id,
                FoundryDataset.provider == provider_id,
                FoundryDataset.dataset_rid == namespace,
            )
        )
        if provisioned is not None:
            return provisioned.branch
        return self.default_branch(provider_id)

    def _credentials_for(self, provider: str) -> dict[str, str]:
        provider_id = provider.casefold()
        if provider_id not in self._credentials:
            from app.services.secrets import decrypt_user_credentials_for_run

            # Demo adapters still receive the saved bundle. They use its
            # endpoint/database as an isolation key and its Foundry branch when
            # producing locators, but never perform network I/O.
            self._credentials[provider_id] = decrypt_user_credentials_for_run(
                self.db,
                self.settings,
                user=self.user,
                provider=provider_id,
                request=self.request,
                purpose="catalog",
            )
        return self._credentials[provider_id]

    def _read_cache(self, provider: str, namespace: str) -> dict[str, Any] | None:
        now = utcnow()
        row = self.db.scalar(
            select(PipelineCatalogCache).where(
                PipelineCatalogCache.user_id == self.user.id,
                PipelineCatalogCache.provider == provider.casefold(),
                PipelineCatalogCache.namespace == namespace,
                PipelineCatalogCache.expires_at > now,
            )
        )
        if row is None:
            return None
        try:
            payload = json.loads(row.payload_json)
        except (TypeError, ValueError):
            return None
        return payload if isinstance(payload, dict) else None

    def _write_cache(self, provider: str, namespace: str, payload: dict[str, Any]) -> None:
        provider_id = provider.casefold()
        row = self.db.scalar(
            select(PipelineCatalogCache).where(
                PipelineCatalogCache.user_id == self.user.id,
                PipelineCatalogCache.provider == provider_id,
                PipelineCatalogCache.namespace == namespace,
            )
        )
        now = utcnow()
        if row is None:
            row = PipelineCatalogCache(
                id=new_id(),
                user_id=self.user.id,
                provider=provider_id,
                namespace=namespace,
                payload_json="{}",
            )
            self.db.add(row)
        row.payload_json = json.dumps(payload, separators=(",", ":"))
        row.fetched_at = now
        row.expires_at = now + timedelta(seconds=self.settings.pipeline_catalog_ttl_seconds)
        self.db.commit()

    @staticmethod
    def _serialize_remote_object(item: RemoteObject) -> dict[str, Any]:
        return {
            "name": item.name,
            "display_name": item.display_name,
            "locator": item.locator.model_dump(by_alias=True),
            "estimated_rows": item.estimated_rows,
            "size_bytes": item.size_bytes,
            "updated_at": item.updated_at,
            "format": item.format,
        }

    @staticmethod
    def _remote_object(payload: dict[str, Any]) -> RemoteObject:
        return RemoteObject(
            name=str(payload.get("name") or ""),
            display_name=str(payload.get("display_name") or payload.get("name") or ""),
            locator=parse_locator(payload.get("locator")),
            estimated_rows=payload.get("estimated_rows"),
            size_bytes=payload.get("size_bytes"),
            updated_at=str(payload.get("updated_at") or ""),
            format=str(payload.get("format") or ""),
        )


def clear_demo_catalog_cache(db: Session) -> int:
    """Discard cached remote state when the process-local emulator restarts."""

    result = db.execute(delete(PipelineCatalogCache))
    db.commit()
    return max(0, int(getattr(result, "rowcount", 0) or 0))


def _ensure_registry() -> None:
    from app.connectors.registry import listed_capabilities as listed

    if listed():
        return
    load_builtin_connectors(demo=get_settings().is_demo_mode)


def provider_catalog(capabilities: ProviderCapabilities) -> ProviderCatalog:
    return ProviderCatalog(
        name=capabilities.provider,
        label=capabilities.label,
        technology=capabilities.technology,
        mark=capabilities.mark,
        region="",
        namespaces_label=capabilities.namespaces_label,
        objects_label=capabilities.objects_label,
        source=capabilities.source,
        destination=capabilities.destination,
        write_modes=capabilities.write_modes,
        schema_inspection=capabilities.schema_inspection,
        exact_row_counts=capabilities.exact_row_counts,
        verification_level=capabilities.verification_level,
        limitations=capabilities.limitations,
        dataset_creation=capabilities.dataset_creation,
    )


def all_provider_catalogs() -> tuple[ProviderCatalog, ...]:
    _ensure_registry()
    return tuple(provider_catalog(item) for item in listed_capabilities() if item.provider != "csv")


PROVIDER_CATALOGS = ()  # populated after registry load; prefer all_provider_catalogs()
CSV_SOURCE_CATALOG = ProviderCatalog(
    name="csv",
    label="CSV file",
    technology="Delimited file",
    mark="CSV",
    region="Browser upload",
    namespaces_label="Upload",
    objects_label="File",
    source=True,
    destination=False,
    write_modes=(),
    schema_inspection=True,
    exact_row_counts=False,
    verification_level="local_manifest",
    limitations=("Scan the upload to inspect schema and exact row counts.",),
)
PROVIDER_CATALOG_MAP = {"csv": CSV_SOURCE_CATALOG}


def require_catalog_provider(provider: str) -> ProviderCatalog:
    if provider.casefold() == "csv":
        return CSV_SOURCE_CATALOG
    _ensure_registry()
    try:
        return provider_catalog(capabilities_for(provider))
    except Exception as exc:
        raise ValueError("Select a supported source and destination.") from exc
