"""Authenticated data-movement demo workspace."""

from __future__ import annotations

import json
from typing import Any

from fastapi import BackgroundTasks, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from hedron import (
    ActionGroup,
    ActionState,
    ActionTrace,
    Alert,
    AsyncRegion,
    AttrHost,
    Avatar,
    Badge,
    Button,
    CircularProgress,
    ConnectorFlow,
    ConnectorNode,
    ConnectorTrack,
    DescriptionList,
    Expander,
    FileUpload,
    FlowStep,
    FormField,
    FormGrid,
    Grid,
    Hedron,
    HedronRouter,
    Inline,
    InteractionResult,
    Metric,
    OobUpdate,
    OperationIdentity,
    ProcessFlow,
    Progress,
    ResourceList,
    ResourceRow,
    ScrollRegion,
    Stack,
    StateView,
    Status,
    Surface,
    Table,
    TableColumn,
    Timeline,
    html,
)
from hedron.htmx import is_htmx_request
from hedron_core import NodeLike
from starlette.responses import Response

from app.config import get_settings
from app.connectors.errors import ConnectorError
from app.connectors.locators import (
    FoundryDatasetFilesLocator,
    FoundryUploadLocator,
    PostgresTableLocator,
    PostgresUpsertPolicy,
    parse_locator,
    parse_snapshot,
    parse_write_policy,
    postgres_table,
)
from app.connectors.registry import capabilities_for, route_allowed, writer_enabled
from app.dependencies import Auth, DbSession, RequireCsrf, SettingsDep
from app.models import PipelineDefinition, PipelineUpload
from app.services.catalogs import (
    CREATE_TABLE_VALUE,
    CSV_SOURCE_CATALOG,
    ProviderCatalog,
    UserCatalog,
    all_provider_catalogs,
    require_catalog_provider,
)
from app.services.csv_uploads import (
    MAX_CSV_UPLOAD_BYTES,
    CsvInspection,
    inspection_from_upload,
    store_csv_upload,
)
from app.services.foundry_datasets import create_foundry_dataset
from app.services.pipeline_metadata import provenance_label, schema_diff
from app.services.pipeline_runs import (
    enqueue_run,
    events_after,
    latest_run_map,
    owned_run,
    record_reconciliation_review,
    request_cancel,
    snapshot_from_definition,
)
from app.services.pipeline_tasks import schedule_pipeline_run
from app.services.pipelines import list_pipelines, save_pipeline
from app.services.secrets import list_user_secrets
from app.ui.design_system import (
    DATA_MOVER_DESIGN,
    PROCESS_FLOW_STEP_STYLE_CLASS,
    apply_data_recipe,
    surface_card,
)
from app.ui.design_system import DataMoverPageHeader as PageHeader
from app.ui.forms import csrf_hidden
from app.ui.http import render_authenticated_view
from app.ui.interactions import interaction_response, ok_fragment
from app.ui.layout import INDICATOR, alert_box
from app.ui.params import (
    CsvUploadForm,
    FoundryDatasetNameForm,
    FoundryFolderRidForm,
    NoticeQuery,
    PipelineConflictColumnsForm,
    PipelineIdForm,
    PipelineNameForm,
    PipelineOptionalTableForm,
    PipelineProviderForm,
    PipelineSchemaForm,
    PipelineSourceProviderForm,
    PipelineTableForm,
    PipelineWriteModeForm,
)
from app.ui.regions import (
    CSV_INSPECTION,
    CSV_UPLOAD_STATE,
    MAIN_PANEL,
    PIPELINE_CSV_FILE,
    PIPELINE_DATASET_CREATOR,
    PIPELINE_PREVIEW_REGION,
    PIPELINE_RUN_MONITOR,
    PIPELINE_SCHEMA_PREVIEW,
    PIPELINE_SOURCE_NODE,
    PIPELINE_SOURCE_PROVIDER_LABEL,
    PIPELINE_SOURCE_SCHEMA_SELECT,
    PIPELINE_SOURCE_TABLE_SELECT,
    PIPELINE_TARGET_NODE,
    PIPELINE_TARGET_PROVIDER_LABEL,
    PIPELINE_TARGET_SCHEMA_SELECT,
    PIPELINE_TARGET_TABLE_SELECT,
    SIDE_NAV,
    TOAST_HOST,
)
from app.ui.tabs import NavigationTabs
from app.ui.urls import form_action, hx_attrs, redirect_path


def _provider_label(provider: str) -> str:
    if provider == "csv":
        return CSV_SOURCE_CATALOG.label
    return require_catalog_provider(provider).label


def _option(
    value: str,
    label: str,
    *,
    selected: bool = False,
    disabled: bool = False,
    **data: str,
):
    attrs = {"value": value}
    if selected:
        attrs["selected"] = True
    if disabled:
        attrs["disabled"] = True
    if data:
        attrs["data"] = data
    return html.option(label, **attrs)


_WRITE_MODE_LABELS = {
    "upsert": "Upsert on primary key",
    "append": "Append only",
    "replace": "Replace destination",
}


def _resolved_write_mode(
    catalog: ProviderCatalog | None, selected: str, *, upsert_available: bool = True
) -> str:
    supported = tuple(
        mode
        for mode in (catalog.write_modes if catalog is not None else ())
        if mode != "upsert" or upsert_available
    )
    if selected in supported:
        return selected
    return supported[0] if supported else ""


def _write_mode_select(
    catalog: ProviderCatalog | None,
    *,
    selected: str,
    upsert_available: bool = True,
    oob: bool = False,
) -> NodeLike:
    resolved = _resolved_write_mode(catalog, selected, upsert_available=upsert_available)
    supported = tuple(
        mode
        for mode in (catalog.write_modes if catalog is not None else ())
        if mode != "upsert" or upsert_available
    )
    options = [
        _option(mode, _WRITE_MODE_LABELS[mode], selected=mode == resolved) for mode in supported
    ]
    if not options:
        options = [_option("", "Choose a destination first", selected=True, disabled=True)]
    attrs: dict[str, Any] = {
        "id": "pipeline-mode-select",
        "name": "write_mode",
        "disabled": not supported,
    }
    if oob:
        attrs["hx-swap-oob"] = "outerHTML:#pipeline-mode-select"
    return html.select(*options, **attrs)


def _upsert_keys(
    catalog_access: UserCatalog, provider: str, namespace: str, object_name: str
) -> tuple[tuple[str, ...], ...]:
    if (
        provider != "postgres"
        or not namespace
        or not object_name
        or object_name == CREATE_TABLE_VALUE
    ):
        return ()
    try:
        schema = catalog_access.inspect_object("postgres", postgres_table(namespace, object_name))
    except Exception:
        return ()
    keys = [tuple(schema.primary_key), *map(tuple, schema.unique_constraints)]
    return tuple(dict.fromkeys(key for key in keys if key))


def _upsert_key_select(
    keys: tuple[tuple[str, ...], ...], *, selected: str = "", oob: bool = False
) -> NodeLike:
    selected_key = tuple(item.strip() for item in selected.split(",") if item.strip())
    options = [
        _option(
            ",".join(key),
            ", ".join(key),
            selected=key == selected_key or (not selected_key and index == 0),
        )
        for index, key in enumerate(keys)
    ]
    if not options:
        options = [_option("", "No primary or unique key", selected=True, disabled=True)]
    attrs: dict[str, Any] = {
        "id": "pipeline-upsert-key-select",
        "name": "conflict_columns",
        "disabled": not keys,
    }
    if oob:
        attrs["hx-swap-oob"] = "outerHTML:#pipeline-upsert-key-select"
    return html.select(*options, **attrs)


def _connection_configured(details: dict[str, str | bool]) -> bool:
    return details["configured"] is True and details["validation"] == "connected"


def _connection_runnable(details: dict[str, str | bool]) -> bool:
    return _connection_configured(details)


def _connection_provisionable(details: dict[str, str | bool], catalog: ProviderCatalog) -> bool:
    return (
        details["configured"] is True
        and catalog.dataset_creation
        and details["validation"] in {"connected", "untested"}
    )


def _configured_catalogs(
    connections: dict[str, dict[str, str | bool]], *, role: str
) -> tuple[ProviderCatalog, ...]:
    return tuple(
        catalog
        for catalog in all_provider_catalogs()
        if catalog.name in connections
        and (
            _connection_configured(connections[catalog.name])
            or (
                role == "destination"
                and _connection_provisionable(connections[catalog.name], catalog)
            )
        )
        and (
            (role == "source" and catalog.source)
            or (role == "destination" and catalog.destination and writer_enabled(catalog.name))
        )
    )


def _provider_options(
    connections: dict[str, dict[str, str | bool]],
    *,
    selected: str,
    role: str,
    counterpart: str = "",
):
    return [
        _option(
            catalog.name,
            f"{catalog.label} · {catalog.technology}",
            selected=catalog.name == selected,
            configured=str(bool(connections[catalog.name]["configured"])).lower(),
            validation=str(connections[catalog.name]["validation"]),
            runtime=str(connections[catalog.name]["runtime"]),
            technology=catalog.technology,
            region=catalog.namespaces_label,
        )
        for catalog in all_provider_catalogs()
        if catalog.name in connections
        and (
            _connection_configured(connections[catalog.name])
            or (
                role == "destination"
                and _connection_provisionable(connections[catalog.name], catalog)
            )
        )
        and (
            (role == "source" and catalog.source)
            or (role == "destination" and catalog.destination and writer_enabled(catalog.name))
        )
        and (
            not counterpart
            or (
                route_allowed(catalog.name, counterpart)
                if role == "source"
                else route_allowed(counterpart, catalog.name)
            )
        )
    ]


def _source_provider_options(
    connections: dict[str, dict[str, str | bool]], *, selected: str, target_provider: str
):
    return [
        *_provider_options(
            connections,
            selected=selected,
            role="source",
            counterpart=target_provider,
        ),
        *(
            [
                _option(
                    "csv",
                    "CSV file · Upload from device",
                    selected=selected == "csv",
                    configured="false",
                    validation="local",
                    runtime="",
                    technology=CSV_SOURCE_CATALOG.technology,
                    region=CSV_SOURCE_CATALOG.region,
                )
            ]
            if not target_provider or route_allowed("csv", target_provider)
            else []
        ),
    ]


def _namespace_entries(catalog_access: UserCatalog, provider: str) -> list[tuple[str, str]]:
    return [(item.name, item.display_name) for item in catalog_access.list_namespaces(provider)]


def _object_entries(
    catalog_access: UserCatalog, provider: str, namespace: str
) -> list[tuple[str, str]]:
    try:
        page = catalog_access.list_objects(provider, namespace)
    except ConnectorError:
        # A saved remote namespace can disappear or, in the demo adapter, be
        # empty after a process restart. Keep the workspace usable so the user
        # can select another destination or provision a replacement.
        return []
    return [(item.name, item.display_name) for item in page.items]


def _first_namespace(catalog_access: UserCatalog, provider: str) -> str:
    entries = _namespace_entries(catalog_access, provider)
    return entries[0][0] if entries else ""


def _first_object(catalog_access: UserCatalog, provider: str, namespace: str) -> str:
    if not namespace:
        return ""
    entries = _object_entries(catalog_access, provider, namespace)
    return entries[0][0] if entries else ""


def _normalized_selection(
    catalog_access: UserCatalog,
    provider: str,
    namespace: str,
    object_name: str,
    *,
    preserve_create: bool = False,
) -> tuple[str, str]:
    """Keep a form selection valid when its provider changes."""
    if not provider:
        return "", ""
    namespaces = _namespace_entries(catalog_access, provider)
    if not namespaces:
        return (
            "",
            CREATE_TABLE_VALUE if preserve_create and object_name == CREATE_TABLE_VALUE else "",
        )
    namespace_names = {name for name, _ in namespaces}
    resolved_namespace = namespace if namespace in namespace_names else namespaces[0][0]
    objects = _object_entries(catalog_access, provider, resolved_namespace)
    object_names = {name for name, _ in objects}
    if preserve_create and object_name == CREATE_TABLE_VALUE:
        return resolved_namespace, CREATE_TABLE_VALUE
    resolved_object = (
        object_name if object_name in object_names else (objects[0][0] if objects else "")
    )
    return resolved_namespace, resolved_object


def _schema_options(catalog_access: UserCatalog, provider: str, preferred_schema: str = ""):
    entries = _namespace_entries(catalog_access, provider)
    return [
        _option(
            name,
            display,
            selected=name == preferred_schema or (not preferred_schema and index == 0),
        )
        for index, (name, display) in enumerate(entries)
    ]


def _committed_new_table_name(value: str) -> str:
    prefix = f"{CREATE_TABLE_VALUE}:"
    return value[len(prefix) :] if value.startswith(prefix) else value


def _table_options(
    catalog_access: UserCatalog,
    provider: str,
    schema_name: str,
    *,
    allow_create: bool = False,
    additional_tables: tuple[str, ...] = (),
    preferred_table: str = "",
    create_label: str = "table",
):
    entries = _object_entries(catalog_access, provider, schema_name) if schema_name else []
    known = {name for name, _ in entries}
    options = [
        _option(name, display, selected=(name == preferred_table or index == 0))
        for index, (name, display) in enumerate(entries)
    ]
    for table_name in additional_tables:
        if table_name not in known:
            options.append(_option(table_name, table_name))
    if allow_create:
        options.append(
            _option(
                CREATE_TABLE_VALUE,
                f"＋ Create a new {create_label}…",
                selected=preferred_table == CREATE_TABLE_VALUE,
            )
        )
    return options


def _destination_namespace_options(
    catalog_access: UserCatalog,
    catalog: ProviderCatalog,
    provider: str,
    *,
    preferred_schema: str = "",
):
    options = _schema_options(catalog_access, provider, preferred_schema=preferred_schema)
    if options or not catalog.dataset_creation:
        return options
    return [_option("", "Create a dataset above", selected=True, disabled=True)]


def _destination_object_options(
    catalog_access: UserCatalog,
    catalog: ProviderCatalog,
    provider: str,
    namespace: str,
    *,
    preferred_table: str = "",
    additional_tables: tuple[str, ...] = (),
):
    if catalog.dataset_creation and not namespace:
        return [_option("", "Create a dataset first", selected=True, disabled=True)]
    return _table_options(
        catalog_access,
        provider,
        namespace,
        allow_create=True,
        additional_tables=additional_tables,
        preferred_table=preferred_table,
        create_label=catalog.objects_label.casefold(),
    )


def _dataset_creator(
    request: Request,
    catalog: ProviderCatalog | None,
    *,
    created_name: str = "",
    error: str = "",
    needs_dataset: bool = False,
    oob: bool = False,
) -> NodeLike:
    attrs: dict[str, Any] = {
        "id": "pipeline-dataset-creator",
        "class_": "data-mover-dataset-creator",
    }
    if oob:
        attrs["hx-swap-oob"] = "outerHTML:#pipeline-dataset-creator"
    if catalog is None or not catalog.dataset_creation:
        attrs["hidden"] = True
        return html.div(**attrs)
    feedback: NodeLike | None = None
    if created_name:
        feedback = Inline(
            Badge("Dataset ready", tone="success"),
            html.span(f"“{created_name}” is selected. Name its first file below."),
            gap="sm",
            class_="data-mover-dataset-ready",
        )
    elif error:
        feedback = Alert(error, tone="danger")
    elif needs_dataset:
        feedback = Alert(
            "No dataset is available yet. Create one to unlock the file destination.",
            tone="info",
        )
    return html.div(
        feedback if created_name else None,
        Expander(
            "Create another dataset" if created_name else "Create Foundry dataset",
            Stack(
                feedback if not created_name else None,
                html.p(
                    "Provision an empty dataset in an approved Foundry folder and select it for this route.",
                    class_="hedron-text-muted",
                ),
                FormGrid(
                    FormField(
                        name="parent_folder_rid",
                        label="Parent folder RID",
                        help="The token must be allowed to create resources in this folder.",
                        control=html.input(
                            name="parent_folder_rid",
                            maxlength="240",
                            placeholder="ri.compass.main.folder…",
                            autocomplete="off",
                            class_="data-mover-rid-input",
                        ),
                    ),
                    FormField(
                        name="dataset_name",
                        label="Dataset name",
                        control=html.input(
                            name="dataset_name",
                            maxlength="160",
                            placeholder="Daily readiness landing",
                            autocomplete="off",
                        ),
                    ),
                    columns=1,
                    gap="sm",
                    density="compact",
                ),
                Button(
                    "Create and select dataset",
                    type="button",
                    variant="secondary",
                    attrs={
                        **hx_attrs(
                            request,
                            path="/pipeline/foundry-datasets",
                            method="post",
                            target="#pipeline-dataset-creator",
                            swap="outerHTML",
                            include="#pipeline-form",
                            disabled_elt="this",
                            indicator=INDICATOR,
                            busy="region",
                        ),
                        "formnovalidate": True,
                        "hx-validate": "false",
                    },
                ),
                gap="sm",
            ),
            open=bool(error or needs_dataset),
        ),
        **attrs,
    )


def _select_fragment(
    select_id: str,
    options,
    *,
    name: str,
    disabled: bool = False,
):
    attrs = {
        "id": select_id,
        "name": name,
        **({"disabled": True} if disabled else {}),
        **{"hx-swap-oob": f"outerHTML:#{select_id}"},
    }
    return html.select(*options, **attrs)


def _created_destination_tables(
    pipelines: list[PipelineDefinition], provider: str, schema_name: str
) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(
            _pipeline_form_locations(pipeline)[3]
            for pipeline in pipelines
            if pipeline.destination_create
            and pipeline.destination_provider == provider
            and _pipeline_form_locations(pipeline)[2] == schema_name
        )
    )


def _pipeline_form_locations(pipeline: PipelineDefinition) -> tuple[str, str, str, str]:
    """Return catalog namespace/object values, preferring canonical versioned locators."""
    source_namespace = pipeline.source_schema
    source_object = pipeline.source_table
    destination_namespace = pipeline.destination_schema
    destination_object = pipeline.destination_table

    try:
        source = parse_locator(json.loads(pipeline.source_locator_json))
    except (TypeError, ValueError, json.JSONDecodeError):
        source = None
    if isinstance(source, PostgresTableLocator):
        source_namespace = source.schema_name
        source_object = source.table
    elif isinstance(source, FoundryDatasetFilesLocator):
        source_namespace = source.dataset_rid
        if source.file_paths != "all_supported":
            source_object = source.file_paths[0]

    try:
        destination = parse_locator(json.loads(pipeline.destination_locator_json))
    except (TypeError, ValueError, json.JSONDecodeError):
        destination = None
    if isinstance(destination, PostgresTableLocator):
        destination_namespace = destination.schema_name
        destination_object = destination.table
    elif isinstance(destination, FoundryUploadLocator):
        destination_namespace = destination.dataset_rid
        destination_object = destination.file_name

    return source_namespace, source_object, destination_namespace, destination_object


def _capability_surface(
    source_catalog: ProviderCatalog, destination_catalog: ProviderCatalog | None
):
    def facts(catalog: ProviderCatalog | None, *, destination: bool = False):
        route_label = "Destination route" if destination else "Source route"
        route_description = (
            "Write options and post-transfer verification."
            if destination
            else "Pre-transfer inspection and extraction visibility."
        )
        if catalog is None:
            return Surface(
                Stack(
                    PageHeader(
                        "Not selected",
                        eyebrow=route_label,
                        description=route_description,
                        level=4,
                        density="compact",
                    ),
                    DescriptionList(
                        ("Status", Badge("Select a destination", tone="warning")),
                        density="compact",
                    ),
                    gap="sm",
                ),
                appearance="raised",
                padding="sm",
                elevation="sm",
            )
        count_label = (
            "Exact counts"
            if destination and catalog.exact_row_counts
            else "Catalog estimates"
            if catalog.exact_row_counts
            else "Counts unavailable"
        )
        schema_label = (
            "Schema preview" if catalog.schema_inspection else "Schema captured during run"
        )
        return Surface(
            Stack(
                PageHeader(
                    catalog.label,
                    eyebrow=route_label,
                    description=route_description,
                    level=4,
                    density="compact",
                ),
                DescriptionList(
                    (
                        "Schema",
                        Badge(
                            schema_label,
                            tone="success" if catalog.schema_inspection else "info",
                        ),
                    ),
                    (
                        "Rows",
                        Badge(
                            count_label,
                            tone="success" if catalog.exact_row_counts else "warning",
                        ),
                    ),
                    ("Verification", catalog.verification_level.replace("_", " ").title()),
                    (
                        "Write modes",
                        ", ".join(catalog.write_modes) if destination else "Source only",
                    ),
                    density="compact",
                ),
                gap="sm",
            ),
            appearance="raised",
            padding="sm",
            elevation="sm",
        )

    return DATA_MOVER_DESIGN.apply(
        "data-mover-inset",
        Surface(
            PageHeader(
                "Route capabilities",
                eyebrow="What will be known before and after the run",
                description="Provider support varies; unavailable facts are reported instead of estimated silently.",
                level=3,
                density="compact",
            ),
            Grid(
                facts(source_catalog),
                facts(destination_catalog, destination=True),
                columns=2,
                gap="md",
            ),
            appearance="plain",
            padding="sm",
            elevation="none",
        ),
    )


def _run_duration_label(run: object) -> str:
    started = getattr(run, "started_at", None)
    finished = getattr(run, "finished_at", None)
    if started is None or finished is None:
        return "Duration unavailable"
    seconds = max(0.0, (finished - started).total_seconds())
    return f"{seconds:.1f}s" if seconds < 60 else f"{seconds / 60:.1f}m"


def _saved_run_summary(run: object | None) -> str:
    if run is None:
        return "No runs yet"
    verification = _run_manifest(getattr(run, "verification_json", None))
    before = verification.get("destination_rows_before")
    after = verification.get("destination_rows_after")
    if isinstance(before, int) and isinstance(after, int):
        destination = f"destination {before:,} → {after:,} ({after - before:+,})"
    elif isinstance(after, int):
        destination = f"destination {after:,} rows"
    else:
        destination = "destination count unavailable"
    timestamp = getattr(run, "finished_at", None) or getattr(run, "created_at", None)
    timestamp_label = (
        timestamp.strftime("%b %d, %H:%M") if timestamp is not None else "time unavailable"
    )
    return (
        f"Last run {timestamp_label} · "
        f"{getattr(run, 'source_rows', 0):,} extracted · "
        f"{getattr(run, 'loaded_rows', 0):,} loaded · {destination} · "
        f"{_run_duration_label(run)}"
    )


def _catalog_data(
    catalog_access: UserCatalog,
    catalogs: tuple[ProviderCatalog, ...],
    pipelines: list[PipelineDefinition],
):
    nodes = []
    for catalog in catalogs:
        for namespace, _display in _namespace_entries(catalog_access, catalog.name):
            objects = [
                name for name, _label in _object_entries(catalog_access, catalog.name, namespace)
            ] + list(_created_destination_tables(pipelines, catalog.name, namespace))
            for table_name in dict.fromkeys(objects):
                nodes.append(
                    html.span(
                        data={
                            "catalog-provider": catalog.name,
                            "catalog-schema": namespace,
                            "catalog-table": table_name,
                            "records": "—",
                            "size": "—",
                            "megabytes": "0",
                        }
                    )
                )
    return html.div(
        *nodes,
        id="pipeline-catalog-data",
        hidden=True,
        aria={"hidden": "true"},
    )


def _format_file_size(size_bytes: int) -> str:
    if size_bytes >= 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.2f} MB"
    if size_bytes >= 1024:
        return f"{size_bytes / 1024:.1f} KB"
    return f"{size_bytes} B"


def _run_locator_label(locator: object) -> str:
    if isinstance(locator, PostgresTableLocator):
        return f"{locator.schema_name}.{locator.table}"
    if isinstance(locator, FoundryDatasetFilesLocator):
        if isinstance(locator.file_paths, list) and locator.file_paths:
            return locator.file_paths[0]
        return locator.dataset_rid
    if isinstance(locator, FoundryUploadLocator):
        return locator.file_name
    return "Uploaded CSV"


def _csv_columns_json(inspection: CsvInspection) -> str:
    return json.dumps(
        [
            {
                "name": column.name,
                "type": column.inferred_type,
                "populated": column.populated,
                "nulls": column.nulls,
                "example": column.example,
            }
            for column in inspection.columns
        ],
        separators=(",", ":"),
    )


def _remote_object_preview(
    catalog_access: UserCatalog, provider: str, namespace: str, object_name: str
):
    if not provider or not namespace or not object_name or object_name == CREATE_TABLE_VALUE:
        return None
    try:
        page = catalog_access.list_objects(provider, namespace)
        return next((item for item in page.items if item.name == object_name), None)
    except Exception:
        return None


def _route_schema_preview(
    catalog_access: UserCatalog,
    provider: str,
    namespace: str,
    object_name: str,
    *,
    destination: bool = False,
    creating: bool = False,
    csv_inspection: CsvInspection | None = None,
):
    if csv_inspection is not None:
        return {
            "rows": csv_inspection.row_count,
            "size_bytes": csv_inspection.size_bytes,
            "columns": [
                {
                    "name": column.name,
                    "data_type": column.inferred_type,
                    "nullable": column.nulls > 0,
                    "example": column.example,
                }
                for column in csv_inspection.columns
            ],
            "primary_key": [],
            "status": "Scanned locally",
            "schema_provenance": "catalog",
            "row_provenance": "exact",
            "size_provenance": "catalog",
            "capabilities": {
                "schema_inspection": True,
                "exact_row_counts": True,
                "verification_level": "local_manifest",
                "limitations": (),
            },
        }
    if creating:
        return {
            "rows": None,
            "size_bytes": None,
            "columns": [],
            "primary_key": [],
            "status": "Created by run",
            "schema_provenance": "unavailable",
            "row_provenance": "unavailable",
            "size_provenance": "unavailable",
            "capabilities": {
                "schema_inspection": True,
                "exact_row_counts": False,
                "verification_level": "local_manifest",
                "limitations": ("The destination schema is created during the run.",),
            },
        }
    remote = _remote_object_preview(catalog_access, provider, namespace, object_name)
    if remote is None:
        return None
    capabilities = capabilities_for(provider)
    columns: list[dict[str, object]] = []
    primary_key: list[str] = []
    estimated_rows = remote.estimated_rows
    schema_provenance = (
        "provider_unavailable" if not capabilities.schema_inspection else "unavailable"
    )
    row_provenance = "estimated" if estimated_rows is not None else "unavailable"
    try:
        inspected = catalog_access.inspect_object(provider, remote.locator)
        estimated_rows = inspected.estimated_rows or estimated_rows
        primary_key = list(inspected.primary_key)
        columns = [
            {
                "name": column.name,
                "data_type": column.data_type,
                "nullable": column.nullable,
                "example": column.example,
            }
            for column in inspected.columns
        ]
        if columns:
            schema_provenance = "catalog"
        if inspected.estimated_rows is not None:
            row_provenance = "estimated"
    except Exception:
        pass
    if destination and capabilities.exact_row_counts:
        try:
            counted = catalog_access.count_rows(provider, remote.locator)
            estimated_rows = counted if counted is not None else estimated_rows
            if counted is not None:
                row_provenance = "exact"
        except Exception:
            pass
    return {
        "rows": estimated_rows,
        "size_bytes": remote.size_bytes,
        "columns": columns,
        "primary_key": primary_key,
        "status": "Catalog preview",
        "schema_provenance": schema_provenance,
        "row_provenance": row_provenance,
        "size_provenance": "catalog" if remote.size_bytes is not None else "unavailable",
        "capabilities": {
            "schema_inspection": capabilities.schema_inspection,
            "exact_row_counts": capabilities.exact_row_counts,
            "verification_level": capabilities.verification_level,
            "limitations": capabilities.limitations,
        },
    }


def _schema_columns_table(columns: list[dict[str, object]], label: str):
    if not columns:
        return StateView(
            "Schema details will appear during validation",
            kind="empty",
            description="The provider does not expose column metadata before a run.",
        )
    return ScrollRegion(
        Table(
            rows=[
                [
                    html.strong(str(column.get("name") or "—")),
                    Badge(str(column.get("data_type") or "unknown"), tone="info", size="sm"),
                    "Nullable" if column.get("nullable") else "Required",
                    html.code(str(column.get("example") or "—")),
                ]
                for column in columns
            ],
            columns=[
                TableColumn(header="Column"),
                TableColumn(header="Type"),
                TableColumn(header="Nullability"),
                TableColumn(header="Example", size="wide"),
            ],
            density="compact",
            sticky_header=True,
            zebra=True,
        ),
        axis="block",
        size="sm",
        label=label,
    )


def _schema_preview_surface(
    title: str, preview: dict[str, Any] | None, *, destination: bool = False
):
    if preview is None:
        return Surface(
            PageHeader(title, eyebrow="Schema preview", level=3, density="compact"),
            StateView(
                "Select an object to preview it",
                kind="empty",
                description="Choose a source or destination object to see available schema facts.",
            ),
            appearance="plain",
            padding="sm",
            elevation="none",
        )
    rows = preview.get("rows")
    row_value = (
        "New table"
        if rows is None and preview.get("status") == "Created by run"
        else (f"{rows:,} rows" if isinstance(rows, int) else "Unavailable")
    )
    size = preview.get("size_bytes")
    size_text = _format_file_size(int(size)) if isinstance(size, int) else "Size unavailable"
    schema_provenance = str(preview.get("schema_provenance") or "unavailable")
    row_provenance = str(preview.get("row_provenance") or "unavailable")
    preview_complete = bool(preview.get("columns")) and row_provenance in {"exact", "estimated"}
    capabilities_value = preview.get("capabilities")
    capabilities: dict[str, Any] = (
        capabilities_value if isinstance(capabilities_value, dict) else {}
    )
    limitations = tuple(str(item) for item in capabilities.get("limitations") or ())
    status_label = "Ready to compare" if preview_complete else "Limited preview"
    status_tone = "success" if preview_complete else "warning"
    limitation = limitations[0] if limitations else None
    return Surface(
        PageHeader(
            title,
            eyebrow="Destination schema" if destination else "Source schema",
            description=(
                f"{preview.get('status') or 'Schema preview'} · {size_text} · "
                f"Rows: {provenance_label(row_provenance)} · "
                f"Schema: {provenance_label(schema_provenance)}"
            ),
            level=3,
            density="compact",
            meta=Badge(status_label, tone=status_tone),
        ),
        Grid(
            Metric("Rows before run" if destination else "Source rows", row_value),
            Metric("Columns", f"{len(preview.get('columns') or []):,}"),
            Metric(
                "Primary key",
                ", ".join(str(item) for item in preview.get("primary_key") or []) or "None",
            ),
            columns=3,
            gap="sm",
        ),
        StateView(
            "Provider limitation" if limitation else "Preview facts",
            kind="empty",
            description=limitation
            or "These facts will be captured again by the worker during the run.",
        )
        if limitation and not preview_complete
        else None,
        _schema_columns_table(preview.get("columns") or [], f"Columns in {title}"),
        appearance="plain",
        padding="sm",
        elevation="none",
    )


def _pipeline_schema_preview_panel(
    *,
    catalog_access: UserCatalog,
    source_provider: str,
    source_schema: str,
    source_object: str,
    destination_provider: str,
    destination_schema: str,
    destination_object: str,
    destination_create: bool,
    csv_inspection: CsvInspection | None,
):
    source = _route_schema_preview(
        catalog_access,
        source_provider,
        source_schema,
        source_object,
        csv_inspection=csv_inspection,
    )
    destination = _route_schema_preview(
        catalog_access,
        destination_provider,
        destination_schema,
        destination_object,
        destination=True,
        creating=destination_create,
    )
    return DATA_MOVER_DESIGN.apply(
        "data-mover-inset",
        Surface(
            PageHeader(
                "Schema & row counts",
                eyebrow="Pre-run review",
                description="Confirm the columns and current row counts before starting this route.",
                level=3,
                density="compact",
            ),
            Grid(
                _schema_preview_surface("Source", source),
                _schema_preview_surface("Destination", destination, destination=True),
                columns=2,
                gap="sm",
            ),
            id="pipeline-schema-preview",
            appearance="plain",
            padding="sm",
            elevation="none",
        ),
    )


def _csv_inspection(
    upload: PipelineUpload | None = None,
    inspection: CsvInspection | None = None,
    *,
    error: str = "",
):
    if error:
        content = StateView(
            "CSV scan failed",
            kind="error",
            description=error,
        )
    elif upload is not None and inspection is not None:
        content = Stack(
            PageHeader(
                inspection.filename,
                eyebrow="CSV source",
                description="Schema inspection completed and this file is ready to map.",
                level=3,
                density="compact",
                meta=Badge("Schema detected", tone="success"),
            ),
            Grid(
                Metric("Rows", f"{inspection.row_count:,}"),
                Metric("Columns", f"{len(inspection.columns)}"),
                Metric("File size", _format_file_size(inspection.size_bytes)),
                columns=3,
                gap="sm",
            ),
            ScrollRegion(
                apply_data_recipe(
                    Table(
                        rows=[
                            [
                                html.strong(column.name),
                                Badge(column.inferred_type, tone="info", size="sm"),
                                (
                                    f"{column.populated / inspection.row_count:.0%}"
                                    if inspection.row_count
                                    else "—"
                                ),
                                html.code(column.example or "—"),
                            ]
                            for column in inspection.columns
                        ],
                        columns=[
                            TableColumn(header="Column"),
                            TableColumn(header="Inferred type"),
                            TableColumn(
                                header="Complete",
                                align="end",
                                numeric=True,
                                size="narrow",
                            ),
                            TableColumn(header="Example", size="wide"),
                        ],
                        density="compact",
                        sticky_header=True,
                        zebra=True,
                    )
                ),
                axis="block",
                size="md",
                label=f"Detected columns in {inspection.filename}",
            ),
            gap="md",
        )
    else:
        content = StateView(
            "No CSV scanned yet",
            kind="empty",
            description="Choose a UTF-8 CSV up to 5 MB to detect its columns and data types.",
        )

    return html.div(
        html.input(
            type="hidden",
            name="source_upload_id",
            value=upload.id if upload is not None else "",
            id="pipeline-source-upload-id",
        ),
        content,
        id="pipeline-csv-inspection",
        data=(
            {
                "csv-ready": "true",
                "csv-filename": inspection.filename,
                "csv-rows": str(inspection.row_count),
                "csv-columns": _csv_columns_json(inspection),
                "csv-size": _format_file_size(inspection.size_bytes),
                "csv-megabytes": f"{inspection.size_bytes / (1024 * 1024):.4f}",
            }
            if upload is not None and inspection is not None
            else {"csv-ready": "false"}
        ),
    )


def _csv_upload_control(request: Request) -> NodeLike:
    attrs = hx_attrs(
        request,
        path="/pipeline/csv/inspect",
        target="#pipeline-csv-inspection",
        trigger="change",
        include="#pipeline-form [name='csrf_token']",
        indicator="#pipeline-csv-upload-state",
    )
    attrs["hx-encoding"] = "multipart/form-data"
    return AttrHost(
        FileUpload(
            name="csv_file",
            accept=".csv,text/csv",
            maximum_size=MAX_CSV_UPLOAD_BYTES,
            label="Choose CSV file",
            hint="UTF-8 CSV · scanned locally before use",
            status="Select a file to inspect its schema.",
            appearance="soft",
            density="comfortable",
        ),
        id="pipeline-csv-file",
        attrs={str(key): str(value) for key, value in attrs.items()},
    )


def _provider_node(
    *,
    kind: str,
    catalog: ProviderCatalog | None,
    detail: str,
    configured: bool,
    runtime: str = "",
):
    if catalog is None:
        mark = "—"
        label = "No connection"
        technology = "Setup required"
        region = "Connections"
        connection_label = "Configure a connection"
    else:
        mark = catalog.mark
        label = catalog.label
        technology = catalog.technology
        region = catalog.region
        connection_label = (
            "Upload required"
            if catalog.name == "csv"
            else "Stored credentials"
            if configured
            else "Setup required"
        )
    return ConnectorNode(
        label,
        leading=Avatar(
            mark or label,
            size="md",
            appearance="soft",
            shape="rounded",
        ),
        state="ready" if configured else "blocked",
        kind="source" if kind == "source" else "target",
        detail=f"{kind.title()} · {connection_label}",
        runtime=runtime or technology,
        children=DescriptionList(
            (
                "Object",
                html.span(detail, id=f"pipeline-{kind}-detail", title=detail),
            ),
            (
                "Region",
                html.span(
                    region or "Not specified",
                    id=f"pipeline-{kind}-region",
                    title=region or "Not specified",
                ),
            ),
            density="compact",
            layout="inline",
        ),
        id=f"pipeline-{kind}-node",
    )


def _saved_pipeline_data(pipeline: PipelineDefinition, *, run: bool = False) -> dict[str, str]:
    source_namespace, source_object, destination_namespace, destination_object = (
        _pipeline_form_locations(pipeline)
    )
    data = {
        "pipeline-load": "true",
        "pipeline-id": pipeline.id,
        "pipeline-name": pipeline.name,
        "pipeline-source": pipeline.source_provider,
        "pipeline-source-schema": source_namespace,
        "pipeline-source-table": source_object,
        "pipeline-target": pipeline.destination_provider,
        "pipeline-target-schema": destination_namespace,
        "pipeline-target-table": (
            CREATE_TABLE_VALUE if pipeline.destination_create else destination_object
        ),
        "pipeline-target-table-new": (destination_object if pipeline.destination_create else ""),
        "pipeline-mode": pipeline.write_mode,
    }
    if run:
        data["pipeline-run"] = "true"
    if pipeline.source_provider == "csv" and pipeline.source_upload is not None:
        inspection = inspection_from_upload(pipeline.source_upload)
        data.update(
            {
                "pipeline-source-upload-id": pipeline.source_upload.id,
                "pipeline-source-upload-name": inspection.filename,
                "pipeline-source-upload-rows": str(inspection.row_count),
                "pipeline-source-upload-size": _format_file_size(inspection.size_bytes),
                "pipeline-source-upload-megabytes": (
                    f"{inspection.size_bytes / (1024 * 1024):.4f}"
                ),
                "pipeline-source-upload-columns": _csv_columns_json(inspection),
            }
        )
    return data


def _saved_pipeline_cards(
    request: Request,
    pipelines: list[PipelineDefinition],
    connections: dict[str, dict[str, str | bool]],
    *,
    csrf_token: str,
    latest_runs: dict[str, object] | None = None,
):
    latest_runs = latest_runs or {}
    if not pipelines:
        return StateView(
            "No saved pipelines yet",
            kind="empty",
            description="Name this route and save it to make it reusable.",
        )
    cards = []
    for pipeline in pipelines:
        source_namespace, source_object, destination_namespace, destination_object = (
            _pipeline_form_locations(pipeline)
        )
        source_configured = pipeline.source_provider == "csv" or _connection_configured(
            connections[pipeline.source_provider]
        )
        target_configured = _connection_configured(connections[pipeline.destination_provider])
        connections_configured = source_configured and target_configured
        source_runnable = pipeline.source_provider == "csv" or _connection_runnable(
            connections[pipeline.source_provider]
        )
        target_runnable = _connection_runnable(
            connections[pipeline.destination_provider]
        ) and writer_enabled(pipeline.destination_provider)
        runnable = (
            connections_configured
            and source_runnable
            and target_runnable
            and route_allowed(pipeline.source_provider, pipeline.destination_provider)
        )
        latest = latest_runs.get(pipeline.id)
        state = getattr(latest, "status", "Saved" if runnable else "Connection required")
        if latest is None:
            state = "Saved" if runnable else "Connection required"
        state_key = str(state).lower()
        state_tone = "info"
        if not runnable:
            state_tone = "warning"
        elif state_key == "succeeded":
            state_tone = "success"
        elif state_key in {"failed", "failed_needs_reconciliation"}:
            state_tone = "danger"
        elif state_key == "cancelled":
            state_tone = "warning"
        cards.append(
            ResourceRow(
                pipeline.name,
                description=(
                    f"{source_object if pipeline.source_provider == 'csv' else f'{source_namespace}.{source_object}'} → "
                    f"{destination_namespace}.{destination_object} · Updated "
                    f"{pipeline.updated_at.strftime('%b %d, %H:%M')} · {_saved_run_summary(latest)}"
                ),
                meta=Inline(
                    Badge(str(state).replace("_", " ").title(), tone=state_tone),
                    html.small(
                        f"{_provider_label(pipeline.source_provider)} → "
                        f"{_provider_label(pipeline.destination_provider)}"
                    ),
                    gap="sm",
                ),
                actions=ActionGroup(
                    Button(
                        "Load",
                        type="button",
                        variant="secondary",
                        size="sm",
                        attrs={
                            **{
                                f"data-{key}": value
                                for key, value in _saved_pipeline_data(pipeline).items()
                            },
                            **hx_attrs(
                                request,
                                method="get",
                                path=f"/pipeline?pipeline_id={pipeline.id}",
                                target="#main-panel",
                                swap="outerHTML",
                                push_url=True,
                                select="#main-panel",
                            ),
                        },
                        disabled=not connections_configured,
                    ),
                    html.form(
                        html.input(type="hidden", name="pipeline_id", value=pipeline.id),
                        html.input(type="hidden", name="csrf_token", value=csrf_token),
                        Button(
                            "Run now",
                            type="submit",
                            variant="secondary",
                            size="sm",
                            attrs={
                                **{
                                    f"data-{key}": value
                                    for key, value in _saved_pipeline_data(
                                        pipeline, run=True
                                    ).items()
                                },
                                **hx_attrs(
                                    request,
                                    path="/pipeline/runs",
                                    method="post",
                                    target="#pipeline-run-monitor",
                                    swap="outerHTML",
                                    busy="region",
                                ),
                            },
                            disabled=not runnable,
                        ),
                        action=form_action(request, "/pipeline/runs"),
                        method="post",
                        id=f"pipeline-run-form-{pipeline.id}",
                    ),
                    gap="xs",
                    collapse="never",
                ),
            )
        )
    return ResourceList(
        *cards,
        label="Saved pipelines",
        density="comfortable",
        id="pipeline-run-history",
    )


def _pipeline_preview_fragment(
    *,
    request: Request,
    catalog_access: UserCatalog,
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
    csv_inspection: CsvInspection | None = None,
    csv_upload: PipelineUpload | None = None,
    connections: dict[str, dict[str, str | bool]],
):
    if source_provider != "csv":
        source_schema, source_table = _normalized_selection(
            catalog_access, source_provider, source_schema, source_table
        )
    if target_provider:
        target_schema, target_table = _normalized_selection(
            catalog_access,
            target_provider,
            target_schema,
            target_table,
            preserve_create=True,
        )
    source_catalog = (
        CSV_SOURCE_CATALOG
        if source_provider == "csv"
        else require_catalog_provider(source_provider)
    )
    target_catalog = None if not target_provider else require_catalog_provider(target_provider)
    csv_ready = source_provider == "csv" and csv_upload is not None and csv_inspection is not None
    source_runtime_ready = (source_provider == "csv" and csv_ready) or (
        source_provider != "csv" and _connection_runnable(connections[source_provider])
    )
    target_runtime_ready = target_catalog is not None and _connection_runnable(
        connections[target_provider]
    )
    availability_message = (
        "Set up at least one connection before building or running a pipeline."
        if target_catalog is None
        else "Upload and scan a CSV source."
        if source_provider == "csv" and not csv_ready
        else "CSV source is ready. Configure destination settings."
        if source_provider == "csv"
        else f"Validate the {source_catalog.label} connection before running."
        if not source_runtime_ready
        else f"Validate the {target_catalog.label} connection before running."
        if not target_runtime_ready
        else "Source and destination connections are ready."
    )
    source_object_name = source_table
    table_name = (
        _committed_new_table_name(destination_table_new) or destination_table_new or target_table
    )
    if target_table == CREATE_TABLE_VALUE:
        table_name = _committed_new_table_name(destination_table_new) or "new_table"
    field_count = len(csv_inspection.columns) if csv_ready and csv_inspection is not None else 14
    source_schema_options = (
        [_option("uploaded", "Upload a CSV to inspect its schema", selected=True, disabled=True)]
        if source_provider == "csv"
        else _schema_options(catalog_access, source_provider, preferred_schema=source_schema)
    )
    source_table_options = (
        [_option("", "Upload required", selected=True, disabled=True)]
        if source_provider == "csv"
        else _table_options(
            catalog_access, source_provider, source_schema, preferred_table=source_table
        )
    )
    target_schema_options = (
        [_option("", "No connection available", selected=True, disabled=True)]
        if target_catalog is None
        else _destination_namespace_options(
            catalog_access,
            target_catalog,
            target_provider,
            preferred_schema=target_schema,
        )
    )
    target_table_options = (
        [_option("", "No connection available", selected=True, disabled=True)]
        if target_catalog is None
        else _destination_object_options(
            catalog_access,
            target_catalog,
            target_provider,
            target_schema,
            preferred_table=table_name
            if target_table != CREATE_TABLE_VALUE
            else CREATE_TABLE_VALUE,
            additional_tables=_created_destination_tables([], target_provider, target_schema),
        )
    )
    upsert_keys = _upsert_keys(
        catalog_access,
        target_provider,
        target_schema,
        table_name if target_table != CREATE_TABLE_VALUE else CREATE_TABLE_VALUE,
    )
    return html.div(
        _select_fragment(
            "pipeline-source-schema-select", source_schema_options, name="source_schema"
        ),
        _select_fragment("pipeline-source-table-select", source_table_options, name="source_table"),
        _select_fragment(
            "pipeline-target-schema-select",
            target_schema_options,
            name="destination_schema",
            disabled=target_catalog is None,
        ),
        _select_fragment(
            "pipeline-target-table-select",
            target_table_options,
            name="destination_table",
            disabled=target_catalog is None,
        ),
        _write_mode_select(
            target_catalog,
            selected=write_mode,
            upsert_available=bool(upsert_keys),
            oob=True,
        ),
        _upsert_key_select(upsert_keys, selected=conflict_columns, oob=True),
        _dataset_creator(
            request,
            target_catalog,
            needs_dataset=bool(
                target_catalog and target_catalog.dataset_creation and not target_schema
            ),
            oob=True,
        ),
        html.p(
            source_schema + "." + source_object_name
            if source_provider != "csv"
            else (
                csv_inspection.filename
                if csv_ready and csv_inspection is not None
                else "Choose a CSV file"
            ),
            id="pipeline-source-detail",
            **{"hx-swap-oob": "outerHTML:#pipeline-source-detail"},
        ),
        html.p(
            target_schema + "." + table_name if target_provider else "Configure a connection",
            id="pipeline-target-detail",
            **{"hx-swap-oob": "outerHTML:#pipeline-target-detail"},
        ),
        html.span(
            f"{field_count} fields" if field_count else "Choose source",
            id="pipeline-field-map-label",
            class_="hedron-process-flow-description",
            **{"hx-swap-oob": "outerHTML:#pipeline-field-map-label"},
        ),
        html.div(
            Alert(
                availability_message,
                tone=("success" if source_runtime_ready and target_runtime_ready else "warning"),
            ),
            id="pipeline-availability-note",
            **{"hx-swap-oob": "outerHTML:#pipeline-availability-note"},
        ),
    )


def _pipeline_body(
    request: Request,
    catalog_access: UserCatalog,
    connections: dict[str, dict[str, str | bool]],
    pipelines: list[PipelineDefinition],
    *,
    loaded_pipeline: PipelineDefinition | None = None,
    loaded_source_upload: PipelineUpload | None = None,
    loaded_source_inspection: CsvInspection | None = None,
    csrf_token: str,
    notice: str = "",
    latest_runs: dict[str, object] | None = None,
    run_monitor: NodeLike = None,
    demo_mode: bool = True,
):
    source_catalogs = _configured_catalogs(connections, role="source")
    destination_catalogs = _configured_catalogs(connections, role="destination")
    catalogs = tuple(dict.fromkeys((*source_catalogs, *destination_catalogs)))
    ready_count = sum(1 for details in connections.values() if _connection_runnable(details))
    if source_catalogs and destination_catalogs:
        source_catalog = source_catalogs[0]
        target_catalog = next(
            (
                item
                for item in destination_catalogs
                if route_allowed(source_catalog.name, item.name)
            ),
            destination_catalogs[0],
        )
    elif destination_catalogs:
        source_catalog = CSV_SOURCE_CATALOG
        target_catalog = destination_catalogs[0]
    else:
        source_catalog = CSV_SOURCE_CATALOG
        target_catalog = None
    source_provider = source_catalog.name
    target_provider = target_catalog.name if target_catalog is not None else ""
    configured_providers = {catalog.name for catalog in catalogs}

    if loaded_pipeline is not None:
        if (
            loaded_pipeline.source_provider in configured_providers
            or loaded_pipeline.source_provider == "csv"
        ):
            source_provider = loaded_pipeline.source_provider
        if loaded_pipeline.destination_provider in configured_providers:
            target_provider = loaded_pipeline.destination_provider
    source_catalog = (
        CSV_SOURCE_CATALOG
        if source_provider == "csv"
        else require_catalog_provider(source_provider)
    )
    target_catalog = require_catalog_provider(target_provider) if target_provider else None
    field_count = (
        len(loaded_source_inspection.columns)
        if source_provider == "csv" and loaded_source_inspection is not None
        else 14
    )
    source_schema_name = (
        _first_namespace(catalog_access, source_provider)
        if source_provider != "csv"
        else "uploaded"
    )
    target_schema_name = (
        _first_namespace(catalog_access, target_provider) if target_provider else ""
    )

    loaded_locations = (
        _pipeline_form_locations(loaded_pipeline) if loaded_pipeline is not None else None
    )
    if loaded_locations is not None:
        source_schema_name, source_table_display, target_schema_name, target_table_name = (
            loaded_locations
        )
    else:
        source_table_display = ""
        target_table_name = ""
    pipeline_id = loaded_pipeline.id if loaded_pipeline is not None else ""
    pipeline_name = loaded_pipeline.name if loaded_pipeline is not None else "Daily readiness sync"
    requested_write_mode = loaded_pipeline.write_mode if loaded_pipeline is not None else ""
    new_target_table_name = (
        loaded_pipeline.destination_table
        if loaded_pipeline is not None and loaded_pipeline.destination_create
        else ""
    )
    if loaded_pipeline is not None and loaded_pipeline.destination_create:
        target_table_name = CREATE_TABLE_VALUE
    if loaded_pipeline is not None and loaded_pipeline.source_provider == "csv":
        source_schema_name = "uploaded"
        source_object_name = loaded_pipeline.source_table
        source_table_display = loaded_pipeline.source_table
        if loaded_pipeline.source_upload is not None and loaded_source_upload is None:
            loaded_source_upload = loaded_pipeline.source_upload
        if loaded_source_upload is not None and loaded_source_inspection is None:
            try:
                loaded_source_inspection = inspection_from_upload(loaded_source_upload)
            except ValueError:
                loaded_source_upload = None
                loaded_source_inspection = None

    if source_provider != "csv":
        source_schema_name, source_table_display = _normalized_selection(
            catalog_access, source_provider, source_schema_name, source_table_display
        )
    if target_provider:
        target_schema_name, target_table_name = _normalized_selection(
            catalog_access,
            target_provider,
            target_schema_name,
            target_table_name,
            preserve_create=target_table_name == CREATE_TABLE_VALUE,
        )
    source_object_name = source_table_display
    target_object_name = (
        target_table_name or _first_object(catalog_access, target_provider, target_schema_name)
        if target_provider
        else ""
    )
    upsert_keys = _upsert_keys(
        catalog_access,
        target_provider,
        target_schema_name,
        target_object_name if target_table_name != CREATE_TABLE_VALUE else CREATE_TABLE_VALUE,
    )
    saved_conflict_columns = ""
    if loaded_pipeline is not None:
        try:
            loaded_policy = parse_write_policy(json.loads(loaded_pipeline.write_policy_json))
        except (TypeError, ValueError, json.JSONDecodeError):
            loaded_policy = None
        if isinstance(loaded_policy, PostgresUpsertPolicy):
            saved_conflict_columns = ",".join(loaded_policy.conflict_columns)
    write_mode = _resolved_write_mode(
        target_catalog, requested_write_mode, upsert_available=bool(upsert_keys)
    )
    csv_source_ready = source_provider == "csv" and loaded_source_upload is not None
    source_runtime_ready = (
        source_provider != "csv" and _connection_runnable(connections[source_provider])
        if source_provider != "csv"
        else csv_source_ready
    )
    target_runtime_ready = target_catalog is not None and _connection_runnable(
        connections[target_provider]
    )
    initial_run_ready = source_runtime_ready and target_runtime_ready
    if target_catalog is None:
        availability_message = (
            "Set up at least one connection before building or running a pipeline."
        )
    elif source_provider == "csv":
        availability_message = (
            "Upload and scan a CSV source."
            if not csv_source_ready
            else "CSV source is ready. Configure destination settings."
        )
    elif not source_runtime_ready:
        availability_message = f"Validate the {source_catalog.label} connection before running."
    elif not target_runtime_ready:
        availability_message = f"Validate the {target_catalog.label} connection before running."
    else:
        availability_message = "Source and destination connections are ready."
    if initial_run_ready and not pipeline_id:
        availability_message = (
            "Source and destination are ready. Save this pipeline to enable runs."
        )
    connection_summary = ActionGroup(
        Badge(
            f"{ready_count}/{len(connections)} connections ready",
            tone="success" if connections and ready_count == len(connections) else "info",
        ),
        Badge(
            "Demo mode" if get_settings().is_demo_mode else "Real transfers",
            tone="warning" if get_settings().is_demo_mode else "success",
        ),
        align="end",
        gap="sm",
        collapse="never",
    )
    pipeline_run_attrs = hx_attrs(
        request,
        path="/pipeline/runs",
        method="post",
        target="#pipeline-run-monitor",
        swap="outerHTML",
        include="#pipeline-form",
        indicator=INDICATOR,
        busy="region",
    )
    setup_flow = ProcessFlow(
        FlowStep(
            "Connect",
            class_=PROCESS_FLOW_STEP_STYLE_CLASS,
            status=("complete" if connections and ready_count == len(connections) else "current"),
            description=(
                f"{ready_count} of {len(connections)} connections validated."
                if connections
                else "Add a source and destination connection."
            ),
            status_text=(
                "Ready"
                if connections and ready_count == len(connections)
                else "Needs attention"
                if connections
                else "Start here"
            ),
        ),
        FlowStep(
            "Configure",
            class_=PROCESS_FLOW_STEP_STYLE_CLASS,
            status="complete" if pipeline_id else "current",
            description="Choose the source, destination, and write policy.",
            status_text="Saved" if pipeline_id else "In progress",
        ),
        FlowStep(
            "Run",
            class_=PROCESS_FLOW_STEP_STYLE_CLASS,
            status="current" if pipeline_id and initial_run_ready else "pending",
            description=(
                "Start a transfer and follow each persisted worker event."
                if pipeline_id and initial_run_ready
                else "Save a ready route to enable transfers."
            ),
            status_text="Ready" if pipeline_id and initial_run_ready else "Next",
        ),
        label="Pipeline workflow",
        direction="horizontal",
        collapse="never",
        density="compact",
    )
    return [
        PageHeader(
            "Pipeline workspace",
            eyebrow="Data movement",
            description=(
                "Choose a route, run the transfer, then follow every stage from one focused "
                "workspace."
            ),
            actions=connection_summary,
            density="compact",
        ),
        setup_flow,
        alert_box(
            "Pipeline saved. You can load or run it any time." if notice == "saved" else "",
            kind="success",
        ),
        NavigationTabs(
            (
                "Route setup",
                surface_card(
                    html.form(
                        csrf_hidden(csrf_token),
                        html.input(
                            type="hidden", name="pipeline_id", value=pipeline_id, id="pipeline-id"
                        ),
                        _catalog_data(catalog_access, catalogs, pipelines),
                        PageHeader(
                            pipeline_name if pipeline_id else "Create a pipeline",
                            eyebrow="Current route" if pipeline_id else "New route",
                            description=(
                                f"{source_catalog.label} to "
                                f"{target_catalog.label if target_catalog is not None else 'destination not selected'}"
                            ),
                            level=2,
                            density="compact",
                            actions=ActionGroup(
                                Button(
                                    "Save pipeline",
                                    variant="secondary",
                                    size="sm",
                                    type="submit",
                                ),
                                Button(
                                    "Run transfer",
                                    type="button",
                                    variant="primary",
                                    size="md",
                                    attrs={
                                        "data-pipeline-start": "true",
                                        **pipeline_run_attrs,
                                        "aria-describedby": "pipeline-availability-note",
                                    },
                                    disabled=not initial_run_ready or not pipeline_id,
                                ),
                                gap="sm",
                                collapse="never",
                            ),
                        ),
                        html.div(
                            Alert(
                                availability_message,
                                tone="success" if initial_run_ready else "warning",
                            ),
                            id="pipeline-availability-note",
                        ),
                        FormGrid(
                            FormField(
                                name="pipeline_name",
                                label="Pipeline name",
                                id="pipeline-name",
                                required=True,
                                control=html.input(
                                    id="pipeline-name",
                                    name="pipeline_name",
                                    value=pipeline_name,
                                    maxlength="120",
                                    required=True,
                                ),
                            ),
                            FormField(
                                name="write_mode",
                                label="Write mode",
                                id="pipeline-mode-select",
                                control=_write_mode_select(
                                    target_catalog,
                                    selected=write_mode,
                                    upsert_available=bool(upsert_keys),
                                ),
                            ),
                            FormField(
                                name="conflict_columns",
                                label="Upsert key",
                                id="pipeline-upsert-key-select",
                                control=_upsert_key_select(
                                    upsert_keys,
                                    selected=saved_conflict_columns,
                                ),
                            ),
                            columns={"base": 1, "lg": 3},
                            gap="md",
                        ),
                        Grid(
                            DATA_MOVER_DESIGN.apply(
                                "data-mover-inset",
                                Surface(
                                    PageHeader(
                                        "Source",
                                        eyebrow="Read from",
                                        description="Choose a connected object or scan a local CSV.",
                                        level=3,
                                        density="compact",
                                        meta=html.span(
                                            Badge(source_catalog.label, tone="success"),
                                            id="pipeline-source-provider-label",
                                        ),
                                    ),
                                    Stack(
                                        FormField(
                                            name="source_provider",
                                            label="Source type",
                                            id="pipeline-source-select",
                                            control=html.select(
                                                *_source_provider_options(
                                                    connections,
                                                    selected=source_provider,
                                                    target_provider=target_provider,
                                                ),
                                                id="pipeline-source-select",
                                                name="source_provider",
                                                data={"pipeline-control": "source-provider"},
                                                **hx_attrs(
                                                    request,
                                                    path="/pipeline/preview",
                                                    method="post",
                                                    target="#pipeline-preview-region",
                                                    swap="none",
                                                    include="#pipeline-form",
                                                    trigger="change",
                                                    select_oob="#pipeline-source-schema-select, #pipeline-source-table-select, #pipeline-target-schema-select, #pipeline-target-table-select, #pipeline-mode-select, #pipeline-upsert-key-select, #pipeline-source-detail, #pipeline-target-detail, #pipeline-field-map-label, #pipeline-availability-note",
                                                ),
                                            ),
                                        ),
                                        FormGrid(
                                            FormField(
                                                name="source_schema",
                                                label="Schema",
                                                id="pipeline-source-schema-select",
                                                control=html.select(
                                                    *(
                                                        _schema_options(
                                                            catalog_access,
                                                            source_provider,
                                                            preferred_schema=source_schema_name,
                                                        )
                                                        if source_provider != "csv"
                                                        else [
                                                            _option(
                                                                "uploaded",
                                                                "Detected from CSV",
                                                                selected=True,
                                                                disabled=True,
                                                            )
                                                        ]
                                                    ),
                                                    id="pipeline-source-schema-select",
                                                    name="source_schema",
                                                    data={"pipeline-control": "source-schema"},
                                                    **hx_attrs(
                                                        request,
                                                        path="/pipeline/preview",
                                                        method="post",
                                                        target="#pipeline-preview-region",
                                                        swap="none",
                                                        include="#pipeline-form",
                                                        trigger="change",
                                                        select_oob="#pipeline-source-schema-select, #pipeline-source-table-select, #pipeline-target-schema-select, #pipeline-target-table-select, #pipeline-mode-select, #pipeline-upsert-key-select, #pipeline-source-detail, #pipeline-target-detail, #pipeline-field-map-label, #pipeline-availability-note",
                                                    ),
                                                ),
                                            ),
                                            FormField(
                                                name="source_table",
                                                label="Table",
                                                id="pipeline-source-table-select",
                                                control=html.select(
                                                    *(
                                                        _table_options(
                                                            catalog_access,
                                                            source_provider,
                                                            source_schema_name,
                                                            preferred_table=source_table_display,
                                                        )
                                                        if source_provider != "csv"
                                                        else [
                                                            _option(
                                                                "",
                                                                "Upload required",
                                                                selected=True,
                                                                disabled=True,
                                                            )
                                                        ]
                                                    ),
                                                    id="pipeline-source-table-select",
                                                    name="source_table",
                                                    data={"pipeline-control": "source-table"},
                                                    **hx_attrs(
                                                        request,
                                                        path="/pipeline/preview",
                                                        method="post",
                                                        target="#pipeline-preview-region",
                                                        swap="none",
                                                        include="#pipeline-form",
                                                        trigger="change",
                                                        select_oob="#pipeline-source-schema-select, #pipeline-source-table-select, #pipeline-target-schema-select, #pipeline-target-table-select, #pipeline-mode-select, #pipeline-upsert-key-select, #pipeline-source-detail, #pipeline-target-detail, #pipeline-field-map-label, #pipeline-availability-note",
                                                    ),
                                                ),
                                            ),
                                            columns=2,
                                            gap="sm",
                                        ),
                                        Surface(
                                            PageHeader(
                                                "CSV alternative",
                                                eyebrow="Local source",
                                                description="Scan a UTF-8 CSV and use its detected schema.",
                                                level=4,
                                                density="compact",
                                                meta=html.span(
                                                    Badge("5 MB maximum", tone="neutral"),
                                                    id="pipeline-csv-upload-state",
                                                ),
                                            ),
                                            _csv_upload_control(request),
                                            _csv_inspection(
                                                loaded_source_upload
                                                if source_provider == "csv"
                                                else None,
                                                loaded_source_inspection
                                                if source_provider == "csv"
                                                else None,
                                            ),
                                            appearance="plain",
                                            padding="sm",
                                            elevation="none",
                                        ),
                                        gap="md",
                                    ),
                                ),
                            ),
                            DATA_MOVER_DESIGN.apply(
                                "data-mover-inset",
                                Surface(
                                    PageHeader(
                                        "Destination",
                                        eyebrow="Write to",
                                        description="Select a connected target and write policy.",
                                        level=3,
                                        density="compact",
                                        meta=html.span(
                                            Badge(
                                                target_catalog.label
                                                if target_catalog is not None
                                                else "Not selected",
                                                tone="info",
                                            ),
                                            id="pipeline-target-provider-label",
                                        ),
                                    ),
                                    Stack(
                                        FormField(
                                            name="destination_provider",
                                            label="Connection",
                                            id="pipeline-target-select",
                                            control=html.select(
                                                *(
                                                    _provider_options(
                                                        connections,
                                                        selected=target_provider,
                                                        role="destination",
                                                        counterpart=source_provider,
                                                    )
                                                    if target_catalog is not None
                                                    else [
                                                        _option(
                                                            "",
                                                            "Set up a connection first",
                                                            selected=True,
                                                            disabled=True,
                                                        )
                                                    ]
                                                ),
                                                id="pipeline-target-select",
                                                name="destination_provider",
                                                data={"pipeline-control": "target-provider"},
                                                disabled=target_catalog is None,
                                                **hx_attrs(
                                                    request,
                                                    path="/pipeline/preview",
                                                    method="post",
                                                    target="#pipeline-preview-region",
                                                    swap="none",
                                                    include="#pipeline-form",
                                                    trigger="change",
                                                    select_oob="#pipeline-source-schema-select, #pipeline-source-table-select, #pipeline-target-schema-select, #pipeline-target-table-select, #pipeline-mode-select, #pipeline-upsert-key-select, #pipeline-source-detail, #pipeline-target-detail, #pipeline-field-map-label, #pipeline-availability-note",
                                                ),
                                            ),
                                        ),
                                        _dataset_creator(
                                            request,
                                            target_catalog,
                                            needs_dataset=bool(
                                                target_catalog
                                                and target_catalog.dataset_creation
                                                and not target_schema_name
                                            ),
                                        ),
                                        FormGrid(
                                            FormField(
                                                name="destination_schema",
                                                label=(
                                                    target_catalog.namespaces_label
                                                    if target_catalog is not None
                                                    else "Schema"
                                                ),
                                                id="pipeline-target-schema-select",
                                                control=html.select(
                                                    *(
                                                        _destination_namespace_options(
                                                            catalog_access,
                                                            target_catalog,
                                                            target_provider,
                                                            preferred_schema=target_schema_name,
                                                        )
                                                        if target_catalog is not None
                                                        else [
                                                            _option(
                                                                "",
                                                                "No connection available",
                                                                selected=True,
                                                                disabled=True,
                                                            )
                                                        ]
                                                    ),
                                                    id="pipeline-target-schema-select",
                                                    name="destination_schema",
                                                    data={"pipeline-control": "target-schema"},
                                                    disabled=target_catalog is None,
                                                    **hx_attrs(
                                                        request,
                                                        path="/pipeline/preview",
                                                        method="post",
                                                        target="#pipeline-preview-region",
                                                        swap="none",
                                                        include="#pipeline-form",
                                                        trigger="change",
                                                        select_oob="#pipeline-source-schema-select, #pipeline-source-table-select, #pipeline-target-schema-select, #pipeline-target-table-select, #pipeline-mode-select, #pipeline-upsert-key-select, #pipeline-source-detail, #pipeline-target-detail, #pipeline-field-map-label, #pipeline-availability-note",
                                                    ),
                                                ),
                                            ),
                                            FormField(
                                                name="destination_table",
                                                label=(
                                                    target_catalog.objects_label
                                                    if target_catalog is not None
                                                    else "Table"
                                                ),
                                                id="pipeline-target-table-select",
                                                control=html.select(
                                                    *(
                                                        _destination_object_options(
                                                            catalog_access,
                                                            target_catalog,
                                                            target_provider,
                                                            target_schema_name,
                                                            preferred_table=target_table_name,
                                                            additional_tables=_created_destination_tables(
                                                                pipelines,
                                                                target_provider,
                                                                target_schema_name,
                                                            ),
                                                        )
                                                        if target_catalog is not None
                                                        else [
                                                            _option(
                                                                "",
                                                                "No connection available",
                                                                selected=True,
                                                                disabled=True,
                                                            )
                                                        ]
                                                    ),
                                                    id="pipeline-target-table-select",
                                                    name="destination_table",
                                                    data={"pipeline-control": "target-table"},
                                                    disabled=target_catalog is None,
                                                    **hx_attrs(
                                                        request,
                                                        path="/pipeline/preview",
                                                        method="post",
                                                        target="#pipeline-preview-region",
                                                        swap="none",
                                                        include="#pipeline-form",
                                                        trigger="change",
                                                    ),
                                                ),
                                            ),
                                            columns=2,
                                            gap="sm",
                                        ),
                                        html.div(
                                            FormField(
                                                name="destination_table_new",
                                                label=(
                                                    "New file name"
                                                    if target_catalog is not None
                                                    and target_catalog.dataset_creation
                                                    else "New table name"
                                                ),
                                                id="pipeline-target-table-new",
                                                help=(
                                                    "Used only when Create a new file is selected. Parquet is added automatically."
                                                    if target_catalog is not None
                                                    and target_catalog.dataset_creation
                                                    else "Used only when Create a new table is selected."
                                                ),
                                                control=html.input(
                                                    id="pipeline-target-table-new",
                                                    name="destination_table_new",
                                                    value=new_target_table_name,
                                                    maxlength="63",
                                                    placeholder=(
                                                        "readiness_export"
                                                        if target_catalog is not None
                                                        and target_catalog.dataset_creation
                                                        else "readiness_events_copy"
                                                    ),
                                                    pattern="[A-Za-z][A-Za-z0-9_]{1,62}",
                                                    disabled=target_table_name
                                                    != CREATE_TABLE_VALUE,
                                                ),
                                            ),
                                            class_="data-mover-new-destination-name",
                                            hidden=target_table_name != CREATE_TABLE_VALUE,
                                        ),
                                        gap="md",
                                    ),
                                ),
                            ),
                            columns={"base": 1, "xl": 2},
                            gap="md",
                        ),
                        ConnectorFlow(
                            _provider_node(
                                kind="source",
                                catalog=source_catalog,
                                detail=(
                                    f"{source_schema_name}.{source_object_name}"
                                    if source_provider != "csv"
                                    else "Choose a CSV file"
                                ),
                                configured=source_provider != "csv",
                                runtime=(
                                    str(connections[source_provider]["runtime"])
                                    if source_provider != "csv"
                                    else ""
                                ),
                            ),
                            ConnectorTrack(
                                Inline(
                                    Badge("Encrypted", tone="success"),
                                    html.span(
                                        f"{field_count} fields" if field_count else "Choose source",
                                        id="pipeline-field-map-label",
                                        class_="hedron-process-flow-description",
                                    ),
                                    gap="sm",
                                ),
                                Status(
                                    "Ready to transfer" if initial_run_ready else "Setup required",
                                    tone="success" if initial_run_ready else "warning",
                                    live=False,
                                    variant="compact",
                                ),
                                label="Encrypted transfer route",
                            ),
                            _provider_node(
                                kind="target",
                                catalog=target_catalog,
                                detail=(
                                    f"{target_schema_name}.{target_object_name}"
                                    if target_catalog is not None
                                    else "Configure a connection"
                                ),
                                configured=target_catalog is not None,
                                runtime=(
                                    str(connections[target_provider]["runtime"])
                                    if target_catalog is not None
                                    else ""
                                ),
                            ),
                            direction="horizontal",
                            collapse="lg",
                            appearance="soft",
                            background="dots",
                            overflow="auto",
                            min_size="sm",
                            id="pipeline-canvas",
                        ),
                        _pipeline_schema_preview_panel(
                            catalog_access=catalog_access,
                            source_provider=source_provider,
                            source_schema=source_schema_name,
                            source_object=source_object_name,
                            destination_provider=target_provider,
                            destination_schema=target_schema_name,
                            destination_object=target_object_name,
                            destination_create=target_table_name == CREATE_TABLE_VALUE,
                            csv_inspection=(
                                loaded_source_inspection if source_provider == "csv" else None
                            ),
                        ),
                        _capability_surface(source_catalog, target_catalog),
                        html.div(id="pipeline-preview-region", hidden=True),
                        action=form_action(request, "/pipeline/save"),
                        method="post",
                        id="pipeline-form",
                    ),
                    id="pipeline-builder",
                ),
            ),
            (
                "Live transfer",
                surface_card(
                    PageHeader(
                        "Live transfer",
                        eyebrow="Run activity",
                        description=(
                            "Follow each stage, counter, and persisted worker event as it happens."
                        ),
                        level=2,
                        density="compact",
                    ),
                    run_monitor
                    or html.div(
                        StateView(
                            "Ready for a live transfer",
                            kind="empty",
                            description=(
                                "Save a pipeline and choose Run transfer. Live progress and the "
                                "worker event feed will appear here."
                            ),
                        ),
                        id="pipeline-run-monitor",
                    ),
                ),
            ),
            (
                "Saved routes",
                surface_card(
                    PageHeader(
                        "Saved routes",
                        eyebrow="Reusable pipelines",
                        description="Load an existing route or start it immediately.",
                        level=2,
                        density="compact",
                        meta=Badge(f"{len(pipelines)} total", tone="neutral"),
                    ),
                    _saved_pipeline_cards(
                        request,
                        pipelines,
                        connections,
                        csrf_token=csrf_token,
                        latest_runs=latest_runs,
                    ),
                    Alert(
                        (
                            "Demo connectors stay on this host and never call external endpoints."
                            if demo_mode
                            else "The worker decrypts credentials only for the claimed run and records persisted facts."
                        ),
                        title="Safe to explore" if demo_mode else "Live transfers",
                        tone="warning" if demo_mode else "success",
                    ),
                ),
            ),
            active=("Route setup" if notice == "saved" or run_monitor is None else "Live transfer"),
            id="pipeline-workspace-tabs",
        ),
    ]


def register_pipeline_routes(app: Hedron, fragment_router: HedronRouter) -> None:
    @app.page(
        "/pipeline",
        fragment_regions=(MAIN_PANEL, SIDE_NAV),
        include_in_schema=False,
    )
    async def pipeline_page(
        request: Request,
        auth: Auth,
        db: DbSession,
        settings: SettingsDep,
        notice: NoticeQuery = "",
        pipeline_id: str = "",
        run_id: str = "",
    ) -> Response:
        request.state.hedron_authenticated = True
        connections = {
            provider.name: {
                "configured": secret is not None,
                "validation": secret.validation_status if secret is not None else "unconfigured",
                "runtime": secret.runtime_status if secret is not None else "",
            }
            for provider, secret in list_user_secrets(db, auth.user)
        }
        pipelines = list_pipelines(db, auth.user)
        latest_runs = latest_run_map(
            db, user=auth.user, pipeline_ids=[item.id for item in pipelines]
        )
        loaded_pipeline: PipelineDefinition | None = None
        loaded_source_upload: PipelineUpload | None = None
        loaded_source_inspection: CsvInspection | None = None
        if pipeline_id:
            pipeline = db.get(PipelineDefinition, pipeline_id)
            if pipeline is None or pipeline.user_id != auth.user.id:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND, detail="Pipeline not found"
                )
            loaded_pipeline = pipeline
            if loaded_pipeline.source_upload is not None:
                loaded_source_upload = loaded_pipeline.source_upload
                try:
                    loaded_source_inspection = inspection_from_upload(loaded_source_upload)
                except ValueError:
                    loaded_source_upload = None
                    loaded_source_inspection = None
        if run_id:
            try:
                displayed_run = owned_run(db, user=auth.user, run_id=run_id)
            except LookupError as exc:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
        elif loaded_pipeline is not None:
            displayed_run = latest_runs.get(loaded_pipeline.id)
        else:
            displayed_run = max(
                latest_runs.values(),
                key=lambda item: item.created_at,
                default=None,
            )
        return await render_authenticated_view(
            request,
            body=_pipeline_body(
                request,
                UserCatalog(db, settings, auth.user, request=request),
                connections,
                pipelines,
                csrf_token=auth.session.csrf_token,
                notice=notice,
                loaded_pipeline=loaded_pipeline,
                loaded_source_upload=loaded_source_upload,
                loaded_source_inspection=loaded_source_inspection,
                latest_runs=latest_runs,
                run_monitor=(
                    _run_status_fragment(
                        request,
                        db,
                        displayed_run,
                        csrf_token=auth.session.csrf_token,
                    )
                    if displayed_run is not None
                    else None
                ),
                demo_mode=settings.is_demo_mode,
            ),
            auth=auth,
            settings=settings,
            page_title="Pipeline",
            csrf_token=auth.session.csrf_token,
            push_path="/pipeline",
            headers={"Cache-Control": "no-store"},
        )

    @app.action(
        "/pipeline/csv/inspect",
        fragment_regions=(CSV_INSPECTION, CSV_UPLOAD_STATE, TOAST_HOST),
        include_in_schema=False,
    )
    async def pipeline_csv_inspect(
        request: Request,
        auth: Auth,
        db: DbSession,
        _csrf: RequireCsrf,
        csv_file: CsvUploadForm,
    ) -> Response:
        try:
            content = await csv_file.read(MAX_CSV_UPLOAD_BYTES + 1)
            upload, inspection = store_csv_upload(
                db,
                user=auth.user,
                filename=csv_file.filename or "",
                content_type=csv_file.content_type or "text/csv",
                content=content,
                request=request,
            )
        except ValueError as exc:
            return await interaction_response(
                request,
                ok_fragment(
                    _csv_inspection(error=str(exc)),
                    status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                    toast=str(exc),
                    toast_tone="danger",
                    oob=(
                        OobUpdate(
                            Badge("Scan failed", tone="danger"),
                            element_id="pipeline-csv-upload-state",
                            swap="outerHTML",
                        ),
                    ),
                    region_id=CSV_INSPECTION.id,
                ),
            )
        finally:
            await csv_file.close()
        return await interaction_response(
            request,
            ok_fragment(
                _csv_inspection(upload, inspection),
                toast=(
                    f"Scanned {inspection.filename}: {len(inspection.columns)} columns detected."
                ),
                oob=(
                    OobUpdate(
                        Badge("Scan complete", tone="success"),
                        element_id="pipeline-csv-upload-state",
                        swap="outerHTML",
                    ),
                ),
                region_id=CSV_INSPECTION.id,
            ),
        )

    @app.action(
        "/pipeline/preview",
        fragment_regions=(
            CSV_INSPECTION,
            CSV_UPLOAD_STATE,
            PIPELINE_CSV_FILE,
            PIPELINE_DATASET_CREATOR,
            PIPELINE_SOURCE_SCHEMA_SELECT,
            PIPELINE_SOURCE_TABLE_SELECT,
            PIPELINE_TARGET_SCHEMA_SELECT,
            PIPELINE_TARGET_TABLE_SELECT,
            PIPELINE_PREVIEW_REGION,
            PIPELINE_SOURCE_NODE,
            PIPELINE_SOURCE_PROVIDER_LABEL,
            PIPELINE_SCHEMA_PREVIEW,
            PIPELINE_TARGET_NODE,
            PIPELINE_TARGET_PROVIDER_LABEL,
            TOAST_HOST,
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
        destination_provider: PipelineProviderForm,
        destination_schema: PipelineSchemaForm,
        destination_table: PipelineTableForm,
        source_schema: PipelineOptionalTableForm = "",
        source_table: PipelineOptionalTableForm = "",
        destination_table_new: PipelineOptionalTableForm = "",
        source_upload_id: PipelineIdForm = "",
        write_mode: PipelineWriteModeForm = "replace",
        conflict_columns: PipelineConflictColumnsForm = "",
    ) -> Response:
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
        if source_provider != "csv" and not _connection_configured(connections[source_provider]):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="Configure and validate the selected source connection first.",
            )
        if destination_provider and (
            not _connection_configured(connections[destination_provider])
            or not writer_enabled(destination_provider)
        ):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="The selected destination is not ready for writes.",
            )
        if destination_provider and not route_allowed(source_provider, destination_provider):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="Select an approved source and destination route.",
            )
        catalog_access = UserCatalog(db, settings, auth.user, request=request)
        if source_provider != "csv":
            source_schema, source_table = _normalized_selection(
                catalog_access, source_provider, source_schema, source_table
            )
        if destination_provider:
            destination_schema, destination_table = _normalized_selection(
                catalog_access,
                destination_provider,
                destination_schema,
                destination_table,
                preserve_create=True,
            )
        preview_fragment = _pipeline_preview_fragment(
            request=request,
            catalog_access=catalog_access,
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
            csv_inspection=csv_inspection,
            csv_upload=csv_upload,
            connections=connections,
        )
        source_object = source_table
        destination_object = _committed_new_table_name(destination_table_new) or destination_table
        if destination_table == CREATE_TABLE_VALUE:
            destination_object = _committed_new_table_name(destination_table_new) or "new_table"
        schema_preview = _pipeline_schema_preview_panel(
            catalog_access=catalog_access,
            source_provider=source_provider,
            source_schema=source_schema,
            source_object=source_object,
            destination_provider=destination_provider,
            destination_schema=destination_schema,
            destination_object=destination_object,
            destination_create=destination_table == CREATE_TABLE_VALUE,
            csv_inspection=csv_inspection if csv_upload is not None else None,
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
        ) or (source_provider != "csv" and _connection_runnable(connections[source_provider]))
        target_ready = target_catalog is not None and _connection_runnable(
            connections[destination_provider]
        )
        source_detail = (
            csv_inspection.filename
            if source_provider == "csv" and source_ready and csv_inspection is not None
            else "Choose a CSV file"
            if source_provider == "csv"
            else f"{source_schema}.{source_table}"
        )
        target_object = _committed_new_table_name(destination_table_new) or destination_table
        if destination_table == CREATE_TABLE_VALUE:
            target_object = _committed_new_table_name(destination_table_new) or "new_table"
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
                _provider_node(
                    kind="source",
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
                _provider_node(
                    kind="target",
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
                        _csv_upload_control(request),
                        element_id="pipeline-csv-file",
                        swap="outerHTML",
                    ),
                    OobUpdate(
                        _csv_inspection(),
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

    @app.action(
        "/pipeline/foundry-datasets",
        fragment_regions=(
            PIPELINE_DATASET_CREATOR,
            PIPELINE_TARGET_SCHEMA_SELECT,
            PIPELINE_TARGET_TABLE_SELECT,
            TOAST_HOST,
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
            created = create_foundry_dataset(
                db,
                settings,
                user=auth.user,
                provider=destination_provider,
                parent_folder_rid=parent_folder_rid,
                name=dataset_name,
                request=request,
            )
        except (ConnectorError, ValueError) as exc:
            return await interaction_response(
                request,
                ok_fragment(
                    _dataset_creator(request, catalog, error=str(exc)),
                    status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                    region_id=PIPELINE_DATASET_CREATOR.id,
                ),
            )

        catalog_access = UserCatalog(db, settings, auth.user, request=request)
        namespace_select = html.select(
            *_schema_options(
                catalog_access,
                destination_provider,
                preferred_schema=created.dataset_rid,
            ),
            id="pipeline-target-schema-select",
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
            *_table_options(
                catalog_access,
                destination_provider,
                created.dataset_rid,
                allow_create=True,
                preferred_table=CREATE_TABLE_VALUE,
                create_label="file",
            ),
            id="pipeline-target-table-select",
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
        return await interaction_response(
            request,
            ok_fragment(
                _dataset_creator(request, catalog, created_name=created.name),
                oob=(
                    OobUpdate(
                        namespace_select,
                        element_id=PIPELINE_TARGET_SCHEMA_SELECT.id,
                        swap="outerHTML",
                    ),
                    OobUpdate(
                        file_select,
                        element_id=PIPELINE_TARGET_TABLE_SELECT.id,
                        swap="outerHTML",
                    ),
                ),
                toast=f'Dataset "{created.name}" was created.',
                status_code=status.HTTP_201_CREATED,
                region_id=PIPELINE_DATASET_CREATOR.id,
            ),
        )

    @app.action("/pipeline/save", include_in_schema=False)
    async def pipeline_save(
        request: Request,
        auth: Auth,
        db: DbSession,
        settings: SettingsDep,
        _csrf: RequireCsrf,
        pipeline_name: PipelineNameForm,
        source_provider: PipelineSourceProviderForm,
        source_schema: PipelineSchemaForm,
        source_table: PipelineTableForm,
        destination_provider: PipelineProviderForm,
        destination_schema: PipelineSchemaForm,
        destination_table: PipelineTableForm,
        write_mode: PipelineWriteModeForm,
        destination_table_new: PipelineOptionalTableForm = "",
        conflict_columns: PipelineConflictColumnsForm = "",
        source_upload_id: PipelineIdForm = "",
        pipeline_id: PipelineIdForm = "",
    ) -> Response:
        available_providers = {
            provider.name
            for provider, secret in list_user_secrets(db, auth.user)
            if secret is not None and secret.validation_status == "connected"
        }
        try:
            catalog_access = UserCatalog(db, settings, auth.user, request=request)
            source_branch = (
                catalog_access.default_branch(source_provider)
                if source_provider != "csv" and source_provider in available_providers
                else ""
            )
            destination_branch = (
                catalog_access.branch_for_namespace(destination_provider, destination_schema)
                if destination_provider in available_providers
                else ""
            )
            if (
                destination_provider == "postgres"
                and destination_provider in available_providers
                and write_mode == "upsert"
            ):
                if destination_table == CREATE_TABLE_VALUE:
                    raise ValueError("Create the PostgreSQL table before configuring an upsert.")
                destination_schema_details = catalog_access.inspect_object(
                    "postgres", postgres_table(destination_schema, destination_table)
                )
                eligible_keys = {
                    tuple(destination_schema_details.primary_key),
                    *map(tuple, destination_schema_details.unique_constraints),
                }
                eligible_keys.discard(())
                selected_key = tuple(
                    item.strip() for item in conflict_columns.split(",") if item.strip()
                )
                if not selected_key and destination_schema_details.primary_key:
                    selected_key = tuple(destination_schema_details.primary_key)
                if not selected_key or selected_key not in eligible_keys:
                    raise ValueError(
                        "Select a current primary or unique key for the PostgreSQL upsert."
                    )
                conflict_columns = ",".join(selected_key)
            saved_pipeline = save_pipeline(
                db,
                user=auth.user,
                name=pipeline_name,
                source_provider=source_provider,
                source_schema=source_schema,
                source_table=source_table,
                source_branch=source_branch,
                destination_provider=destination_provider,
                destination_schema=destination_schema,
                destination_table=destination_table,
                destination_branch=destination_branch,
                destination_table_new=destination_table_new,
                source_upload_id=source_upload_id,
                write_mode=write_mode,
                conflict_columns=conflict_columns,
                available_providers=available_providers,
                pipeline_id=pipeline_id,
                request=request,
            )
        except (ConnectorError, ValueError) as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
            ) from exc
        return RedirectResponse(
            redirect_path(
                request,
                f"/pipeline?notice=saved&pipeline_id={saved_pipeline.id}",
            ),
            status_code=status.HTTP_303_SEE_OTHER,
        )

    async def _start_pipeline_run(
        request: Request,
        auth: Auth,
        db: DbSession,
        settings: SettingsDep,
        _csrf: RequireCsrf,
        background_tasks: BackgroundTasks,
        pipeline_id: str,
        idempotency_token: PipelineIdForm = "",
    ) -> Response:
        pipeline = db.get(PipelineDefinition, pipeline_id)
        if pipeline is None or pipeline.user_id != auth.user.id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Pipeline not found")
        try:
            snapshot = snapshot_from_definition(pipeline)
            run = enqueue_run(
                db,
                user=auth.user,
                pipeline=pipeline,
                snapshot=snapshot,
                idempotency_token=idempotency_token or None,
                request=request,
            )
        except (ValueError, LookupError) as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
            ) from exc
        if settings.is_demo_mode and settings.app_env == "test":
            from app.worker import process_one

            process_one(db, settings, run_id=run.id)
            db.refresh(run)
        else:
            schedule_pipeline_run(
                background_tasks,
                settings,
                run.id,
                getattr(request.app.state, "pipeline_stop_event", None),
            )
        if is_htmx_request(request):
            run_events = events_after(db, run=run, after_sequence=0)
            action_state, action_trace = _run_action_metadata(run, run_events)
            response = await interaction_response(
                request,
                ok_fragment(
                    _run_status_fragment(
                        request,
                        db,
                        run,
                        csrf_token=auth.session.csrf_token,
                        events=run_events,
                    ),
                    status_code=status.HTTP_202_ACCEPTED,
                    action_state=action_state,
                    action_trace=action_trace,
                ),
            )
            return response
        response = RedirectResponse(
            redirect_path(request, f"/pipeline?notice=queued&run_id={run.id}"),
            status_code=status.HTTP_303_SEE_OTHER,
        )
        return response

    @app.action(
        "/pipeline/runs",
        fragment_regions=(PIPELINE_RUN_MONITOR, TOAST_HOST),
        include_in_schema=False,
    )
    async def pipeline_run_start(
        request: Request,
        auth: Auth,
        db: DbSession,
        settings: SettingsDep,
        background_tasks: BackgroundTasks,
        _csrf: RequireCsrf,
        pipeline_id: PipelineIdForm,
        idempotency_token: PipelineIdForm = "",
    ) -> Response:
        if not pipeline_id:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="pipeline_id is required to start a pipeline run",
            )
        return await _start_pipeline_run(
            request=request,
            auth=auth,
            db=db,
            settings=settings,
            background_tasks=background_tasks,
            _csrf=_csrf,
            pipeline_id=pipeline_id,
            idempotency_token=idempotency_token,
        )

    @fragment_router.view(
        "/pipeline/runs/{run_id}/status",
        fragment_regions=(PIPELINE_RUN_MONITOR,),
        include_in_schema=False,
    )
    async def pipeline_run_status(
        request: Request,
        auth: Auth,
        db: DbSession,
        run_id: str,
    ) -> Response | InteractionResult:
        request.state.hedron_authenticated = True
        try:
            run = owned_run(db, user=auth.user, run_id=run_id)
        except LookupError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
        if not is_htmx_request(request):
            return RedirectResponse(
                redirect_path(request, f"/pipeline?run_id={run.id}"),
                status_code=status.HTTP_303_SEE_OTHER,
            )
        run_events = events_after(db, run=run, after_sequence=0)
        action_state, action_trace = _run_action_metadata(run, run_events)
        return ok_fragment(
            _run_status_fragment(
                request,
                db,
                run,
                csrf_token=auth.session.csrf_token,
                events=run_events,
            ),
            **_run_status_toasts(run),
            action_state=action_state,
            action_trace=action_trace,
        )

    @app.action(
        "/pipeline/runs/{run_id}/cancel",
        fragment_regions=(PIPELINE_RUN_MONITOR, TOAST_HOST),
        include_in_schema=False,
    )
    async def pipeline_run_cancel(
        request: Request,
        auth: Auth,
        db: DbSession,
        _csrf: RequireCsrf,
        run_id: str,
    ) -> Response:
        try:
            run = request_cancel(db, user=auth.user, run_id=run_id)
        except LookupError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
        run_events = events_after(db, run=run, after_sequence=0)
        action_state, action_trace = _run_action_metadata(run, run_events)
        return await interaction_response(
            request,
            ok_fragment(
                _run_status_fragment(
                    request,
                    db,
                    run,
                    csrf_token=auth.session.csrf_token,
                    events=run_events,
                ),
                **_run_status_toasts(run),
                action_state=action_state,
                action_trace=action_trace,
            ),
        )

    @app.action(
        "/pipeline/runs/{run_id}/reconcile",
        fragment_regions=(PIPELINE_RUN_MONITOR, TOAST_HOST),
        include_in_schema=False,
    )
    async def pipeline_run_reconcile(
        request: Request,
        auth: Auth,
        db: DbSession,
        _csrf: RequireCsrf,
        run_id: str,
    ) -> Response:
        try:
            run = record_reconciliation_review(db, user=auth.user, run_id=run_id)
        except LookupError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
        run_events = events_after(db, run=run, after_sequence=0)
        action_state, action_trace = _run_action_metadata(run, run_events)
        return await interaction_response(
            request,
            ok_fragment(
                _run_status_fragment(
                    request,
                    db,
                    run,
                    csrf_token=auth.session.csrf_token,
                    events=run_events,
                ),
                toast="Reconciliation review recorded.",
                toast_tone="info",
                action_state=action_state,
                action_trace=action_trace,
            ),
        )


def _run_status_toasts(run):
    if run.status == "succeeded":
        return {"toast": "Transfer completed.", "toast_tone": "success"}
    if run.status in {"failed", "cancelled", "failed_needs_reconciliation"}:
        return {"toast": "Transfer ended.", "toast_tone": "warning"}
    return {}


def _run_action_phase(status: str) -> str:
    if status in {"queued", "validating", "extracting", "transforming", "loading", "verifying"}:
        return "pending"
    if status == "succeeded":
        return "success"
    if status == "cancelled":
        return "cancelled"
    if status == "failed_needs_reconciliation":
        return "conflict"
    if status == "failed":
        return "error"
    return "idle"


def _run_operation(run, *, revision: int | None = None) -> OperationIdentity:
    """Project a persisted run into Hedron's bounded operation identity."""
    attempt = max(int(run.attempt or 1) - 1, 0)
    return OperationIdentity(
        str(run.id),
        generation=attempt,
        target="#pipeline-run-monitor",
        correlation_id=run.pipeline_definition_id,
        attempt=attempt,
        revision=revision,
    )


def _run_action_state(
    run,
    *,
    progress: int | None = None,
    revision: int | None = None,
) -> ActionState:
    status = str(run.status or "idle").lower()
    phase = _run_action_phase(status)
    operation = _run_operation(run, revision=revision)
    message = (
        run.error_summary
        if phase in {"error", "conflict"}
        else _RUN_STAGE_COPY.get(status, (status.replace("_", " ").title(), ""))[0]
    )
    return ActionState(
        phase=phase,
        operation=operation,
        message=(message or None),
        retryable=bool(phase == "error" and run.retryable),
        progress=progress,
        revision=revision,
    )


def _run_action_trace(run, events, state: ActionState) -> ActionTrace:
    trace = ActionTrace()
    for event in events:
        trace = trace.append(
            "pending",
            operation=state.operation,
            facts={"stage": event.stage, "sequence": event.sequence, "message": event.message},
        )
    return trace.append(state.phase, operation=state.operation, facts={"status": run.status})


def _run_action_metadata(run, events) -> tuple[ActionState, ActionTrace]:
    revision = events[-1].sequence if events else None
    status = str(run.status or "idle").lower()
    state = _run_action_state(run, progress=_RUN_PROGRESS.get(status), revision=revision)
    return state, _run_action_trace(run, events, state)


_RUN_PROGRESS = {
    "queued": 4,
    "validating": 16,
    "extracting": 42,
    "loading": 72,
    "verifying": 92,
    "succeeded": 100,
}

_RUN_STAGE_INDEX = {
    "queued": 0,
    "validating": 0,
    "extracting": 1,
    "loading": 2,
    "verifying": 3,
}

_RUN_STAGE_COPY = {
    "queued": ("Queued", "Waiting for an available worker."),
    "validating": ("Validating", "Checking the route and connection handshakes."),
    "extracting": ("Extracting", "Reading source batches and counting rows."),
    "loading": ("Loading", "Writing batches to the destination."),
    "verifying": ("Verifying", "Comparing persisted results with the source."),
    "succeeded": ("Complete", "Transfer verified and ready for review."),
    "cancelled": ("Cancelled", "The transfer was stopped before completion."),
    "failed": ("Failed", "The worker stopped and recorded a failure."),
    "failed_needs_reconciliation": (
        "Needs review",
        "The worker stopped; reconcile the destination before retrying.",
    ),
}

_EVENT_STAGE_LABELS = {
    "queued": "Queue",
    "authenticate": "Validate",
    "inspect": "Extract",
    "transfer": "Load",
    "verify": "Verify",
    "cancelled": "Cancelled",
    "failed": "Failed",
    "reconcile": "Reconcile",
}


def _run_flow_statuses(run_status: str) -> tuple[str, str, str, str]:
    if run_status == "succeeded":
        return ("complete", "complete", "complete", "complete")
    current = _RUN_STAGE_INDEX.get(run_status, 0)
    failed = run_status in {"failed", "failed_needs_reconciliation", "cancelled"}
    return tuple(
        "complete"
        if index < current
        else "blocked"
        if failed and index == current
        else "current"
        if index == current
        else "pending"
        for index in range(4)
    )  # type: ignore[return-value]


def _run_flow_steps(flow_statuses: tuple[str, str, str, str]) -> tuple[FlowStep, ...]:
    labels = ("Validate", "Extract", "Load", "Verify")
    descriptions = (
        "Check credentials and route settings.",
        "Read source batches.",
        "Write destination batches.",
        "Confirm row counts and checksums.",
    )
    status_text = {
        "complete": "Complete",
        "current": "In progress",
        "blocked": "Stopped",
        "pending": "Waiting",
    }
    return tuple(
        FlowStep(
            label,
            class_=PROCESS_FLOW_STEP_STYLE_CLASS,
            status=step_status,
            description=description,
            status_text=status_text[step_status],
        )
        for label, description, step_status in zip(labels, descriptions, flow_statuses, strict=True)
    )  # type: ignore[return-value]


def _destination_count_metric(run) -> Metric:
    """Build a before/after destination count metric from persisted verification data."""

    try:
        verification = json.loads(run.verification_json or "{}")
    except (TypeError, ValueError):
        verification = {}
    before = verification.get("destination_rows_before")
    after = verification.get("destination_rows_after")
    delta = verification.get("destination_row_delta")
    if isinstance(before, int) and isinstance(after, int):
        delta = after - before if not isinstance(delta, int) else delta
        tone = "up" if delta > 0 else "down" if delta < 0 else "neutral"
        return Metric(
            "Destination table",
            f"{before:,} → {after:,} rows",
            delta=f"{delta:+,} rows",
            delta_tone=tone,
        )
    if isinstance(after, int):
        return Metric(
            "Destination table",
            f"{after:,} rows after run",
            delta="Before count unavailable",
            delta_tone="neutral",
        )
    return Metric(
        "Destination table",
        "Count unavailable",
        delta="Provider does not expose counts",
        delta_tone="neutral",
    )


def _run_manifest(value: str | None) -> dict[str, Any]:
    try:
        parsed = json.loads(value or "{}")
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _run_schema_surface(title: str, manifest: dict[str, Any], row_value: str):
    schema_value = manifest.get("schema")
    schema: dict[str, Any] = schema_value if isinstance(schema_value, dict) else {}
    columns_value = schema.get("columns")
    columns = columns_value if isinstance(columns_value, list) else []
    primary_key_value = schema.get("primary_key")
    primary_key = primary_key_value if isinstance(primary_key_value, list) else []
    schema_before = manifest.get("schema_before")
    metadata_value = manifest.get("metadata")
    metadata: dict[str, Any] = metadata_value if isinstance(metadata_value, dict) else {}
    row_metadata_value = metadata.get("rows")
    row_metadata: dict[str, Any] = (
        row_metadata_value if isinstance(row_metadata_value, dict) else {}
    )
    schema_metadata_value = metadata.get("schema")
    schema_metadata: dict[str, Any] = (
        schema_metadata_value if isinstance(schema_metadata_value, dict) else {}
    )
    snapshot_label = "Before + after" if isinstance(schema_before, dict) else "Captured"
    return Surface(
        PageHeader(
            title,
            eyebrow="Persisted schema",
            description="Schema and counts captured by the worker for this run.",
            level=3,
            density="compact",
            meta=ActionGroup(
                Badge(
                    snapshot_label, tone="success" if isinstance(schema_before, dict) else "info"
                ),
                Badge(
                    provenance_label(str(row_metadata.get("provenance") or "unavailable")),
                    tone="success"
                    if row_metadata.get("provenance") in {"exact", "captured"}
                    else "info",
                ),
                Badge(
                    provenance_label(str(schema_metadata.get("provenance") or "unavailable")),
                    tone="success" if schema_metadata.get("available") else "warning",
                ),
                gap="xs",
                collapse="never",
            ),
        ),
        Grid(
            Metric("Rows", row_value),
            Metric("Columns", f"{len(columns):,}"),
            Metric("Primary key", ", ".join(str(item) for item in primary_key) or "None"),
            columns={"base": 2, "lg": 3},
            gap="sm",
        ),
        _schema_columns_table(columns, f"Persisted columns in {title}"),
        appearance="plain",
        padding="sm",
        elevation="none",
    )


def _run_schema_results(run):
    source_manifest = _run_manifest(run.source_manifest_json)
    destination_manifest = _run_manifest(run.destination_manifest_json)
    verification = _run_manifest(run.verification_json)
    before = verification.get("destination_rows_before")
    after = verification.get("destination_rows_after")
    destination_rows = (
        f"{before:,} → {after:,}"
        if isinstance(before, int) and isinstance(after, int)
        else "Unavailable"
    )
    source_rows = source_manifest.get("rows", run.source_rows)
    differences = schema_diff(source_manifest, destination_manifest)
    return DATA_MOVER_DESIGN.apply(
        "data-mover-inset",
        Surface(
            PageHeader(
                "Run schema & row counts",
                eyebrow="After-run review",
                description="Compare the schema captured by the worker with the destination count change.",
                level=3,
                density="compact",
            ),
            Expander(
                "Source and destination manifests",
                Grid(
                    _run_schema_surface("Source", source_manifest, f"{int(source_rows):,}"),
                    _run_schema_surface("Destination", destination_manifest, destination_rows),
                    columns={"base": 1, "xl": 2},
                    gap="sm",
                ),
                open=False,
                enhance="native",
            ),
            _schema_diff_surface(differences),
            id="pipeline-run-schema-results",
            appearance="plain",
            padding="sm",
            elevation="none",
        ),
    )


def _schema_diff_surface(differences: list[dict[str, str]]):
    if not differences:
        return StateView(
            "Schema comparison unavailable",
            kind="empty",
            description="One or both run manifests did not include column metadata.",
        )
    status_labels = {
        "match": ("Match", "success"),
        "changed": ("Changed", "warning"),
        "missing_destination": ("Missing at destination", "danger"),
        "extra_destination": ("Extra at destination", "info"),
    }
    return Surface(
        PageHeader(
            "Schema comparison",
            eyebrow="Source versus destination",
            description="Review column names, types, and nullability captured for this run.",
            level=3,
            density="compact",
        ),
        ScrollRegion(
            Table(
                rows=[
                    [
                        Badge(
                            status_labels.get(row["status"], ("Review", "warning"))[0],
                            tone=status_labels.get(row["status"], ("Review", "warning"))[1],
                            size="sm",
                        ),
                        html.strong(row["name"]),
                        f"{row['source_type']} · {row['source_nullable']}",
                        f"{row['destination_type']} · {row['destination_nullable']}",
                    ]
                    for row in differences
                ],
                columns=[
                    TableColumn(header="Status"),
                    TableColumn(header="Column"),
                    TableColumn(header="Source"),
                    TableColumn(header="Destination"),
                ],
                density="compact",
                sticky_header=True,
                zebra=True,
            ),
            axis="block",
            size="sm",
            label="Schema comparison",
        ),
        appearance="plain",
        padding="sm",
        elevation="none",
    )


def _run_recovery_surface(request: Request, run, *, csrf_token: str):
    run_status = str(run.status or "")
    if run_status not in {"failed", "failed_needs_reconciliation"}:
        return None
    review_recorded = bool(_run_manifest(run.verification_json).get("reconciliation_reviewed_at"))
    retry_form = None
    if run_status == "failed" and run.retryable and run.pipeline_definition_id:
        retry_form = html.form(
            csrf_hidden(csrf_token),
            html.input(type="hidden", name="pipeline_id", value=run.pipeline_definition_id),
            Button(
                "Retry run",
                type="submit",
                variant="primary",
                size="sm",
                attrs=hx_attrs(
                    request,
                    path="/pipeline/runs",
                    method="post",
                    target="#pipeline-run-monitor",
                    swap="outerHTML",
                    indicator=INDICATOR,
                    busy="region",
                ),
            ),
            action=form_action(request, "/pipeline/runs"),
            method="post",
        )
    review_form = None
    if run_status == "failed_needs_reconciliation":
        review_form = html.form(
            csrf_hidden(csrf_token),
            Button(
                "Review recorded" if review_recorded else "Record reconciliation review",
                type="submit",
                variant="secondary",
                size="sm",
                disabled=review_recorded,
                attrs=hx_attrs(
                    request,
                    path=f"/pipeline/runs/{run.id}/reconcile",
                    method="post",
                    target="#pipeline-run-monitor",
                    swap="outerHTML",
                    indicator=INDICATOR,
                    busy="region",
                    confirm="Confirm that an operator reviewed the destination before any retry.",
                ),
            ),
            action=form_action(request, f"/pipeline/runs/{run.id}/reconcile"),
            method="post",
        )
    return DATA_MOVER_DESIGN.apply(
        "data-mover-inset",
        Surface(
            PageHeader(
                "Recovery guidance",
                eyebrow="Operator action required",
                description=(
                    "The transfer failed and can be retried safely."
                    if run_status == "failed" and run.retryable
                    else "Destination state may be uncertain. Inspect it before retrying."
                ),
                level=3,
                density="compact",
            ),
            Alert(
                run.error_summary or "The transfer ended without a recoverable summary.",
                title=run.error_code or "Transfer failure",
                tone="danger" if run_status == "failed" else "warning",
            ),
            ActionGroup(retry_form, review_form, gap="sm", collapse="never")
            if retry_form or review_form
            else None,
            appearance="plain",
            padding="sm",
            elevation="none",
        ),
    )


def _run_status_fragment(
    request: Request,
    db,
    run,
    csrf_token: str = "",
    events=None,
):
    lines = events if events is not None else events_after(db, run=run, after_sequence=0)
    next_sequence = lines[-1].sequence if lines else None
    monitor_active = run.status not in {
        "succeeded",
        "failed",
        "cancelled",
        "failed_needs_reconciliation",
    }
    run_status = (run.status or "idle").lower()
    run_badge_text = "Standing by"
    run_badge_tone = "info"
    if run_status == "succeeded":
        run_badge_text = "Succeeded"
    elif run_status in {
        "queued",
        "running",
        "validating",
        "extracting",
        "transforming",
        "loading",
        "verifying",
    }:
        run_badge_text = _RUN_STAGE_COPY.get(run_status, ("Running", ""))[0]
        run_badge_tone = "info"
    elif run_status == "cancelled":
        run_badge_text = "Cancelled"
        run_badge_tone = "warning"
    elif run_status in {"failed", "failed_needs_reconciliation"}:
        run_badge_text = "Failed"
        run_badge_tone = "danger"
    progress_value = _RUN_PROGRESS.get(run_status, 0)
    action_state = _run_action_state(
        run,
        progress=progress_value if not monitor_active else None,
        revision=next_sequence,
    )
    stage_label, stage_description = _RUN_STAGE_COPY.get(
        run_status,
        (run_status.replace("_", " ").title(), "Worker state persisted to the run log."),
    )
    flow_statuses = _run_flow_statuses(run_status)
    snapshot = parse_snapshot(run.definition_snapshot_json)
    source_label = _provider_label(snapshot.source_provider)
    target_label = _provider_label(snapshot.destination_provider)
    failed = run_status in {"failed", "failed_needs_reconciliation"}
    source_state = (
        "failed"
        if failed
        else "succeeded"
        if run_status in {"loading", "verifying", "succeeded"}
        else "running"
        if monitor_active
        else "ready"
    )
    target_state = (
        "failed"
        if failed
        else "succeeded"
        if run_status == "succeeded"
        else "running"
        if run_status in {"loading", "verifying"}
        else "ready"
    )
    hx_poll = hx_attrs(
        request,
        path=f"/pipeline/runs/{run.id}/status",
        method="get",
        target="#pipeline-run-monitor",
        swap="outerHTML",
        polling=1.5,
        indicator=INDICATOR,
        busy="region",
    )
    run_again_form = (
        html.form(
            csrf_hidden(csrf_token),
            html.input(
                type="hidden",
                name="pipeline_id",
                value=run.pipeline_definition_id or "",
            ),
            Button(
                "Retry run" if run_status == "failed" and run.retryable else "Run again",
                type="submit",
                variant="primary",
                size="sm",
                disabled=(
                    monitor_active
                    or not run.pipeline_definition_id
                    or run_status == "failed_needs_reconciliation"
                    or (run_status == "failed" and not run.retryable)
                ),
                attrs=hx_attrs(
                    request,
                    path="/pipeline/runs",
                    method="post",
                    target="#pipeline-run-monitor",
                    swap="outerHTML",
                    indicator=INDICATOR,
                    busy="region",
                ),
            ),
            action=form_action(request, "/pipeline/runs"),
            method="post",
            id=f"pipeline-run-again-form-{run.id}",
        )
        if run.pipeline_definition_id
        and (run_status not in {"failed", "failed_needs_reconciliation"} or run.retryable)
        else None
    )
    cancel_form = (
        html.form(
            csrf_hidden(csrf_token),
            Button(
                "Cancel run",
                type="submit",
                variant="danger",
                size="sm",
                attrs=hx_attrs(
                    request,
                    path=f"/pipeline/runs/{run.id}/cancel",
                    method="post",
                    target="#pipeline-run-monitor",
                    swap="outerHTML",
                    indicator=INDICATOR,
                    busy="region",
                    confirm="Cancel this transfer? The worker will stop at its next safe checkpoint.",
                ),
            ),
            action=form_action(request, f"/pipeline/runs/{run.id}/cancel"),
            method="post",
        )
        if monitor_active
        else None
    )
    status_bar = AsyncRegion(
        ActionGroup(
            DATA_MOVER_DESIGN.apply(
                "data-mover-operational-status",
                Status(
                    run_badge_text,
                    tone=run_badge_tone,
                    live=True,
                    variant="activity" if monitor_active else "compact",
                ),
            ),
            Badge(snapshot.name, tone="neutral"),
            cancel_form,
            run_again_form,
            align="between",
            gap="sm",
            collapse="never",
        ),
        state=action_state.phase,
        label=f"Pipeline run {run.id} status",
        mark="pipeline-run-status",
    )
    return html.div(
        status_bar,
        ConnectorFlow(
            ConnectorNode(
                source_label,
                leading=Avatar(source_label, size="md", appearance="soft", shape="rounded"),
                state=source_state,
                kind="source",
                detail="Source",
                runtime=_run_locator_label(snapshot.source),
            ),
            ConnectorTrack(
                Inline(
                    CircularProgress(
                        None if monitor_active else progress_value,
                        indeterminate=monitor_active,
                        label=f"Transfer {progress_value}% complete",
                    ),
                    Status(
                        f"{progress_value}% · {stage_label}",
                        tone=run_badge_tone,
                        live=False,
                        variant="activity" if monitor_active else "compact",
                    ),
                    gap="sm",
                ),
                Progress(
                    progress_value,
                    label=f"Transfer progress: {progress_value}%",
                ),
                active=monitor_active,
                label=(
                    f"Data moving from {source_label} to {target_label}"
                    if monitor_active
                    else f"Transfer from {source_label} to {target_label}"
                ),
            ),
            ConnectorNode(
                target_label,
                leading=Avatar(target_label, size="md", appearance="soft", shape="rounded"),
                state=target_state,
                kind="target",
                detail="Destination",
                runtime=_run_locator_label(snapshot.destination),
            ),
            direction="horizontal",
            collapse="never",
            appearance="soft",
            density="compact",
            background="dots",
            overflow="auto",
            min_size="sm",
        ),
        ProcessFlow(
            *_run_flow_steps(flow_statuses),
            label="Live transfer stages",
            direction="horizontal",
            collapse="never",
            density="compact",
        ),
        Alert(
            stage_description,
            title=f"{stage_label} stage",
            tone="danger" if failed else "success" if run_status == "succeeded" else "info",
        ),
        _run_recovery_surface(request, run, csrf_token=csrf_token),
        Grid(
            Metric(
                "Extracted",
                f"{run.source_rows:,} rows",
                delta=_format_file_size(run.source_bytes),
                delta_tone="up" if run.source_rows else "neutral",
            ),
            Metric(
                "Loaded",
                f"{run.loaded_rows:,} rows",
                delta=_format_file_size(run.loaded_bytes),
                delta_tone="up" if run.loaded_rows else "neutral",
            ),
            _destination_count_metric(run),
            Metric(
                "Worker stage",
                stage_label,
                delta=f"Attempt {run.attempt}",
            ),
            columns={"base": 2, "xl": 4},
            gap="sm",
        ),
        _run_schema_results(run)
        if run_status in {"succeeded", "failed", "cancelled", "failed_needs_reconciliation"}
        else None,
        Expander(
            "Live event feed",
            Status(
                "Listening for worker events" if monitor_active else "Persisted run history",
                tone="info" if monitor_active else run_badge_tone,
                live=False,
                variant="activity" if monitor_active else "compact",
            ),
            ScrollRegion(
                Timeline(
                    [
                        (
                            event.occurred_at.strftime("%H:%M:%S"),
                            _EVENT_STAGE_LABELS.get(event.stage, event.stage.title()),
                            event.message,
                        )
                        for event in lines
                    ],
                    label=f"Event feed for {snapshot.name}",
                ),
                id="pipeline-run-log",
                axis="block",
                size="md",
                label="Live event feed",
            ),
            open=monitor_active,
            enhance="native",
        ),
        id="pipeline-run-monitor",
        aria={"live": "polite"},
        **({} if not monitor_active else hx_poll),
    )
