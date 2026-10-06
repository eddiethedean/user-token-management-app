"""Provider catalogs sourced from the connector registry."""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, cast

from fastapi import Request
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.application.ports import CatalogCache, CredentialResolver
from app.config import Settings, get_settings
from app.connectors.base import (
    CatalogBrowser,
    CatalogPage,
    CatalogReader,
    ColumnSchema,
    ObjectSchema,
    ObjectSchemaInspector,
    ProviderCapabilities,
    RemoteNamespace,
    RemoteObject,
    RowCounter,
)
from app.connectors.decimal_validation import validate_decimal_destination_schema
from app.connectors.errors import ConnectorError, TransferErrorCode
from app.connectors.locators import (
    CsvUploadLocator,
    FoundryDatasetFilesLocator,
    parse_locator,
    validate_locator,
)
from app.connectors.registry import (
    capabilities_for,
    catalog_browser_for,
    listed_capabilities,
    load_builtin_connectors,
    object_schema_inspector_for,
    row_counter_for,
    source_reader_for,
)
from app.db_compat import insert_for
from app.domain.locators import Locator, PostgresTableLocator, PostgresUpsertPolicy, WritePolicy
from app.domain.pipelines.guardrails import scan_ssn_frame
from app.models import FoundryDataset, PipelineCatalogCache, PipelineUpload, User, new_id, utcnow

CREATE_TABLE_VALUE = "__new__"
NEW_TABLE_VALUE_PREFIX = f"{CREATE_TABLE_VALUE}:"
SensitivityMetadataInspector = Callable[
    [dict[str, str], Locator], tuple[tuple[str, ...], dict[str, tuple[str, ...]]]
]


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
        connector_resolver: Callable[[str], CatalogReader] | None = None,
        browser_resolver: Callable[[str], CatalogBrowser] | None = None,
        schema_resolver: Callable[[str], ObjectSchemaInspector] | None = None,
        row_counter_resolver: Callable[[str], RowCounter] | None = None,
        cache: CatalogCache | None = None,
        credential_resolver: CredentialResolver | None = None,
    ) -> None:
        self.db = db
        self.settings = settings
        self.user = user
        self.request = request
        # Resolve module-level defaults at construction time so tests and
        # application composition can replace each narrow connector authority.
        # connector_resolver remains as a compatibility adapter for callers
        # that still provide the legacy composite catalog port.
        legacy_resolver = connector_resolver
        self.browser_resolver = browser_resolver or legacy_resolver or catalog_browser_for
        self.schema_resolver = schema_resolver or legacy_resolver or object_schema_inspector_for
        self.row_counter_resolver = row_counter_resolver or legacy_resolver or row_counter_for
        self.cache = cache
        self.credential_resolver = credential_resolver
        self._credentials: dict[str, dict[str, str]] = {}

    def list_namespaces(self, provider: str) -> list[RemoteNamespace]:
        cached = self._read_cache(provider, "")
        if cached is not None:
            items = [RemoteNamespace(**item) for item in cached.get("items", [])]
        else:
            items = self.browser_resolver(provider).list_namespaces(self._credentials_for(provider))
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
        page = self.browser_resolver(provider).list_objects(credentials, namespace)
        self._write_cache(
            provider,
            namespace,
            {
                "items": [self._serialize_remote_object(item) for item in page.items],
                "cursor": page.cursor,
            },
        )
        return page

    def inspect_object(self, provider: str, locator: Locator):
        locator = validate_locator(locator)
        inspector = self.schema_resolver(provider)
        if not inspector.capabilities.schema_inspection:
            raise ConnectorError(
                code=TransferErrorCode.INTERNAL_ERROR,
                summary="The selected provider does not support schema inspection.",
                retryable=False,
            )
        refresh_sensitivity: SensitivityMetadataInspector | None = None
        if provider.casefold() in {"mss", "mcscop"}:
            refresh_sensitivity = cast(
                SensitivityMetadataInspector | None,
                getattr(inspector, "inspect_sensitivity_metadata", None),
            )
            if not callable(refresh_sensitivity):
                raise self._missing_foundry_metadata_refresh()
        cache_namespace = ""
        if (
            provider.casefold() in {"mss", "mcscop"}
            and isinstance(locator, FoundryDatasetFilesLocator)
            and isinstance(locator.file_paths, list)
            and len(locator.file_paths) == 1
        ):
            locator_digest = hashlib.sha256(locator.model_dump_json().encode()).hexdigest()
            cache_namespace = f"schema:{locator_digest}"
            cached = self._read_cache(provider, cache_namespace)
            if cached is not None and isinstance(cached.get("columns"), list):
                try:
                    cached_columns = tuple(
                        ColumnSchema(
                            **{
                                **item,
                                "example": "",
                                "sensitivity_markers": tuple(item.get("sensitivity_markers") or ()),
                            }
                        )
                        for item in cached["columns"]
                    )
                    cached_schema = ObjectSchema(
                        locator=locator,
                        columns=cached_columns,
                        sensitivity_markers=tuple(cached.get("sensitivity_markers") or ()),
                        column_sensitivity_markers=tuple(
                            (str(name), tuple(markers or ()))
                            for name, markers in (
                                cached.get("column_sensitivity_markers") or {}
                            ).items()
                        ),
                    )
                    assert refresh_sensitivity is not None
                    table_markers, column_markers = refresh_sensitivity(
                        self._credentials_for(provider), locator
                    )
                    cached_schema = ObjectSchema(
                        locator=locator,
                        columns=(
                            ()
                            if table_markers
                            else tuple(
                                ColumnSchema(
                                    name=column.name,
                                    data_type=column.data_type,
                                    nullable=column.nullable,
                                    example="",
                                    sensitivity_markers=column_markers.get(column.name, ()),
                                )
                                for column in cached_schema.columns
                            )
                        ),
                        sensitivity_markers=tuple(table_markers),
                        column_sensitivity_markers=tuple(sorted(column_markers.items())),
                    )
                    # Scrub examples from schema cache rows written by earlier
                    # versions before they can be reused by a later request.
                    self._write_cache(
                        provider,
                        cache_namespace,
                        {
                            "columns": [vars(column) for column in cached_schema.columns],
                            "sensitivity_markers": list(cached_schema.sensitivity_markers),
                            "column_sensitivity_markers": {
                                name: list(markers)
                                for name, markers in cached_schema.column_sensitivity_markers
                            },
                        },
                        preserve_expiry=True,
                    )
                    return cached_schema
                except (TypeError, ValueError):
                    pass
        inspected = inspector.inspect_object(self._credentials_for(provider), locator)
        if cache_namespace:
            self._write_cache(
                provider,
                cache_namespace,
                {
                    "columns": [{**vars(column), "example": ""} for column in inspected.columns],
                    "sensitivity_markers": list(inspected.sensitivity_markers),
                    "column_sensitivity_markers": {
                        name: list(markers)
                        for name, markers in inspected.column_sensitivity_markers
                    },
                },
            )
        return inspected

    def scan_sensitive_content(
        self,
        provider: str,
        locator: Locator,
        *,
        source_upload_id: str = "",
    ) -> list[dict[str, object]]:
        """Scan a complete source through bounded batches and return counts only."""

        locator = validate_locator(locator)
        provider_id = provider.casefold()
        ignored_sensitive_columns: set[str] = set()
        if provider_id == "csv":
            if not isinstance(locator, CsvUploadLocator):
                raise ConnectorError(
                    TransferErrorCode.SOURCE_NOT_FOUND,
                    "The CSV source selection is no longer available.",
                    retryable=False,
                )
            upload = self.db.get(PipelineUpload, source_upload_id or locator.upload_id)
            if (
                upload is None
                or upload.user_id != self.user.id
                or upload.checksum_sha256 != locator.checksum_sha256
            ):
                raise ConnectorError(
                    TransferErrorCode.SOURCE_NOT_FOUND,
                    "The CSV source selection is no longer available.",
                    retryable=False,
                )
            from app.services.csv_uploads import inspection_from_upload

            inspection = inspection_from_upload(upload)
            content = upload.content
            credentials = {
                "content": content.decode("utf-8") if isinstance(content, bytes) else "",
                "delimiter": inspection.delimiter,
                "quote_char": inspection.quote_char,
                "columns": json.dumps([column.name for column in inspection.columns]),
                "column_types": json.dumps([column.inferred_type for column in inspection.columns]),
                "column_timezones": json.dumps(
                    [column.timezone_aware for column in inspection.columns]
                ),
                "column_decimal_specs": json.dumps(
                    [
                        {
                            "precision": column.decimal_precision,
                            "scale": column.decimal_scale,
                        }
                        for column in inspection.columns
                    ]
                ),
            }
        else:
            credentials = self._credentials_for(provider_id)
            if provider_id in {"mss", "mcscop"}:
                inspector = self.schema_resolver(provider_id)
                inspect_sensitivity = cast(
                    SensitivityMetadataInspector | None,
                    getattr(inspector, "inspect_sensitivity_metadata", None),
                )
                if not callable(inspect_sensitivity):
                    raise self._missing_foundry_metadata_refresh()
                table_markers, column_markers = inspect_sensitivity(credentials, locator)
                if table_markers:
                    raise ConnectorError(
                        TransferErrorCode.SENSITIVE_DATA_GUARDRAIL_BLOCKED,
                        "A table-level Foundry sensitivity marking blocks content scanning.",
                        retryable=False,
                    )
                ignored_sensitive_columns = set(column_markers)

        source = source_reader_for(provider_id)
        counts: dict[str, int] = {}
        total_bytes = 0
        started = time.monotonic()
        iterator = source.extract(
            credentials,
            locator,
            batch_rows=self.settings.pipeline_batch_rows,
            batch_bytes=self.settings.pipeline_batch_target_bytes,
        )
        try:
            for batch in iterator:
                total_bytes += int(batch.byte_count)
                if total_bytes > self.settings.pipeline_max_source_bytes:
                    raise ConnectorError(
                        TransferErrorCode.SOURCE_LIMIT_EXCEEDED,
                        "The source exceeded the configured scan limit.",
                        retryable=False,
                    )
                if time.monotonic() - started > self.settings.pipeline_max_run_seconds:
                    raise ConnectorError(
                        TransferErrorCode.RUN_TIMEOUT,
                        "The sensitive-data scan exceeded its time limit.",
                        retryable=False,
                    )
                for name, count in scan_ssn_frame(
                    batch.frame,
                    ignored_columns=tuple(sorted(ignored_sensitive_columns)),
                ).items():
                    counts[name] = counts.get(name, 0) + count
        finally:
            close = getattr(iterator, "close", None)
            if close is not None:
                close()
        return [
            {
                "detector": "ssn",
                "source": "Content scan",
                "column": column,
                "count": count,
            }
            for column, count in sorted(counts.items())
        ]

    @staticmethod
    def _missing_foundry_metadata_refresh() -> ConnectorError:
        return ConnectorError(
            TransferErrorCode.SENSITIVE_DATA_GUARDRAIL_BLOCKED,
            "Foundry sensitivity metadata could not be verified for this source.",
            retryable=False,
        )

    def inspect_object_fresh(self, provider: str, locator: Locator) -> ObjectSchema:
        """Inspect live provider metadata without using the catalog cache."""

        locator = validate_locator(locator)
        inspector = self.schema_resolver(provider)
        if not inspector.capabilities.schema_inspection:
            raise ConnectorError(
                code=TransferErrorCode.INTERNAL_ERROR,
                summary="The selected provider does not support schema inspection.",
                retryable=False,
            )
        return inspector.inspect_object(self._credentials_for(provider), locator)

    def preflight_source(self, provider: str, locator: Locator) -> ObjectSchema:
        """Run connector-specific source permission checks, then read live metadata."""

        locator = validate_locator(locator)
        inspector = self.schema_resolver(provider)
        preflight = getattr(inspector, "preflight_source", None)
        if callable(preflight):
            schema = preflight(self._credentials_for(provider), locator)
            if not isinstance(schema, ObjectSchema):
                raise ConnectorError(
                    TransferErrorCode.SCHEMA_DRIFT,
                    "The source connector returned invalid schema metadata.",
                    retryable=False,
                )
            return schema
        return self.inspect_object_fresh(provider, locator)

    def preflight_destination(
        self,
        provider: str,
        locator: Locator,
        source_schema: ObjectSchema,
        write_policy: WritePolicy,
    ) -> ObjectSchema | None:
        """Run a connector-owned, read-only readiness check when available."""

        locator = validate_locator(locator)
        connector = self.schema_resolver(provider)
        preflight = getattr(connector, "preflight_destination", None)
        if callable(preflight):
            result = preflight(
                self._credentials_for(provider), locator, source_schema, write_policy
            )
            if result is not None and not isinstance(result, ObjectSchema):
                raise ConnectorError(
                    TransferErrorCode.SCHEMA_DRIFT,
                    "The destination connector returned invalid schema metadata.",
                    retryable=False,
                )
            return result
        if isinstance(locator, PostgresTableLocator):
            try:
                destination_schema = self.inspect_object(provider, locator)
                validate_decimal_destination_schema(
                    source_schema.columns, destination_schema.columns
                )
                return destination_schema
            except ConnectorError as exc:
                if exc.code == TransferErrorCode.SOURCE_NOT_FOUND and not isinstance(
                    write_policy, PostgresUpsertPolicy
                ):
                    return None
                raise
        return None

    def count_rows(self, provider: str, locator: Locator) -> int | None:
        locator = validate_locator(locator)
        counter = self.row_counter_resolver(provider)
        if not counter.capabilities.exact_row_counts:
            raise ConnectorError(
                code=TransferErrorCode.INTERNAL_ERROR,
                summary="The selected provider does not support exact row counts.",
                retryable=False,
            )
        return counter.count_rows(self._credentials_for(provider), locator)

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
            # Demo adapters still receive the saved bundle. They use its
            # endpoint/database as an isolation key and its Foundry branch when
            # producing locators, but never perform network I/O.
            if self.credential_resolver is None:
                from app.infrastructure.security.credentials import SqlAlchemyCredentialResolver

                self.credential_resolver = SqlAlchemyCredentialResolver(
                    self.db, self.settings, self.user, self.request
                )
            self._credentials[provider_id] = self.credential_resolver(
                user_id=self.user.id,
                provider=provider_id,
                purpose="catalog",
            )
        return self._credentials[provider_id]

    def _read_cache(self, provider: str, namespace: str) -> dict[str, Any] | None:
        if self.cache is not None:
            return self.cache.get(provider, namespace)
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

    def _write_cache(
        self,
        provider: str,
        namespace: str,
        payload: dict[str, Any],
        *,
        preserve_expiry: bool = False,
    ) -> None:
        if self.cache is not None:
            self.cache.put(provider, namespace, payload, preserve_expiry=preserve_expiry)
            return
        provider_id = provider.casefold()
        now = utcnow()
        cached_row = None
        if preserve_expiry:
            cached_row = self.db.scalar(
                select(PipelineCatalogCache).where(
                    PipelineCatalogCache.user_id == self.user.id,
                    PipelineCatalogCache.provider == provider_id,
                    PipelineCatalogCache.namespace == namespace,
                )
            )
        fetched_at = cached_row.fetched_at if cached_row is not None else now
        expires_at = (
            cached_row.expires_at
            if cached_row is not None
            else now + timedelta(seconds=self.settings.pipeline_catalog_ttl_seconds)
        )
        values = {
            "id": new_id(),
            "user_id": self.user.id,
            "provider": provider_id,
            "namespace": namespace,
            "payload_json": json.dumps(payload, separators=(",", ":")),
            "fetched_at": fetched_at,
            "expires_at": expires_at,
        }
        if not isinstance(self.db.get_bind().dialect.name, str):
            # Lightweight test doubles have no SQL dialect and cannot build a native upsert.
            row = self.db.scalar(
                select(PipelineCatalogCache).where(
                    PipelineCatalogCache.user_id == self.user.id,
                    PipelineCatalogCache.provider == provider_id,
                    PipelineCatalogCache.namespace == namespace,
                )
            )
            if row is None:
                row = PipelineCatalogCache(**values)
                self.db.add(row)
            else:
                row.payload_json = values["payload_json"]
                row.fetched_at = now
                row.expires_at = values["expires_at"]
            self.db.commit()
            return
        statement = insert_for(self.db, PipelineCatalogCache).values(**values)
        self.db.execute(
            statement.on_conflict_do_update(
                index_elements=[
                    PipelineCatalogCache.user_id,
                    PipelineCatalogCache.provider,
                    PipelineCatalogCache.namespace,
                ],
                set_={
                    "payload_json": values["payload_json"],
                    "fetched_at": now,
                    "expires_at": values["expires_at"],
                },
            )
        )
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


def invalidate_published_foundry_file_cache(
    db: Session,
    *,
    user_id: str,
    provider: str,
    dataset_rid: str,
    branch: str,
    file_name: str,
) -> None:
    """Refresh a published file and its dataset listing in the next editor view."""

    locator = FoundryDatasetFilesLocator(
        dataset_rid=dataset_rid, branch=branch, file_paths=[file_name]
    )
    schema_key = f"schema:{hashlib.sha256(locator.model_dump_json().encode()).hexdigest()}"
    db.execute(
        delete(PipelineCatalogCache).where(
            PipelineCatalogCache.user_id == user_id,
            PipelineCatalogCache.provider == provider.casefold(),
            PipelineCatalogCache.namespace.in_((dataset_rid, schema_key)),
        )
    )
    db.commit()


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


def require_catalog_provider(provider: str) -> ProviderCatalog:
    if provider.casefold() == "csv":
        return CSV_SOURCE_CATALOG
    _ensure_registry()
    try:
        return provider_catalog(capabilities_for(provider))
    except Exception as exc:
        raise ValueError("Select a supported source and destination.") from exc
