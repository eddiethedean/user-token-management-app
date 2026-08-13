"""Authenticated data-movement demo workspace."""

from __future__ import annotations

import json

from fastapi import HTTPException, Request, status
from fastapi.responses import RedirectResponse
from hedron import Heading, Hedron, html
from starlette.responses import Response

from app.dependencies import Auth, DbSession, RequireCsrf, SettingsDep
from app.models import PipelineDefinition, PipelineUpload
from app.routing import redirect_path
from app.services.catalogs import (
    CREATE_TABLE_VALUE,
    CSV_SOURCE_CATALOG,
    PROVIDER_CATALOG_MAP,
    PROVIDER_CATALOGS,
    ProviderCatalog,
)
from app.services.csv_uploads import (
    MAX_CSV_UPLOAD_BYTES,
    CsvInspection,
    inspection_from_upload,
    store_csv_upload,
)
from app.services.pipelines import list_pipelines, save_pipeline
from app.services.secrets import list_user_secrets
from app.ui.forms import csrf_hidden
from app.ui.http import render_authenticated_view
from app.ui.interactions import interaction_response, ok_fragment
from app.ui.layout import alert_box, page_heading
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
from app.ui.regions import CSV_INSPECTION, MAIN_PANEL, SIDE_NAV, TOAST_HOST
from app.ui.urls import form_action, hx_attrs

PROVIDER_LABELS = {
    **{catalog.name: catalog.label for catalog in PROVIDER_CATALOGS},
    "csv": CSV_SOURCE_CATALOG.label,
}


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


def _metric(label: str, value: str, metric_id: str):
    return html.div(
        html.span(label),
        html.strong(value, id=metric_id),
        class_="run-metric",
    )


def _run_step(number: str, title: str, detail: str, stage: str):
    return html.li(
        html.span(number, class_="run-step-index", aria={"hidden": "true"}),
        html.div(html.strong(title), html.small(detail)),
        html.span("Waiting", class_="run-step-state"),
        class_="run-step",
        data={"pipeline-stage": stage},
    )


def _connection_configured(details: dict[str, str | bool]) -> bool:
    return details["configured"] is True and details["validation"] == "connected"


def _connection_runnable(details: dict[str, str | bool]) -> bool:
    return _connection_configured(details) and details["runtime"] != "sleeping"


def _configured_catalogs(
    connections: dict[str, dict[str, str | bool]],
) -> tuple[ProviderCatalog, ...]:
    return tuple(
        catalog
        for catalog in PROVIDER_CATALOGS
        if _connection_configured(connections[catalog.name])
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
            region=catalog.region,
        )
        for catalog in PROVIDER_CATALOGS
        if _connection_configured(connections[catalog.name])
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


def _schema_options(provider: str):
    return [
        _option(schema.name, schema.name, selected=index == 0)
        for index, schema in enumerate(PROVIDER_CATALOG_MAP[provider].schemas)
    ]


def _table_metrics(table_name: str) -> tuple[str, str, str]:
    known = {
        "readiness_events": ("128442", "1.84 GB", "1884"),
        "asset_inventory": ("84319", "920 MB", "920"),
        "mission_orders": ("21704", "386 MB", "386"),
    }
    if table_name in known:
        return known[table_name]
    seed = sum((index + 1) * ord(character) for index, character in enumerate(table_name))
    records = 18_000 + seed % 164_000
    megabytes = 240 + seed % 1_300
    size = f"{megabytes / 1024:.2f} GB" if megabytes >= 1024 else f"{megabytes} MB"
    return str(records), size, str(megabytes)


def _table_options(provider: str, schema_name: str, *, allow_create: bool = False):
    catalog = PROVIDER_CATALOG_MAP[provider]
    schema = next(item for item in catalog.schemas if item.name == schema_name)
    options = []
    for index, table_name in enumerate(schema.tables):
        records, size, megabytes = _table_metrics(table_name)
        options.append(
            _option(
                table_name,
                table_name,
                selected=index == 0,
                records=records,
                size=size,
                megabytes=megabytes,
            )
        )
    if allow_create:
        options.append(_option(CREATE_TABLE_VALUE, "＋ Create a new table…"))
    return options


def _catalog_data(catalogs: tuple[ProviderCatalog, ...]):
    return html.div(
        *[
            html.span(
                data={
                    "catalog-provider": catalog.name,
                    "catalog-schema": schema.name,
                    "catalog-table": table_name,
                    "records": _table_metrics(table_name)[0],
                    "size": _table_metrics(table_name)[1],
                    "megabytes": _table_metrics(table_name)[2],
                }
            )
            for catalog in catalogs
            for schema in catalog.schemas
            for table_name in schema.tables
        ],
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
        content = html.div(
            html.strong("CSV scan failed"),
            html.p(error),
            class_="csv-inspection-empty is-error",
            role="alert",
        )
    elif upload is not None and inspection is not None:
        content = html.div(
            html.div(
                html.div(
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
                html.span("Schema detected", class_="connection-health is-connected"),
                class_="csv-inspection-heading",
            ),
            html.div(
                html.table(
                    html.thead(
                        html.tr(
                            html.th("Column"),
                            html.th("Inferred type"),
                            html.th("Complete"),
                            html.th("Example"),
                        )
                    ),
                    html.tbody(
                        *[
                            html.tr(
                                html.td(column.name),
                                html.td(
                                    column.inferred_type,
                                    class_=f"csv-type csv-type-{column.inferred_type}",
                                ),
                                html.td(
                                    f"{column.populated / inspection.row_count:.0%}"
                                    if inspection.row_count
                                    else "—"
                                ),
                                html.td(column.example or "—", class_="csv-example"),
                            )
                            for column in inspection.columns
                        ]
                    ),
                ),
                class_="csv-schema-table-wrap",
            ),
            class_="csv-inspection-success",
        )
    else:
        content = html.div(
            html.span("⇧", aria={"hidden": "true"}),
            html.strong("No CSV scanned yet"),
            html.p("Choose a UTF-8 CSV up to 5 MB to detect its columns and data types."),
            class_="csv-inspection-empty",
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
            else "Cluster sleeping"
            if configured and catalog.supports_runtime_wake and runtime == "sleeping"
            else "Stored credentials"
            if configured
            else "Setup required"
        )
    connection_class = (
        "is-sleeping"
        if connection_label == "Cluster sleeping"
        else "is-connected"
        if configured
        else "is-demo"
    )
    return html.article(
        html.div(
            html.span(
                mark,
                class_=f"provider-logo provider-logo-{kind}",
                aria={"hidden": "true"},
            ),
            html.div(
                html.p(kind, class_="node-kicker"),
                html.h3(label, id=f"pipeline-{kind}-name"),
            ),
            class_="provider-node-heading",
        ),
        html.div(
            html.span(class_="connection-dot", aria={"hidden": "true"}),
            connection_label,
            class_=f"connection-state {connection_class}",
            id=f"pipeline-{kind}-connection",
        ),
        html.dl(
            html.div(html.dt("Object"), html.dd(detail, id=f"pipeline-{kind}-detail")),
            html.div(
                html.dt("Engine"),
                html.dd(technology, id=f"pipeline-{kind}-engine"),
            ),
            html.div(html.dt("Region"), html.dd(region, id=f"pipeline-{kind}-region")),
            class_="node-details",
        ),
        class_=f"provider-node provider-node-{kind}",
    )


def _saved_pipeline_data(pipeline: PipelineDefinition, *, run: bool = False) -> dict[str, str]:
    data = {
        "pipeline-load": "true",
        "pipeline-id": pipeline.id,
        "pipeline-name": pipeline.name,
        "pipeline-source": pipeline.source_provider,
        "pipeline-source-schema": pipeline.source_schema,
        "pipeline-source-table": pipeline.source_table,
        "pipeline-target": pipeline.destination_provider,
        "pipeline-target-schema": pipeline.destination_schema,
        "pipeline-target-table": (
            CREATE_TABLE_VALUE if pipeline.destination_create else pipeline.destination_table
        ),
        "pipeline-target-table-new": (
            pipeline.destination_table if pipeline.destination_create else ""
        ),
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
    pipelines: list[PipelineDefinition],
    connections: dict[str, dict[str, str | bool]],
):
    if not pipelines:
        return html.div(
            html.span("＋", aria={"hidden": "true"}),
            html.strong("No saved pipelines yet"),
            html.p("Name this route and save it to make it reusable."),
            class_="saved-pipeline-empty",
        )
    cards = []
    for pipeline in pipelines:
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
        state = (
            "Saved"
            if runnable
            else "Wake compute"
            if connections_configured
            else "Connection required"
        )
        cards.append(
            html.article(
                html.div(
                    html.strong(pipeline.name),
                    html.small(
                        f"{PROVIDER_LABELS[pipeline.source_provider]} → "
                        f"{PROVIDER_LABELS[pipeline.destination_provider]}"
                    ),
                ),
                html.span(
                    state,
                    class_=("history-state is-saved" if runnable else "history-state is-blocked"),
                ),
                html.p(
                    f"{pipeline.source_table if pipeline.source_provider == 'csv' else f'{pipeline.source_schema}.{pipeline.source_table}'} → "
                    f"{pipeline.destination_schema}.{pipeline.destination_table} · Updated "
                    f"{pipeline.updated_at.strftime('%b %d, %H:%M')}"
                ),
                html.div(
                    html.button(
                        "Load",
                        type="button",
                        class_="button button-quiet button-small",
                        data=_saved_pipeline_data(pipeline),
                        disabled=not connections_configured,
                        title=(
                            "Reconnect this pipeline's source and destination before loading it."
                            if not connections_configured
                            else None
                        ),
                    ),
                    html.button(
                        "Run now",
                        type="button",
                        class_="button button-secondary button-small",
                        data=_saved_pipeline_data(pipeline, run=True),
                        disabled=not runnable,
                        title=(
                            "Wake the selected compute before running this pipeline."
                            if connections_configured and not runnable
                            else "Reconnect this pipeline's source and destination before running it."
                            if not connections_configured
                            else None
                        ),
                    ),
                    class_="saved-pipeline-actions",
                ),
            )
        )
    return html.div(
        *cards,
        class_="run-history-list",
        id="pipeline-run-history",
    )


def _pipeline_body(
    request: Request,
    connections: dict[str, dict[str, str | bool]],
    pipelines: list[PipelineDefinition],
    *,
    csrf_token: str,
    notice: str = "",
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
    source_schema_name = source_catalog.schemas[0].name if source_catalog.schemas else "uploaded"
    target_schema_name = target_catalog.schemas[0].name if target_catalog is not None else ""
    source_runtime_ready = source_provider != "csv" and _connection_runnable(
        connections[source_provider]
    )
    target_runtime_ready = target_catalog is not None and _connection_runnable(
        connections[target_provider]
    )
    initial_run_ready = source_runtime_ready and target_runtime_ready
    initial_save_ready = source_provider != "csv" and target_catalog is not None
    if target_catalog is None:
        availability_message = (
            "Set up at least one connection before building or running a pipeline."
        )
    elif source_provider == "csv":
        availability_message = "Upload and scan a CSV source, or set up a second connection for remote-to-remote routes."
    elif not source_runtime_ready:
        availability_message = f"Wake {source_catalog.label} compute before running this route."
    elif not target_runtime_ready:
        availability_message = f"Wake {target_catalog.label} compute before running this route."
    else:
        availability_message = "Source and destination connections are ready."
    connection_summary = html.div(
        html.span(
            f"{ready_count}/{len(connections)} connections ready",
            class_="connection-summary",
        ),
        html.span("Simulation mode", class_="demo-badge"),
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
    return [
        page_heading(
            "Data movement workspace",
            "Build a transfer",
            "Choose two systems, shape the payload, and watch Data Mover move it end to end.",
            connection_summary,
        ),
        alert_box(
            "Pipeline saved. You can load or run it any time." if notice == "saved" else "",
            kind="success",
        ),
        html.section(
            html.form(
                csrf_hidden(csrf_token),
                html.input(type="hidden", name="pipeline_id", value="", id="pipeline-id"),
                _catalog_data(catalogs),
                html.div(
                    html.div(
                        html.p("01 / Configure", class_="section-number"),
                        Heading("Define the route", level=2),
                        html.p(
                            "This demo uses realistic timing and telemetry without contacting remote APIs."
                        ),
                    ),
                    html.div(
                        html.button(
                            "Save pipeline",
                            class_="button button-secondary button-small",
                            type="submit",
                            data={"pipeline-save": "true"},
                            disabled=not initial_save_ready,
                        ),
                        html.button(
                            html.span(class_="run-button-icon", aria={"hidden": "true"}),
                            html.span("Run transfer", class_="run-button-label"),
                            class_="button button-primary run-transfer-button",
                            type="button",
                            data={"pipeline-start": "true"},
                            disabled=not initial_run_ready,
                            aria={"describedby": "pipeline-availability-note"},
                        ),
                        class_="builder-actions",
                    ),
                    class_="pipeline-section-heading",
                ),
                html.p(
                    availability_message,
                    id="pipeline-availability-note",
                    class_=(
                        "pipeline-availability-note is-ready"
                        if initial_run_ready
                        else "pipeline-availability-note is-blocked"
                    ),
                    role="status",
                ),
                html.div(
                    html.div(
                        html.label("Pipeline name", for_="pipeline-name"),
                        html.input(
                            id="pipeline-name",
                            name="pipeline_name",
                            value="Daily readiness sync",
                            maxlength="120",
                            required=True,
                        ),
                    ),
                    html.div(
                        html.label("Write mode", for_="pipeline-mode-select"),
                        html.select(
                            _option("upsert", "Upsert on primary key", selected=True),
                            _option("append", "Append only"),
                            _option("replace", "Replace destination"),
                            id="pipeline-mode-select",
                            name="write_mode",
                        ),
                    ),
                    class_="route-meta-controls",
                ),
                html.div(
                    html.section(
                        html.div(
                            html.span("Source object", class_="object-picker-title"),
                            html.span("Connection or file", class_="object-picker-badge"),
                            html.p(
                                "Browse a connected catalog or upload a CSV and inspect its schema."
                            ),
                            class_="object-picker-heading",
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
                                ),
                            ),
                            html.div(
                                html.label("Schema", for_="pipeline-source-schema-select"),
                                html.select(
                                    *(
                                        _schema_options(source_provider)
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
                                ),
                                class_="source-remote-field",
                            ),
                            html.div(
                                html.label("Table", for_="pipeline-source-table-select"),
                                html.select(
                                    *(
                                        _table_options(source_provider, source_schema_name)
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
                                ),
                                class_="source-remote-field",
                            ),
                            html.div(
                                html.div(
                                    html.div(
                                        html.strong("Upload CSV source"),
                                        html.span(
                                            "UTF-8 · 5 MB maximum",
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
                                _csv_inspection(),
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
                            html.span("Destination object", class_="object-picker-title"),
                            html.span("Existing or new", class_="object-picker-badge is-accent"),
                            html.p(
                                "Choose an existing table or create one when this pipeline runs."
                            ),
                            class_="object-picker-heading",
                        ),
                        html.div(
                            html.div(
                                html.label("Connection", for_="pipeline-target-select"),
                                html.select(
                                    *(
                                        _provider_options(connections, selected=target_provider)
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
                                ),
                            ),
                            html.div(
                                html.label("Schema", for_="pipeline-target-schema-select"),
                                html.select(
                                    *(
                                        _schema_options(target_provider)
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
                                ),
                            ),
                            html.div(
                                html.label("Table", for_="pipeline-target-table-select"),
                                html.select(
                                    *(
                                        _table_options(
                                            target_provider,
                                            target_schema_name,
                                            allow_create=True,
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
                                ),
                            ),
                            html.div(
                                html.label("New table name", for_="pipeline-target-table-new"),
                                html.input(
                                    id="pipeline-target-table-new",
                                    name="destination_table_new",
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
                    class_="object-picker-grid",
                ),
                html.div(
                    _provider_node(
                        kind="source",
                        catalog=source_catalog,
                        detail=(
                            f"{source_schema_name}.{source_catalog.schemas[0].tables[0]}"
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
                    html.div(
                        html.div(
                            html.span(class_="transfer-packet packet-one"),
                            html.span(class_="transfer-packet packet-two"),
                            html.span(class_="transfer-packet packet-three"),
                            class_="transfer-track",
                            aria={"hidden": "true"},
                        ),
                        html.div(
                            html.span("Extract"),
                            html.span("Map 14 fields", id="pipeline-field-map-label"),
                            html.span("Load"),
                            class_="transfer-stages",
                        ),
                        html.p("TLS 1.3 · Encrypted in transit", class_="transfer-protocol"),
                        class_="transfer-link",
                    ),
                    _provider_node(
                        kind="target",
                        catalog=target_catalog,
                        detail=(
                            f"{target_schema_name}.{target_catalog.schemas[0].tables[0]}"
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
                    class_="pipeline-canvas",
                    id="pipeline-canvas",
                ),
                html.div(
                    html.div(
                        html.span("Transforms", class_="transform-label"),
                        html.span(
                            "Normalize timestamps",
                            class_="transform-tag",
                            id="pipeline-transform-time",
                        ),
                        html.span(
                            "Drop 3 empty fields",
                            class_="transform-tag",
                            id="pipeline-transform-null",
                        ),
                        html.span(
                            "Validate event_id",
                            class_="transform-tag",
                            id="pipeline-transform-key",
                        ),
                    ),
                    html.p(
                        "Credentials are decrypted only at the execution boundary.",
                        class_="key-boundary-note",
                    ),
                    class_="pipeline-transform-row",
                ),
                action=form_action(request, "/pipeline/save"),
                method="post",
                id="pipeline-form",
                class_="pipeline-form",
            ),
            class_="panel pipeline-builder",
            id="pipeline-builder",
        ),
        html.div(
            html.section(
                html.div(
                    html.div(
                        html.p("02 / Observe", class_="section-number"),
                        Heading("Live run", level=2),
                    ),
                    html.span("Ready", class_="run-status is-ready", id="pipeline-run-status"),
                    class_="pipeline-section-heading",
                ),
                html.div(
                    html.div(
                        html.span("Awaiting transfer", id="pipeline-progress-label"),
                        html.strong("0%", id="pipeline-progress-value"),
                        class_="progress-copy",
                    ),
                    html.div(
                        html.span(id="pipeline-progress-bar"),
                        class_="pipeline-progress-track",
                        role="progressbar",
                        aria={
                            "label": "Transfer progress",
                            "valuemin": "0",
                            "valuemax": "100",
                            "valuenow": "0",
                        },
                    ),
                    class_="progress-block",
                ),
                html.div(
                    _metric("Records", "—", "pipeline-records"),
                    _metric("Transferred", "—", "pipeline-bytes"),
                    _metric("Throughput", "—", "pipeline-throughput"),
                    _metric("Elapsed", "—", "pipeline-elapsed"),
                    class_="run-metrics",
                ),
                html.div(
                    html.div(
                        html.span("Live batch stream"),
                        html.small("0 batches", id="pipeline-batch-count"),
                        class_="batch-stream-heading",
                    ),
                    html.div(
                        *[html.span(data={"batch-bar": str(index)}) for index in range(18)],
                        id="pipeline-batch-stream",
                        class_="batch-stream-bars",
                        aria={"hidden": "true"},
                    ),
                    class_="batch-stream",
                ),
                html.ol(
                    _run_step(
                        "01", "Authenticate", "Validate source and destination access", "auth"
                    ),
                    _run_step("02", "Inspect", "Compare schemas and prepare mapping", "inspect"),
                    _run_step("03", "Transfer", "Stream encrypted record batches", "transfer"),
                    _run_step("04", "Verify", "Reconcile counts and checksums", "verify"),
                    class_="run-steps",
                ),
                html.div(
                    html.div(
                        html.span(class_="log-live-dot", aria={"hidden": "true"}),
                        html.span("Run log"),
                        html.code("dm_demo_8f21a"),
                        class_="run-log-heading",
                    ),
                    html.div(
                        html.p(
                            html.time("09:41:02"), html.span("READY"), " Pipeline is ready to run."
                        ),
                        id="pipeline-run-log",
                        class_="run-log-lines",
                        aria={"live": "polite"},
                    ),
                    class_="run-log",
                ),
                class_="panel run-monitor",
            ),
            html.aside(
                html.div(
                    html.p("Reusable routes", class_="section-number"),
                    Heading("Saved pipelines", level=2),
                ),
                _saved_pipeline_cards(pipelines, connections),
                html.div(
                    html.span("DEMO", aria={"hidden": "true"}),
                    html.p(
                        html.strong("Safe to explore"),
                        html.small(
                            "Runs use synthetic telemetry and never call external endpoints."
                        ),
                    ),
                    class_="sandbox-note",
                ),
                class_="panel run-history",
            ),
            class_="pipeline-observe-grid",
        ),
    ]


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
        return await render_authenticated_view(
            request,
            body=_pipeline_body(
                request,
                connections,
                list_pipelines(db, auth.user),
                csrf_token=auth.session.csrf_token,
                notice=notice,
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
        fragment_regions=(CSV_INSPECTION, TOAST_HOST),
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
                region_id=CSV_INSPECTION.id,
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
            save_pipeline(
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
            redirect_path(request, "/pipeline?notice=saved"),
            status_code=status.HTTP_303_SEE_OTHER,
        )
