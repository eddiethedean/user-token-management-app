"""HTTP registration for pipeline preview and catalog selection interactions."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol, cast

from fastapi import HTTPException, Request, status
from hedron import Badge, Hedron, OobUpdate, html
from hedron_core import NodeLike
from starlette.responses import Response

from app.application.catalogs import CatalogAccess
from app.connectors.registry import route_allowed, writer_enabled
from app.dependencies import Auth, DbSession, RequireCsrf, SettingsDep
from app.domain.column_types import (
    COLUMN_TYPE_OVERRIDE_DATA_TYPES,
    parse_column_type_override_values,
)
from app.models import PipelineUpload
from app.services.catalogs import (
    CREATE_TABLE_VALUE,
    CSV_SOURCE_CATALOG,
    ProviderCatalog,
    require_catalog_provider,
)
from app.services.csv_uploads import CsvInspection, inspection_from_upload
from app.services.secrets import list_user_secrets
from app.ui.interactions import interaction_response, ok_fragment
from app.ui.params import (
    PipelineAddColumnCastForm,
    PipelineAutoIncrementPrimaryKeyForm,
    PipelineColumnTypeOverridesForm,
    PipelineConflictColumnsForm,
    PipelineIdForm,
    PipelineOptionalProviderForm,
    PipelineOptionalTableForm,
    PipelinePrimaryKeyColumnsForm,
    PipelineSourceProviderForm,
    PipelineSwapForm,
    PipelineWriteModeForm,
)
from app.ui.regions import (
    CSV_INSPECTION,
    CSV_UPLOAD_STATE,
    PIPELINE_CREATE_KEY_CONTROLS,
    PIPELINE_CSV_FILE,
    PIPELINE_DATASET_CREATOR,
    PIPELINE_PREVIEW_REGION,
    PIPELINE_SCHEMA_PREVIEW,
    PIPELINE_SOURCE_DATASET_SUGGESTIONS,
    PIPELINE_SOURCE_FILE_SUGGESTIONS,
    PIPELINE_SOURCE_NODE,
    PIPELINE_SOURCE_PROVIDER_LABEL,
    PIPELINE_SOURCE_SCHEMA_SELECT,
    PIPELINE_SOURCE_SELECT,
    PIPELINE_SOURCE_TABLE_SELECT,
    PIPELINE_TARGET_NODE,
    PIPELINE_TARGET_PROVIDER_LABEL,
    PIPELINE_TARGET_SCHEMA_SELECT,
    PIPELINE_TARGET_SELECT,
    PIPELINE_TARGET_TABLE_SELECT,
    REQUEST_FEEDBACK,
    TOAST_HOST,
)
from app.ui.routes.pipeline_context import WithUserCatalog, run_owned_sync

Connections = dict[str, dict[str, str | bool]]
WriterPolicy = Callable[[str], bool]


class SwapEligibilityResult(Protocol):
    @property
    def allowed(self) -> bool: ...

    @property
    def reason(self) -> str: ...


class CanSwapDirection(Protocol):
    def __call__(
        self,
        catalog_access: CatalogAccess,
        connections: Connections,
        *,
        source_provider: str,
        source_schema: str,
        source_table: str,
        target_provider: str,
        target_schema: str,
        target_table: str,
        writer_policy: WriterPolicy,
        destination_table_new: str = "",
    ) -> SwapEligibilityResult: ...


class PipelinePreviewFragment(Protocol):
    def __call__(
        self,
        *,
        request: Request,
        catalog_access: CatalogAccess,
        source_provider: str,
        source_schema: str,
        source_table: str,
        target_provider: str,
        target_schema: str,
        target_table: str,
        destination_table_new: str = "",
        source_upload_id: str = "",
        write_mode: str = "",
        conflict_columns: str = "",
        primary_key_columns: str = "",
        auto_increment_primary_key: str = "",
        csv_inspection: CsvInspection | None = None,
        csv_upload: PipelineUpload | None = None,
        connections: Connections,
        writer_policy: WriterPolicy,
    ) -> NodeLike: ...


class NormalizedSelection(Protocol):
    def __call__(
        self,
        catalog_access: CatalogAccess,
        provider: str,
        namespace: str,
        object_name: str,
        *,
        preserve_create: bool = False,
        freeform_namespace: bool = False,
        destination: bool = False,
    ) -> tuple[str, str]: ...


class EligibleDestinations(Protocol):
    def __call__(
        self,
        connections: Connections,
        source_provider: str,
        *,
        writer_policy: WriterPolicy,
    ) -> tuple[ProviderCatalog, ...]: ...


class PipelineSchemaPreviewPanel(Protocol):
    def __call__(
        self,
        *,
        request: Request,
        catalog_access: CatalogAccess,
        source_provider: str,
        source_schema: str,
        source_object: str,
        destination_provider: str,
        destination_schema: str,
        destination_object: str,
        destination_create: bool,
        csv_inspection: CsvInspection | None,
        write_mode: str = "",
        column_type_overrides: dict[str, str] | None = None,
        primary_key_columns: str = "",
        auto_increment_primary_key: str = "",
        include_id: bool = True,
    ) -> NodeLike: ...


class ProviderNode(Protocol):
    def __call__(
        self,
        *,
        kind: str,
        catalog: ProviderCatalog | None,
        detail: str,
        configured: bool,
        runtime: str = "",
        include_id: bool = True,
    ) -> NodeLike: ...


class CsvInspectionFragment(Protocol):
    def __call__(
        self,
        upload: PipelineUpload | None = None,
        inspection: CsvInspection | None = None,
        *,
        error: str = "",
        include_id: bool = True,
    ) -> NodeLike: ...


@dataclass(frozen=True)
class PipelinePreviewDependencies:
    """Typed application callbacks used by the preview interaction route."""

    with_user_catalog: WithUserCatalog
    can_swap_direction: CanSwapDirection
    eligible_destinations: EligibleDestinations
    normalized_selection: NormalizedSelection
    pipeline_preview_fragment: PipelinePreviewFragment
    pipeline_schema_preview_panel: PipelineSchemaPreviewPanel
    committed_new_table_name: Callable[[str], str]
    provider_node: ProviderNode
    csv_inspection_fragment: CsvInspectionFragment
    connection_configured: Callable[[dict[str, str | bool]], bool]
    connection_runnable: Callable[[dict[str, str | bool]], bool]


def register_pipeline_preview_routes(
    app: Hedron,
    *,
    dependencies: PipelinePreviewDependencies,
) -> None:
    """Register the catalog-backed preview interaction."""

    with_user_catalog = dependencies.with_user_catalog
    can_swap_direction = dependencies.can_swap_direction
    eligible_destinations = dependencies.eligible_destinations
    normalized_selection = dependencies.normalized_selection
    pipeline_preview_fragment = dependencies.pipeline_preview_fragment
    pipeline_schema_preview_panel = dependencies.pipeline_schema_preview_panel
    committed_new_table_name = dependencies.committed_new_table_name
    provider_node = dependencies.provider_node
    csv_inspection_fragment = dependencies.csv_inspection_fragment
    connection_configured = dependencies.connection_configured
    connection_runnable = dependencies.connection_runnable

    @app.action(
        "/pipeline/preview",
        fragment_regions=(
            CSV_INSPECTION,
            CSV_UPLOAD_STATE,
            PIPELINE_CSV_FILE,
            PIPELINE_SOURCE_SELECT,
            PIPELINE_DATASET_CREATOR,
            PIPELINE_SOURCE_SCHEMA_SELECT,
            PIPELINE_SOURCE_TABLE_SELECT,
            PIPELINE_SOURCE_DATASET_SUGGESTIONS,
            PIPELINE_SOURCE_FILE_SUGGESTIONS,
            PIPELINE_TARGET_SELECT,
            PIPELINE_TARGET_SCHEMA_SELECT,
            PIPELINE_TARGET_TABLE_SELECT,
            PIPELINE_CREATE_KEY_CONTROLS,
            PIPELINE_PREVIEW_REGION,
            PIPELINE_SOURCE_NODE,
            PIPELINE_SOURCE_PROVIDER_LABEL,
            PIPELINE_SCHEMA_PREVIEW,
            PIPELINE_TARGET_NODE,
            PIPELINE_TARGET_PROVIDER_LABEL,
            TOAST_HOST,
            REQUEST_FEEDBACK,
        ),
        include_in_schema=False,
    )
    async def pipeline_preview(
        request: Request,
        auth: Auth,
        db: DbSession,
        settings: SettingsDep,
        _csrf: RequireCsrf,
        source_provider: PipelineSourceProviderForm,
        destination_provider: PipelineOptionalProviderForm = "",
        destination_schema: PipelineOptionalTableForm = "",
        destination_table: PipelineOptionalTableForm = "",
        source_schema: PipelineOptionalTableForm = "",
        source_table: PipelineOptionalTableForm = "",
        destination_table_new: PipelineOptionalTableForm = "",
        source_upload_id: PipelineIdForm = "",
        write_mode: PipelineWriteModeForm = "replace",
        conflict_columns: PipelineConflictColumnsForm = "",
        column_type_overrides: PipelineColumnTypeOverridesForm = None,
        add_column_cast: PipelineAddColumnCastForm = False,
        manual_cast_column: PipelineOptionalTableForm = "",
        manual_cast_type: PipelineOptionalTableForm = "",
        primary_key_columns: PipelinePrimaryKeyColumnsForm = "",
        auto_increment_primary_key: PipelineAutoIncrementPrimaryKeyForm = "",
        swap_direction: PipelineSwapForm = False,
    ) -> Response:
        try:
            parsed_column_type_overrides = parse_column_type_override_values(
                column_type_overrides or ()
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
            ) from exc
        if add_column_cast:
            if (
                not manual_cast_column.strip()
                or len(manual_cast_column) > 256
                or manual_cast_type not in COLUMN_TYPE_OVERRIDE_DATA_TYPES
            ):
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                    detail="Enter a source column name and choose a cast type.",
                )
            parsed_column_type_overrides[manual_cast_column] = manual_cast_type

        def writer_policy(provider: str) -> bool:
            return writer_enabled(provider, settings=settings)

        csv_upload = None
        csv_inspection = None
        if source_provider == "csv" and source_upload_id:
            csv_upload = db.get(PipelineUpload, source_upload_id)
            if csv_upload is not None and csv_upload.user_id == auth.user.id:
                try:
                    csv_inspection = inspection_from_upload(csv_upload)
                except ValueError:
                    csv_upload = None
                    csv_inspection = None
        connections = {
            provider.name: {
                "configured": secret is not None,
                "validation": secret.validation_status if secret is not None else "unconfigured",
                "runtime": secret.runtime_status if secret is not None else "",
            }
            for provider, secret in list_user_secrets(db, auth.user)
        }
        if swap_direction:
            swap_eligibility = await run_owned_sync(
                request,
                with_user_catalog,
                settings,
                auth.user.id,
                request,
                lambda catalog: can_swap_direction(
                    catalog,
                    connections,
                    source_provider=source_provider,
                    source_schema=source_schema,
                    source_table=source_table,
                    target_provider=destination_provider,
                    target_schema=destination_schema,
                    target_table=destination_table,
                    writer_policy=writer_policy,
                    destination_table_new=destination_table_new,
                ),
            )
            if not swap_eligibility.allowed:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                    detail=swap_eligibility.reason,
                )
            source_provider, destination_provider = (
                cast(PipelineSourceProviderForm, destination_provider),
                cast(PipelineOptionalProviderForm, source_provider),
            )
            source_schema, destination_schema = destination_schema, source_schema
            source_table, destination_table = destination_table, source_table
            destination_table_new = ""
        if swap_direction or request.headers.get("HX-Trigger") in {
            "pipeline-source-select",
            "pipeline-source-schema-select",
            "pipeline-source-table-select",
        }:
            parsed_column_type_overrides = {}
        if source_provider != "csv" and not connection_configured(connections[source_provider]):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="Configure and validate the selected source connection first.",
            )
        # A source change can invalidate the previous destination. Resolve a
        # compatible selection before inspecting its schema; save/run still
        # enforce the route and writer policy independently.
        if (
            request.headers.get("HX-Trigger") == "pipeline-source-select"
            or not destination_provider
        ):
            destinations = eligible_destinations(
                connections, source_provider, writer_policy=writer_policy
            )
            if destination_provider not in {catalog.name for catalog in destinations}:
                destination_provider = cast(
                    PipelineOptionalProviderForm, destinations[0].name if destinations else ""
                )
                destination_schema = ""
                destination_table = ""
                destination_table_new = ""
                conflict_columns = ""
        if destination_provider and (
            not connection_configured(connections[destination_provider])
            or not writer_policy(destination_provider)
        ):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="The selected destination is not ready for writes.",
            )
        if destination_provider and not route_allowed(source_provider, destination_provider):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="Select source and destination providers that support this transfer.",
            )
        if source_provider != "csv":
            source_schema, source_table = await run_owned_sync(
                request,
                with_user_catalog,
                settings,
                auth.user.id,
                request,
                lambda catalog: normalized_selection(
                    catalog,
                    source_provider,
                    source_schema,
                    source_table,
                    freeform_namespace=source_provider in {"mss", "mcscop"},
                ),
            )
        if destination_provider:
            destination_schema, destination_table = await run_owned_sync(
                request,
                with_user_catalog,
                settings,
                auth.user.id,
                request,
                lambda catalog: normalized_selection(
                    catalog,
                    destination_provider,
                    destination_schema,
                    destination_table,
                    preserve_create=True,
                    destination=True,
                ),
            )
        preview_fragment = await run_owned_sync(
            request,
            with_user_catalog,
            settings,
            auth.user.id,
            request,
            lambda catalog: pipeline_preview_fragment(
                request=request,
                catalog_access=catalog,
                source_provider=source_provider,
                source_schema=source_schema,
                source_table=source_table,
                target_provider=destination_provider,
                target_schema=destination_schema,
                target_table=destination_table,
                destination_table_new=destination_table_new,
                source_upload_id=source_upload_id,
                write_mode=write_mode,
                conflict_columns=conflict_columns,
                primary_key_columns=primary_key_columns,
                auto_increment_primary_key=auto_increment_primary_key,
                csv_inspection=csv_inspection,
                csv_upload=csv_upload,
                connections=connections,
                writer_policy=writer_policy,
            ),
        )
        source_object = source_table
        destination_object = committed_new_table_name(destination_table_new) or destination_table
        if destination_table == CREATE_TABLE_VALUE:
            destination_object = committed_new_table_name(destination_table_new)
        schema_preview = await run_owned_sync(
            request,
            with_user_catalog,
            settings,
            auth.user.id,
            request,
            lambda catalog: pipeline_schema_preview_panel(
                request=request,
                catalog_access=catalog,
                source_provider=source_provider,
                source_schema=source_schema,
                source_object=source_object,
                destination_provider=destination_provider,
                destination_schema=destination_schema,
                destination_object=destination_object,
                destination_create=destination_table == CREATE_TABLE_VALUE,
                write_mode=write_mode,
                csv_inspection=csv_inspection if csv_upload is not None else None,
                column_type_overrides=parsed_column_type_overrides,
                primary_key_columns=primary_key_columns,
                auto_increment_primary_key=auto_increment_primary_key,
                include_id=False,
            ),
        )
        source_catalog = (
            CSV_SOURCE_CATALOG
            if source_provider == "csv"
            else require_catalog_provider(source_provider)
        )
        target_catalog = (
            require_catalog_provider(destination_provider) if destination_provider else None
        )
        source_ready = (
            source_provider == "csv" and csv_upload is not None and csv_inspection is not None
        ) or (source_provider != "csv" and connection_runnable(connections[source_provider]))
        target_ready = target_catalog is not None and connection_runnable(
            connections[destination_provider]
        )
        source_detail = (
            csv_inspection.filename
            if source_provider == "csv" and source_ready and csv_inspection is not None
            else "Choose a CSV file"
            if source_provider == "csv"
            else f"{source_schema}.{source_table}"
        )
        target_object = committed_new_table_name(destination_table_new) or destination_table
        if destination_table == CREATE_TABLE_VALUE:
            target_object = committed_new_table_name(destination_table_new) or "new_table"
        oob_updates = [
            OobUpdate(schema_preview, element_id="pipeline-schema-preview", swap="outerHTML"),
            OobUpdate(
                html.span(Badge(source_catalog.label, tone="success")),
                element_id="pipeline-source-provider-label",
                swap="outerHTML",
            ),
            OobUpdate(
                html.span(
                    Badge(
                        target_catalog.label if target_catalog is not None else "Not selected",
                        tone="info",
                    )
                ),
                element_id="pipeline-target-provider-label",
                swap="outerHTML",
            ),
            OobUpdate(
                provider_node(
                    kind="source",
                    include_id=False,
                    catalog=source_catalog,
                    detail=source_detail,
                    configured=source_ready,
                    runtime=(
                        str(connections[source_provider]["runtime"])
                        if source_provider != "csv"
                        else ""
                    ),
                ),
                element_id="pipeline-source-node",
                swap="outerHTML",
            ),
            OobUpdate(
                provider_node(
                    kind="target",
                    include_id=False,
                    catalog=target_catalog,
                    detail=(
                        f"{destination_schema}.{target_object}"
                        if target_catalog is not None
                        else "Configure a connection"
                    ),
                    configured=target_ready,
                    runtime=(
                        str(connections[destination_provider]["runtime"])
                        if target_catalog is not None
                        else ""
                    ),
                ),
                element_id="pipeline-target-node",
                swap="outerHTML",
            ),
        ]
        if source_provider != "csv":
            oob_updates.extend(
                [
                    OobUpdate(
                        csv_inspection_fragment(include_id=False),
                        element_id="pipeline-csv-inspection",
                        swap="outerHTML",
                    ),
                    OobUpdate(
                        Badge("5 MB maximum", tone="neutral"),
                        element_id="pipeline-csv-upload-state",
                        swap="outerHTML",
                    ),
                ]
            )
        return await interaction_response(
            request,
            ok_fragment(
                preview_fragment,
                oob=tuple(oob_updates),
            ),
        )
