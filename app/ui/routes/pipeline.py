"""Authenticated data-movement demo workspace."""

from __future__ import annotations

import json
from typing import Any

from fastapi import HTTPException, Request, status
from fastapi.responses import RedirectResponse
from hedron import (
    ActionGroup,
    Alert,
    Avatar,
    Badge,
    Button,
    CircularProgress,
    ConnectorFlow,
    ConnectorNode,
    ConnectorTrack,
    DescriptionList,
    FlowStep,
    Grid,
    Heading,
    Hedron,
    Inline,
    Metric,
    OobUpdate,
    PageHeader,
    ProcessFlow,
    Progress,
    ResourceList,
    ResourceRow,
    ScrollRegion,
    StateView,
    Status,
    Table,
    TableColumn,
    Timeline,
    html,
)
from hedron.htmx import is_htmx_request
from hedron_core import NodeLike
from starlette.background import BackgroundTask
from starlette.responses import Response

from app.config import get_settings
from app.connectors.locators import (
    FoundryDatasetFilesLocator,
    FoundryUploadLocator,
    PostgresTableLocator,
    parse_locator,
    parse_snapshot,
)
from app.connectors.registry import connector_for
from app.database import SessionLocal
from app.dependencies import Auth, DbSession, RequireCsrf, SettingsDep
from app.models import PipelineDefinition, PipelineUpload
from app.services.catalogs import (
    CREATE_TABLE_VALUE,
    CSV_SOURCE_CATALOG,
    ProviderCatalog,
    all_provider_catalogs,
    require_catalog_provider,
)
from app.services.csv_uploads import (
    MAX_CSV_UPLOAD_BYTES,
    CsvInspection,
    inspection_from_upload,
    store_csv_upload,
)
from app.services.pipeline_runs import (
    enqueue_run,
    events_after,
    latest_run_map,
    owned_run,
    request_cancel,
    snapshot_from_definition,
)
from app.services.pipelines import list_pipelines, save_pipeline
from app.services.secrets import list_user_secrets
from app.ui.design_system import DATA_MOVER_DESIGN, apply_data_recipe, surface_card
from app.ui.forms import csrf_hidden
from app.ui.http import render_authenticated_view
from app.ui.interactions import interaction_response, ok_fragment
from app.ui.layout import INDICATOR, alert_box
from app.ui.params import (
    CsvUploadForm,
    NoticeQuery,
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
    PIPELINE_PREVIEW_REGION,
    PIPELINE_RUN_MONITOR,
    PIPELINE_SOURCE_SCHEMA_SELECT,
    PIPELINE_SOURCE_TABLE_SELECT,
    PIPELINE_TARGET_SCHEMA_SELECT,
    PIPELINE_TARGET_TABLE_SELECT,
    SIDE_NAV,
    TOAST_HOST,
)
from app.ui.tabs import NavigationTabs
from app.ui.urls import form_action, hx_attrs, mounted_path


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


def _resolved_write_mode(catalog: ProviderCatalog | None, selected: str) -> str:
    supported = catalog.write_modes if catalog is not None else ()
    if selected in supported:
        return selected
    return supported[0] if supported else ""


def _write_mode_select(
    catalog: ProviderCatalog | None,
    *,
    selected: str,
    oob: bool = False,
) -> NodeLike:
    resolved = _resolved_write_mode(catalog, selected)
    supported = catalog.write_modes if catalog is not None else ()
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


def _connection_configured(details: dict[str, str | bool]) -> bool:
    return details["configured"] is True and details["validation"] == "connected"


def _connection_runnable(details: dict[str, str | bool]) -> bool:
    return _connection_configured(details)


def _configured_catalogs(
    connections: dict[str, dict[str, str | bool]],
) -> tuple[ProviderCatalog, ...]:
    return tuple(
        catalog
        for catalog in all_provider_catalogs()
        if catalog.name in connections and _connection_configured(connections[catalog.name])
    )


def _provider_options(connections: dict[str, dict[str, str | bool]], *, selected: str):
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
        if catalog.name in connections and _connection_configured(connections[catalog.name])
    ]


def _source_provider_options(connections: dict[str, dict[str, str | bool]], *, selected: str):
    return [
        *_provider_options(connections, selected=selected),
        _option(
            "csv",
            "CSV file · Upload from device",
            selected=selected == "csv",
            configured="false",
            validation="local",
            runtime="",
            technology=CSV_SOURCE_CATALOG.technology,
            region=CSV_SOURCE_CATALOG.region,
        ),
    ]


def _namespace_entries(provider: str) -> list[tuple[str, str]]:
    connector = connector_for(provider)
    return [(item.name, item.display_name) for item in connector.list_namespaces({})]


def _object_entries(provider: str, namespace: str) -> list[tuple[str, str]]:
    connector = connector_for(provider)
    page = connector.list_objects({}, namespace)
    return [(item.name, item.display_name) for item in page.items]


def _first_namespace(provider: str) -> str:
    entries = _namespace_entries(provider)
    return entries[0][0] if entries else ""


def _first_object(provider: str, namespace: str) -> str:
    if not namespace:
        return ""
    entries = _object_entries(provider, namespace)
    return entries[0][0] if entries else ""


def _schema_options(provider: str, preferred_schema: str = ""):
    entries = _namespace_entries(provider)
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
    provider: str,
    schema_name: str,
    *,
    allow_create: bool = False,
    additional_tables: tuple[str, ...] = (),
    preferred_table: str = "",
):
    entries = _object_entries(provider, schema_name) if schema_name else []
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
                "＋ Create a new table…",
                selected=preferred_table == CREATE_TABLE_VALUE,
            )
        )
    return options


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
    """Return catalog namespace/object values, preferring canonical v2 locators."""
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


def _catalog_data(catalogs: tuple[ProviderCatalog, ...], pipelines: list[PipelineDefinition]):
    nodes = []
    for catalog in catalogs:
        for namespace, _display in _namespace_entries(catalog.name):
            objects = [name for name, _label in _object_entries(catalog.name, namespace)] + list(
                _created_destination_tables(pipelines, catalog.name, namespace)
            )
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
        content = html.div(
            html.div(
                ActionGroup(
                    html.span("CSV", class_="csv-file-mark", aria={"hidden": "true"}),
                    html.div(
                        html.strong(inspection.filename),
                        html.span(
                            f"{inspection.row_count:,} rows · {len(inspection.columns)} columns · "
                            f"{_format_file_size(inspection.size_bytes)}"
                        ),
                    ),
                    class_="csv-inspection-file",
                ),
                Badge("Schema detected", tone="success"),
                class_="csv-inspection-heading",
            ),
            html.div(
                apply_data_recipe(
                    Table(
                        rows=[
                            [
                                column.name,
                                html.span(
                                    column.inferred_type,
                                    class_=f"csv-type csv-type-{column.inferred_type}",
                                ),
                                (
                                    f"{column.populated / inspection.row_count:.0%}"
                                    if inspection.row_count
                                    else "—"
                                ),
                                html.span(column.example or "—", class_="csv-example"),
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
                    )
                ),
                class_="csv-schema-table-wrap",
            ),
            class_="csv-inspection-success",
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
        class_="csv-inspection",
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
        target_runnable = _connection_runnable(connections[pipeline.destination_provider])
        runnable = connections_configured and source_runnable and target_runnable
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
                    f"{pipeline.updated_at.strftime('%b %d, %H:%M')}"
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
    source_provider: str,
    source_schema: str,
    source_table: str,
    target_provider: str,
    target_schema: str,
    target_table: str,
    destination_table_new: str = "",
    source_upload_id: str = "",
    write_mode: str = "",
    csv_inspection: CsvInspection | None = None,
    csv_upload: PipelineUpload | None = None,
    connections: dict[str, dict[str, str | bool]],
):
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
    source_object_name = (
        source_table if source_provider == "csv" else _first_object(source_provider, source_schema)
    )
    table_name = (
        _committed_new_table_name(destination_table_new) or destination_table_new or target_table
    )
    if target_table == CREATE_TABLE_VALUE:
        table_name = _committed_new_table_name(destination_table_new) or "new_table"
    field_count = len(csv_inspection.columns) if csv_ready and csv_inspection is not None else 14
    source_schema_options = (
        [_option("uploaded", "Upload a CSV to inspect its schema", selected=True, disabled=True)]
        if source_provider == "csv"
        else _schema_options(source_provider, preferred_schema=source_schema)
    )
    source_table_options = (
        [_option("", "Upload required", selected=True, disabled=True)]
        if source_provider == "csv"
        else _table_options(source_provider, source_schema, preferred_table=source_table)
    )
    target_schema_options = (
        [_option("", "No connection available", selected=True, disabled=True)]
        if target_catalog is None
        else _schema_options(target_provider, preferred_schema=target_schema)
    )
    target_table_options = (
        [_option("", "No connection available", selected=True, disabled=True)]
        if target_catalog is None
        else _table_options(
            target_provider,
            target_schema,
            preferred_table=table_name
            if target_table != CREATE_TABLE_VALUE
            else CREATE_TABLE_VALUE,
            allow_create=True,
            additional_tables=_created_destination_tables([], target_provider, target_schema),
        )
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
        _write_mode_select(target_catalog, selected=write_mode, oob=True),
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
    catalogs = _configured_catalogs(connections)
    ready_count = sum(1 for details in connections.values() if _connection_runnable(details))
    if len(catalogs) >= 2:
        source_catalog = catalogs[0]
        target_catalog: ProviderCatalog | None = catalogs[1]
    elif catalogs:
        source_catalog = CSV_SOURCE_CATALOG
        target_catalog = catalogs[0]
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
        _first_namespace(source_provider) if source_provider != "csv" else "uploaded"
    )
    target_schema_name = _first_namespace(target_provider) if target_provider else ""

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
    write_mode = _resolved_write_mode(target_catalog, requested_write_mode)
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

    source_object_name = (
        source_table_display
        if source_provider == "csv"
        else source_table_display or _first_object(source_provider, source_schema_name)
    )
    target_object_name = (
        target_table_name or _first_object(target_provider, target_schema_name)
        if target_provider
        else ""
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
        class_="heading-actions",
    )
    csv_upload_attrs = hx_attrs(
        request,
        path="/pipeline/csv/inspect",
        target="#pipeline-csv-inspection",
        trigger="change",
        include="#pipeline-form [name='csrf_token']",
        indicator="#pipeline-csv-upload-state",
    )
    csv_upload_attrs["hx-encoding"] = "multipart/form-data"
    pipeline_run_attrs = hx_attrs(
        request,
        path="/pipeline/runs",
        method="post",
        target="#pipeline-run-monitor",
        swap="outerHTML",
        include="#pipeline-form",
        indicator=INDICATOR,
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
            density="comfortable",
        ),
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
                        _catalog_data(catalogs, pipelines),
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
                                    class_="run-transfer-button",
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
                        html.div(
                            html.div(
                                html.label("Pipeline name", for_="pipeline-name"),
                                html.input(
                                    id="pipeline-name",
                                    name="pipeline_name",
                                    value=pipeline_name,
                                    maxlength="120",
                                    required=True,
                                ),
                            ),
                            html.div(
                                html.label("Write mode", for_="pipeline-mode-select"),
                                _write_mode_select(target_catalog, selected=write_mode),
                            ),
                            class_="route-meta-controls",
                        ),
                        Grid(
                            html.section(
                                html.div(
                                    Heading("Source", level=3),
                                    Badge(source_catalog.label, tone="success"),
                                ),
                                html.div(
                                    html.div(
                                        html.label("Source type", for_="pipeline-source-select"),
                                        html.select(
                                            *_source_provider_options(
                                                connections, selected=source_provider
                                            ),
                                            id="pipeline-source-select",
                                            name="source_provider",
                                            data={"pipeline-control": "source-provider"},
                                            **hx_attrs(
                                                request,
                                                path="/pipeline/preview",
                                                method="post",
                                                target="#pipeline-preview-region",
                                                swap="outerHTML",
                                                include="#pipeline-form",
                                                trigger="change",
                                                select_oob="#pipeline-source-schema-select, #pipeline-source-table-select, #pipeline-target-schema-select, #pipeline-target-table-select, #pipeline-source-detail, #pipeline-target-detail, #pipeline-field-map-label, #pipeline-availability-note",
                                            ),
                                        ),
                                    ),
                                    html.div(
                                        html.label("Schema", for_="pipeline-source-schema-select"),
                                        html.select(
                                            *(
                                                _schema_options(
                                                    source_provider,
                                                    preferred_schema=source_schema_name,
                                                )
                                                if source_provider != "csv"
                                                else [
                                                    _option(
                                                        "uploaded",
                                                        "Upload a CSV to inspect its schema",
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
                                                swap="outerHTML",
                                                include="#pipeline-form",
                                                trigger="change",
                                                select_oob="#pipeline-source-schema-select, #pipeline-source-table-select, #pipeline-target-schema-select, #pipeline-target-table-select, #pipeline-source-detail, #pipeline-target-detail, #pipeline-field-map-label, #pipeline-availability-note",
                                            ),
                                        ),
                                        class_="source-remote-field",
                                    ),
                                    html.div(
                                        html.label("Table", for_="pipeline-source-table-select"),
                                        html.select(
                                            *(
                                                _table_options(
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
                                                swap="outerHTML",
                                                include="#pipeline-form",
                                                trigger="change",
                                                select_oob="#pipeline-source-schema-select, #pipeline-source-table-select, #pipeline-target-schema-select, #pipeline-target-table-select, #pipeline-source-detail, #pipeline-target-detail, #pipeline-field-map-label, #pipeline-availability-note",
                                            ),
                                        ),
                                        class_="source-remote-field",
                                    ),
                                    html.div(
                                        html.div(
                                            html.div(
                                                html.strong("Upload CSV source"),
                                                html.span(
                                                    Badge("UTF-8 · 5 MB maximum", tone="neutral"),
                                                    id="pipeline-csv-upload-state",
                                                ),
                                            ),
                                            html.label(
                                                html.span("Choose CSV file"),
                                                html.input(
                                                    type="file",
                                                    name="csv_file",
                                                    accept=".csv,text/csv",
                                                    id="pipeline-csv-file",
                                                    **csv_upload_attrs,
                                                ),
                                                class_="csv-upload-button",
                                            ),
                                            class_="csv-upload-heading",
                                        ),
                                        _csv_inspection(
                                            loaded_source_upload
                                            if source_provider == "csv"
                                            else None,
                                            loaded_source_inspection
                                            if source_provider == "csv"
                                            else None,
                                        ),
                                        id="pipeline-csv-upload-panel",
                                        class_="csv-upload-panel",
                                        hidden=True,
                                    ),
                                    class_="object-picker-fields",
                                ),
                                class_="object-picker source-object-picker",
                            ),
                            html.section(
                                html.div(
                                    Heading("Destination", level=3),
                                    Badge(
                                        target_catalog.label
                                        if target_catalog is not None
                                        else "Not selected",
                                        tone="info",
                                    ),
                                ),
                                html.div(
                                    html.div(
                                        html.label("Connection", for_="pipeline-target-select"),
                                        html.select(
                                            *(
                                                _provider_options(
                                                    connections, selected=target_provider
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
                                                swap="outerHTML",
                                                include="#pipeline-form",
                                                trigger="change",
                                                select_oob="#pipeline-source-schema-select, #pipeline-source-table-select, #pipeline-target-schema-select, #pipeline-target-table-select, #pipeline-source-detail, #pipeline-target-detail, #pipeline-field-map-label, #pipeline-availability-note",
                                            ),
                                        ),
                                    ),
                                    html.div(
                                        html.label("Schema", for_="pipeline-target-schema-select"),
                                        html.select(
                                            *(
                                                _schema_options(
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
                                                swap="outerHTML",
                                                include="#pipeline-form",
                                                trigger="change",
                                                select_oob="#pipeline-source-schema-select, #pipeline-source-table-select, #pipeline-target-schema-select, #pipeline-target-table-select, #pipeline-source-detail, #pipeline-target-detail, #pipeline-field-map-label, #pipeline-availability-note",
                                            ),
                                        ),
                                    ),
                                    html.div(
                                        html.label("Table", for_="pipeline-target-table-select"),
                                        html.select(
                                            *(
                                                _table_options(
                                                    target_provider,
                                                    target_schema_name,
                                                    preferred_table=target_table_name,
                                                    allow_create=True,
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
                                                swap="outerHTML",
                                                include="#pipeline-form",
                                                trigger="change",
                                            ),
                                        ),
                                    ),
                                    html.div(
                                        html.label(
                                            "New table name", for_="pipeline-target-table-new"
                                        ),
                                        html.input(
                                            id="pipeline-target-table-new",
                                            name="destination_table_new",
                                            value=new_target_table_name,
                                            maxlength="63",
                                            placeholder="readiness_events_copy",
                                            pattern="[A-Za-z][A-Za-z0-9_]{1,62}",
                                        ),
                                        id="pipeline-new-table-field",
                                        class_="new-table-field",
                                        hidden=True,
                                    ),
                                    class_="object-picker-fields",
                                ),
                                class_="object-picker target-object-picker",
                            ),
                            columns=2,
                            gap="sm",
                            class_="object-picker-grid",
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
                            collapse="md",
                            appearance="soft",
                            background="dots",
                            overflow="auto",
                            min_size="sm",
                            id="pipeline-canvas",
                        ),
                        html.div(id="pipeline-preview-region", hidden=True),
                        action=form_action(request, "/pipeline/save"),
                        method="post",
                        id="pipeline-form",
                        class_="pipeline-form",
                    ),
                    class_="pipeline-builder",
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
                    class_="run-monitor",
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
                    class_="run-history",
                ),
            ),
            active=("Route setup" if notice == "saved" or run_monitor is None else "Live transfer"),
            id="pipeline-workspace-tabs",
        ),
    ]


def _process_demo_run() -> None:
    """Run one demo transfer after the response exposes its live monitor."""

    from app.worker import process_one

    with SessionLocal() as worker_db:
        process_one(worker_db, get_settings())


def register_pipeline_routes(app: Hedron) -> None:
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
                connections,
                pipelines,
                csrf_token=auth.session.csrf_token,
                notice=notice,
                loaded_pipeline=loaded_pipeline,
                loaded_source_upload=loaded_source_upload,
                loaded_source_inspection=loaded_source_inspection,
                latest_runs=latest_runs,
                run_monitor=(
                    _run_status_fragment(request, db, displayed_run)
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
            PIPELINE_SOURCE_SCHEMA_SELECT,
            PIPELINE_SOURCE_TABLE_SELECT,
            PIPELINE_TARGET_SCHEMA_SELECT,
            PIPELINE_TARGET_TABLE_SELECT,
            PIPELINE_PREVIEW_REGION,
            TOAST_HOST,
        ),
        include_in_schema=False,
    )
    async def pipeline_preview(
        request: Request,
        auth: Auth,
        db: DbSession,
        _csrf: RequireCsrf,
        source_provider: PipelineSourceProviderForm,
        source_schema: PipelineSchemaForm,
        source_table: PipelineTableForm,
        destination_provider: PipelineProviderForm,
        destination_schema: PipelineSchemaForm,
        destination_table: PipelineTableForm,
        destination_table_new: PipelineOptionalTableForm = "",
        source_upload_id: PipelineIdForm = "",
        write_mode: PipelineWriteModeForm = "replace",
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
        return await interaction_response(
            request,
            ok_fragment(
                _pipeline_preview_fragment(
                    source_provider=source_provider,
                    source_schema=source_schema,
                    source_table=source_table,
                    target_provider=destination_provider,
                    target_schema=destination_schema,
                    target_table=destination_table,
                    destination_table_new=destination_table_new,
                    source_upload_id=source_upload_id,
                    write_mode=write_mode,
                    csv_inspection=csv_inspection,
                    csv_upload=csv_upload,
                    connections={
                        provider.name: {
                            "configured": secret is not None,
                            "validation": secret.validation_status
                            if secret is not None
                            else "unconfigured",
                            "runtime": secret.runtime_status if secret is not None else "",
                        }
                        for provider, secret in list_user_secrets(db, auth.user)
                    },
                ),
            ),
        )

    @app.action("/pipeline/save", include_in_schema=False)
    async def pipeline_save(
        request: Request,
        auth: Auth,
        db: DbSession,
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
        source_upload_id: PipelineIdForm = "",
        pipeline_id: PipelineIdForm = "",
    ) -> Response:
        available_providers = {
            provider.name
            for provider, secret in list_user_secrets(db, auth.user)
            if secret is not None and secret.validation_status == "connected"
        }
        try:
            saved_pipeline = save_pipeline(
                db,
                user=auth.user,
                name=pipeline_name,
                source_provider=source_provider,
                source_schema=source_schema,
                source_table=source_table,
                destination_provider=destination_provider,
                destination_schema=destination_schema,
                destination_table=destination_table,
                destination_table_new=destination_table_new,
                source_upload_id=source_upload_id,
                write_mode=write_mode,
                available_providers=available_providers,
                pipeline_id=pipeline_id,
                request=request,
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
            ) from exc
        return RedirectResponse(
            mounted_path(
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

            process_one(db, settings)
            db.refresh(run)
        if is_htmx_request(request):
            response = await interaction_response(
                request,
                ok_fragment(
                    _run_status_fragment(request, db, run),
                    status_code=status.HTTP_202_ACCEPTED,
                ),
            )
            if settings.is_demo_mode and settings.app_env != "test":
                response.background = BackgroundTask(_process_demo_run)
            return response
        response = RedirectResponse(
            mounted_path(request, f"/pipeline?notice=queued&run_id={run.id}"),
            status_code=status.HTTP_303_SEE_OTHER,
        )
        if settings.is_demo_mode and settings.app_env != "test":
            response.background = BackgroundTask(_process_demo_run)
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
            _csrf=_csrf,
            pipeline_id=pipeline_id,
            idempotency_token=idempotency_token,
        )

    @app.page(
        "/pipeline/runs/{run_id}/status",
        fragment_regions=(PIPELINE_RUN_MONITOR,),
        include_in_schema=False,
    )
    async def pipeline_run_status(
        request: Request,
        auth: Auth,
        db: DbSession,
        run_id: str,
        after_sequence: int = 0,
    ) -> Response:
        try:
            run = owned_run(db, user=auth.user, run_id=run_id)
        except LookupError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
        return await interaction_response(
            request,
            ok_fragment(
                _run_status_fragment(request, db, run, after_sequence=after_sequence),
                **_run_status_toasts(run),
            ),
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
        return await interaction_response(
            request,
            ok_fragment(_run_status_fragment(request, db, run), **_run_status_toasts(run)),
        )


def _run_status_toasts(run):
    if run.status == "succeeded":
        return {"toast": "Transfer completed.", "toast_tone": "success"}
    if run.status in {"failed", "cancelled", "failed_needs_reconciliation"}:
        return {"toast": "Transfer ended.", "toast_tone": "warning"}
    return {}


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


def _run_status_fragment(request: Request, db, run, after_sequence: int = 0):
    lines = events_after(db, run=run, after_sequence=0)
    next_sequence = lines[-1].sequence if lines else after_sequence
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
        run_badge_text = "Running"
        run_badge_tone = "info"
    elif run_status == "cancelled":
        run_badge_text = "Cancelled"
        run_badge_tone = "warning"
    elif run_status in {"failed", "failed_needs_reconciliation"}:
        run_badge_text = "Failed"
        run_badge_tone = "danger"
    progress_value = _RUN_PROGRESS.get(run_status, 0)
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
        path=f"/pipeline/runs/{run.id}/status?after_sequence={next_sequence}",
        method="get",
        target="#pipeline-run-monitor",
        swap="outerHTML",
        polling=1.5,
        indicator=INDICATOR,
    )
    return html.div(
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
            align="between",
            gap="sm",
            collapse="never",
        ),
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
                        progress_value,
                        label=f"Transfer {progress_value}% complete",
                    ),
                    Status(
                        f"{progress_value}% · {run.stage.replace('_', ' ').title()}",
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
            collapse="md",
            appearance="soft",
            density="compact",
            background="dots",
            overflow="auto",
            min_size="sm",
        ),
        ProcessFlow(
            FlowStep("Validate", status=flow_statuses[0]),
            FlowStep("Extract", status=flow_statuses[1]),
            FlowStep("Load", status=flow_statuses[2]),
            FlowStep("Verify", status=flow_statuses[3]),
            label="Live transfer stages",
            direction="horizontal",
            collapse="sm",
            density="compact",
        ),
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
            Metric(
                "Worker stage",
                run.stage.replace("_", " ").title(),
                delta=f"Attempt {run.attempt}",
            ),
            columns={"base": 1, "sm": 3},
            gap="sm",
        ),
        Heading("Live event feed", level=3),
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
        id="pipeline-run-monitor",
        aria={"live": "polite"},
        **({} if not monitor_active else hx_poll),
    )
