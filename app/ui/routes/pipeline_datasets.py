"""HTTP registration for Foundry dataset provisioning interactions."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from fastapi import Request, status
from hedron import Fragment, Hedron, html
from hedron_core import NodeLike
from starlette.responses import Response

from app.application.catalogs import CatalogAccess
from app.connectors.errors import ConnectorError
from app.connectors.registry import writer_enabled
from app.dependencies import Auth, DbSession, RequireCsrf, SettingsDep
from app.services.catalogs import (
    CREATE_TABLE_VALUE,
    ProviderCatalog,
    require_catalog_provider,
)
from app.services.foundry_datasets import create_foundry_dataset
from app.ui.interactions import interaction_response, ok_fragment
from app.ui.params import FoundryDatasetNameForm, FoundryFolderRidForm, PipelineProviderForm
from app.ui.regions import (
    PIPELINE_DATASET_CREATOR,
    PIPELINE_TARGET_SCHEMA_SELECT,
    PIPELINE_TARGET_TABLE_SELECT,
    REQUEST_FEEDBACK,
    TOAST_HOST,
)
from app.ui.routes.pipeline_context import WithUserCatalog, WithUserSession, run_owned_sync
from app.ui.urls import hx_attrs


class DatasetCreatorFragment(Protocol):
    def __call__(
        self,
        request: Request,
        catalog: ProviderCatalog | None,
        *,
        created_name: str = "",
        error: str = "",
        needs_dataset: bool = False,
        oob: bool = False,
    ) -> NodeLike: ...


class SchemaOptions(Protocol):
    def __call__(
        self, catalog_access: CatalogAccess, provider: str, preferred_schema: str = ""
    ) -> Sequence[NodeLike]: ...


class TableOptions(Protocol):
    def __call__(
        self,
        catalog_access: CatalogAccess,
        provider: str,
        schema_name: str,
        *,
        allow_create: bool = False,
        additional_tables: tuple[str, ...] = (),
        preferred_table: str = "",
        create_label: str = "table",
        destination: bool = False,
    ) -> Sequence[NodeLike]: ...


def register_pipeline_dataset_routes(
    app: Hedron,
    *,
    dataset_creator_fragment: DatasetCreatorFragment,
    schema_options: SchemaOptions,
    table_options: TableOptions,
    with_user_session: WithUserSession,
    with_user_catalog: WithUserCatalog,
) -> None:
    """Register dataset creation and catalog-refresh endpoints."""

    @app.action(
        "/pipeline/foundry-datasets",
        fragment_regions=(
            PIPELINE_DATASET_CREATOR,
            PIPELINE_TARGET_SCHEMA_SELECT,
            PIPELINE_TARGET_TABLE_SELECT,
            TOAST_HOST,
            REQUEST_FEEDBACK,
        ),
        include_in_schema=False,
    )
    async def pipeline_foundry_dataset_create(
        request: Request,
        auth: Auth,
        db: DbSession,
        settings: SettingsDep,
        _csrf: RequireCsrf,
        destination_provider: PipelineProviderForm,
        parent_folder_rid: FoundryFolderRidForm,
        dataset_name: FoundryDatasetNameForm,
    ) -> Response:
        catalog = require_catalog_provider(destination_provider)
        try:
            created = await run_owned_sync(
                request,
                with_user_session,
                settings,
                auth.user.id,
                lambda thread_db, user: create_foundry_dataset(
                    thread_db,
                    settings,
                    user=user,
                    provider=destination_provider,
                    parent_folder_rid=parent_folder_rid,
                    name=dataset_name,
                    request=request,
                    writer_policy=lambda provider: writer_enabled(provider, settings=settings),
                ),
            )
        except (ConnectorError, ValueError) as exc:
            return await interaction_response(
                request,
                ok_fragment(
                    dataset_creator_fragment(request, catalog, error=str(exc)),
                    status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                    region_id=PIPELINE_DATASET_CREATOR.id,
                ),
            )

        namespace_select = html.select(
            *await run_owned_sync(
                request,
                with_user_catalog,
                settings,
                auth.user.id,
                request,
                lambda catalog_access: schema_options(
                    catalog_access,
                    destination_provider,
                    preferred_schema=created.dataset_rid,
                ),
            ),
            id="pipeline-target-schema-select",
            **{"hx-swap-oob": "outerHTML:#pipeline-target-schema-select"},
            name="destination_schema",
            data={"pipeline-control": "target-schema"},
            **hx_attrs(
                request,
                path="/pipeline/preview",
                method="post",
                target="#pipeline-preview-region",
                swap="none",
                include="#pipeline-form",
                trigger="change",
            ),
        )
        file_select = html.select(
            *await run_owned_sync(
                request,
                with_user_catalog,
                settings,
                auth.user.id,
                request,
                lambda catalog_access: table_options(
                    catalog_access,
                    destination_provider,
                    created.dataset_rid,
                    allow_create=True,
                    preferred_table=CREATE_TABLE_VALUE,
                    create_label="file",
                    destination=True,
                ),
            ),
            id="pipeline-target-table-select",
            **{"hx-swap-oob": "outerHTML:#pipeline-target-table-select"},
            name="destination_table",
            data={"pipeline-control": "target-table"},
            **hx_attrs(
                request,
                path="/pipeline/preview",
                method="post",
                target="#pipeline-preview-region",
                swap="none",
                include="#pipeline-form",
                trigger="change",
            ),
        )
        response = await interaction_response(
            request,
            ok_fragment(
                Fragment(
                    dataset_creator_fragment(request, catalog, created_name=created.name),
                    namespace_select,
                    file_select,
                ),
                toast=f'Dataset "{created.name}" was created.',
                status_code=status.HTTP_201_CREATED,
                region_id=PIPELINE_DATASET_CREATOR.id,
            ),
        )
        response.headers["HX-Trigger-After-Settle"] = "pipelineDatasetCreated"
        return response
